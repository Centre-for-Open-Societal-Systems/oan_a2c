"""Resolution of the secrets used to sign and verify oan_a2c access tokens.

The signing secret is kept separate from `encryption_key` on purpose.
`encryption_key` is Frappe's Fernet key for data at rest (Password fieldtypes,
2FA secrets, integration credentials) and has to stay stable for the life of the
site: rotate it and every previously encrypted value becomes undecryptable. A
token-signing secret has the opposite requirement — rotating it is the response
to a suspected leak, so it must be cheap. One value serving both roles makes the
cheap action inherit the expensive action's cost, which in practice means the
rotation never happens.

Configuration (site_config.json):

    "jwt_secrets": {"v1": "<random>", "v2": "<random>"},
    "jwt_current_kid": "v2"

New tokens are signed with the key `jwt_current_kid` names; every key still
listed in `jwt_secrets` is accepted on verification. A zero-downtime rotation is
therefore: add the new kid, point `jwt_current_kid` at it, then drop the old kid
once the longest-lived access token (15 minutes) has expired.

When `jwt_secrets` is absent the map falls back to `{"v1": encryption_key}`.
That fallback is permanent rather than a migration shim — it means auth survives
either value going missing, and it lets this land with no config coordination.
"""

import os

import frappe
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.serialization import load_pem_private_key

FALLBACK_KID = "v1"

# Module-level latch so the fallback notice is logged once per worker rather than
# once per request — this runs on the hot path of every authenticated call.
_fallback_warned = False


class JWTKeyConfigurationError(Exception):
	"""The site has no usable JWT key material at all.

	Distinct from "this token names a kid we don't know": callers map the two to
	different responses so a server misconfiguration is not reported as a bad token.
	"""


def _key_map() -> dict[str, str]:
	"""Return kid -> secret for every key currently accepted on verification."""
	global _fallback_warned

	configured = frappe.conf.get("jwt_secrets")
	if isinstance(configured, dict):
		# Drop blank entries: a kid mapped to "" would verify nothing while still
		# making the config look populated.
		usable = {kid: secret for kid, secret in configured.items() if kid and secret}
		if usable:
			return usable

	fallback = frappe.conf.get("encryption_key")
	if fallback:
		if not _fallback_warned:
			_fallback_warned = True
			frappe.logger().warning(
				"jwt_secrets is not configured; signing access tokens with encryption_key. "
				"Set jwt_secrets and jwt_current_kid in site_config.json."
			)
		return {FALLBACK_KID: fallback}

	return {}


def get_signing_key() -> tuple[str, str]:
	"""Return the (kid, secret) that new access tokens must be signed with."""
	keys = _key_map()
	if not keys:
		raise JWTKeyConfigurationError("No JWT signing key configured (jwt_secrets or encryption_key)")

	kid = frappe.conf.get("jwt_current_kid") or FALLBACK_KID
	secret = keys.get(kid)
	if not secret:
		# Signing with some other key would mint tokens nobody asked for; failing
		# here keeps the misconfiguration loud and local to login.
		raise JWTKeyConfigurationError(f"jwt_current_kid '{kid}' has no matching entry in jwt_secrets")

	return kid, secret


def get_verification_key(kid: str | None) -> str | None:
	"""Return the secret for `kid`, or None when the kid is absent or unknown.

	Raises JWTKeyConfigurationError when the site has no key material at all.
	"""
	keys = _key_map()
	if not keys:
		raise JWTKeyConfigurationError("No JWT signing key configured (jwt_secrets or encryption_key)")

	if not kid:
		return None

	return keys.get(kid)


def _resolve_key_content(secret: str) -> str:
	"""Return PEM key content if secret is a file path or direct PEM string."""
	if not secret:
		return secret
	if "\n" not in secret and (secret.endswith(".pem") or secret.endswith(".key")):
		try:
			site_path = frappe.get_site_path(secret) if hasattr(frappe, "get_site_path") else None
			if site_path and os.path.isfile(site_path):
				with open(site_path, encoding="utf-8") as f:
					return f.read().strip()
		except Exception:
			pass
		if os.path.isfile(secret):
			with open(secret, encoding="utf-8") as f:
				return f.read().strip()
	return secret


def is_rsa_key(key_material: str) -> bool:
	"""Return True if the key material is a PEM-formatted RSA key."""
	return bool(key_material and ("-----BEGIN" in key_material))


_public_key_cache: dict[str, str] = {}


def get_public_key_pem(key_material: str) -> str:
	"""Extract RSA public key PEM from private key PEM, with caching."""
	key_material = _resolve_key_content(key_material)
	if key_material in _public_key_cache:
		return _public_key_cache[key_material]

	if "-----BEGIN PUBLIC KEY" in key_material:
		return key_material

	priv_key = load_pem_private_key(key_material.encode("utf-8"), password=None)
	pub_pem = (
		priv_key.public_key()
		.public_bytes(
			encoding=serialization.Encoding.PEM,
			format=serialization.PublicFormat.SubjectPublicKeyInfo,
		)
		.decode("utf-8")
	)
	_public_key_cache[key_material] = pub_pem
	return pub_pem


def get_signing_material() -> tuple[str, str, str]:
	"""Return (kid, signing_key, algorithm).

	TEMPORARY: Auto-detects RS256 vs HS256 to allow backward-compatibility with
	existing deployments using legacy HMAC keys. Once all environments are migrated
	to RS256 / Kong Gateway, HS256 fallback will be removed.
	"""
	kid, raw_secret = get_signing_key()
	secret = _resolve_key_content(raw_secret)
	if is_rsa_key(secret):
		return kid, secret, "RS256"

	# TEMPORARY FALLBACK: Legacy HMAC secret (will be removed when all deployments migrate to RS256)
	return kid, secret, "HS256"


def get_verification_material(kid: str | None) -> tuple[str, str] | None:
	"""Return (verification_key, expected_algorithm) strictly derived from server config.

	TEMPORARY: Auto-detects RS256 vs HS256 to allow backward-compatibility with
	existing deployments using legacy HMAC keys. Once all environments are migrated
	to RS256 / Kong Gateway, HS256 fallback will be removed.
	"""
	raw_secret = get_verification_key(kid)
	if not raw_secret:
		return None

	secret = _resolve_key_content(raw_secret)
	if is_rsa_key(secret):
		pub_key = get_public_key_pem(secret)
		return pub_key, "RS256"

	# TEMPORARY FALLBACK: Legacy HMAC secret (will be removed when all deployments migrate to RS256)
	return secret, "HS256"
