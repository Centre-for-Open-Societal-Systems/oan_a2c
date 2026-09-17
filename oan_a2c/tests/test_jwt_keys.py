"""Tests for JWT signing-key resolution, the encryption_key fallback, and rotation.

Its own module rather than an append to test_auth.py — see docs/merge-hygiene.md.

No fixtures are created here: the middleware only needs a `sub` naming an enabled
User, so these use Administrator and touch no rows at all.
"""

import datetime
import json
import unittest

import frappe
import jwt

from oan_a2c.api.jwt_keys import (
	FALLBACK_KID,
	JWTKeyConfigurationError,
	get_signing_key,
	get_signing_material,
	get_verification_key,
	get_verification_material,
)
from oan_a2c.api.middleware import JWTUnauthorized, validate_jwt_request
from oan_a2c.tests.request_context import RequestContextMixin

# Every conf key the resolver reads. Saved and restored wholesale so a test that
# clears encryption_key cannot leak that state into the rest of the suite.
CONF_KEYS = ("jwt_secrets", "jwt_current_kid", "encryption_key")

PROTECTED_PATH = "/api/method/oan_a2c.api.v1.get_leads"


class TestJWTKeyResolution(RequestContextMixin, unittest.TestCase):
	def setUp(self):
		super().setUp()
		self._saved_conf = {key: frappe.conf.get(key) for key in CONF_KEYS}

	def tearDown(self):
		for key, value in self._saved_conf.items():
			if value is None:
				frappe.conf.pop(key, None)
			else:
				frappe.conf[key] = value
		frappe.set_user("Administrator")
		super().tearDown()

	def _set_conf(self, **values):
		"""Replace the key config wholesale — absent kwargs mean absent from site_config."""
		for key in CONF_KEYS:
			frappe.conf.pop(key, None)
		for key, value in values.items():
			frappe.conf[key] = value

	def _token(self, secret, kid, sub="Administrator"):
		payload = {
			"sub": sub,
			"iss": "oan_a2c_identity_gateway",
			"aud": "oan_a2c_client",
			"exp": datetime.datetime.now(datetime.UTC) + datetime.timedelta(minutes=5),
		}
		return jwt.encode(payload, secret, algorithm="HS256", headers={"kid": kid})

	def _validate(self, token):
		frappe.local.request = frappe._dict({"path": PROTECTED_PATH})
		self._mock_headers["Authorization"] = f"Bearer {token}"
		return validate_jwt_request()

	# ------------------------------------------------------------------
	# Key resolution
	# ------------------------------------------------------------------

	def test_signing_key_uses_current_kid(self):
		self._set_conf(jwt_secrets={"v1": "old-secret", "v2": "new-secret"}, jwt_current_kid="v2")
		self.assertEqual(get_signing_key(), ("v2", "new-secret"))

	def test_signing_key_defaults_to_v1_without_current_kid(self):
		self._set_conf(jwt_secrets={"v1": "only-secret"})
		self.assertEqual(get_signing_key(), (FALLBACK_KID, "only-secret"))

	def test_jwt_secrets_takes_precedence_over_encryption_key(self):
		self._set_conf(jwt_secrets={"v1": "dedicated"}, encryption_key="legacy-key")
		self.assertEqual(get_signing_key(), (FALLBACK_KID, "dedicated"))

	def test_auth_survives_missing_encryption_key(self):
		"""The split's payoff: no encryption_key in site_config, login still signs."""
		self._set_conf(jwt_secrets={"v1": "dedicated"})
		kid, secret = get_signing_key()
		self.assertEqual((kid, secret), (FALLBACK_KID, "dedicated"))
		self.assertEqual(get_verification_key(kid), "dedicated")

	def test_falls_back_to_encryption_key(self):
		"""The other direction: a site never given jwt_secrets keeps working."""
		self._set_conf(encryption_key="legacy-key")
		self.assertEqual(get_signing_key(), (FALLBACK_KID, "legacy-key"))

	def test_blank_secret_entries_are_ignored(self):
		# A kid mapped to "" would otherwise look configured while verifying nothing.
		self._set_conf(jwt_secrets={"v1": ""}, encryption_key="legacy-key")
		self.assertEqual(get_signing_key(), (FALLBACK_KID, "legacy-key"))

	def test_current_kid_without_matching_secret_raises(self):
		self._set_conf(jwt_secrets={"v1": "a"}, jwt_current_kid="v9")
		with self.assertRaises(JWTKeyConfigurationError):
			get_signing_key()

	def test_no_key_material_raises(self):
		self._set_conf()
		with self.assertRaises(JWTKeyConfigurationError):
			get_signing_key()
		with self.assertRaises(JWTKeyConfigurationError):
			get_verification_key("v1")

	def test_verification_key_lookup(self):
		self._set_conf(jwt_secrets={"v1": "a", "v2": "b"}, jwt_current_kid="v2")
		self.assertEqual(get_verification_key("v1"), "a")
		self.assertEqual(get_verification_key("v2"), "b")
		self.assertIsNone(get_verification_key("v3"))
		self.assertIsNone(get_verification_key(None))

	# ------------------------------------------------------------------
	# Rotation, end to end through the middleware
	# ------------------------------------------------------------------

	def test_rotation_accepts_both_current_and_previous_kid(self):
		"""Rotating the signing key must not invalidate tokens already in flight."""
		self._set_conf(jwt_secrets={"v1": "old-secret", "v2": "new-secret"}, jwt_current_kid="v2")
		self.assertIsNone(self._validate(self._token("old-secret", "v1")))
		self.assertIsNone(self._validate(self._token("new-secret", "v2")))

	def test_retired_kid_is_rejected(self):
		"""Once the old kid is dropped from jwt_secrets, its tokens stop verifying."""
		self._set_conf(jwt_secrets={"v2": "new-secret"}, jwt_current_kid="v2")
		with self.assertRaises(JWTUnauthorized) as context:
			self._validate(self._token("old-secret", "v1"))
		self.assertIn("Invalid or missing Key ID", context.exception.message)

	def test_right_kid_wrong_secret_is_rejected(self):
		self._set_conf(jwt_secrets={"v2": "new-secret"}, jwt_current_kid="v2")
		with self.assertRaises(JWTUnauthorized) as context:
			self._validate(self._token("not-the-secret", "v2"))
		self.assertIn("Invalid token", context.exception.message)

	def test_missing_key_material_is_reported_as_configuration_error(self):
		# Distinguishable from a bad token, which is the reason JWTKeyConfigurationError
		# is a separate exception rather than a None return.
		self._set_conf()
		with self.assertRaises(JWTUnauthorized) as context:
			self._validate(self._token("whatever", "v1"))
		self.assertIn("System encryption key missing", context.exception.message)

	# ------------------------------------------------------------------
	# RS256 (Asymmetric RSA) Tests & Dual-Mode Validation
	# ------------------------------------------------------------------

	def test_rsa_signing_material_and_verification(self):
		"""An RSA private key signs with RS256 and validates with the derived public key."""
		from cryptography.hazmat.primitives import serialization
		from cryptography.hazmat.primitives.asymmetric import rsa

		key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
		priv_pem = key.private_bytes(
			encoding=serialization.Encoding.PEM,
			format=serialization.PrivateFormat.PKCS8,
			encryption_algorithm=serialization.NoEncryption(),
		).decode("utf-8")

		self._set_conf(jwt_secrets={"v2": priv_pem}, jwt_current_kid="v2")
		kid, _signing_key, alg = get_signing_material()
		self.assertEqual(kid, "v2")
		self.assertEqual(alg, "RS256")

		verif_key, expected_alg = get_verification_material("v2")
		self.assertEqual(expected_alg, "RS256")
		self.assertIn("-----BEGIN PUBLIC KEY-----", verif_key)

		# Mint an RS256 token and validate through middleware
		now = datetime.datetime.now(datetime.UTC)
		payload = {
			"sub": "Administrator",
			"iss": "oan_a2c_identity_gateway",
			"aud": "oan_a2c_client",
			"iat": now,
			"exp": now + datetime.timedelta(minutes=15),
			"roles": ["System Manager"],
			"user_type": "marketplace",
		}
		rsa_token = jwt.encode(payload, priv_pem, algorithm="RS256", headers={"kid": "v2"})
		self.assertIsNone(self._validate(rsa_token))

	def test_dual_mode_accepts_both_rsa_and_hmac(self):
		"""Dual-mode accepts both legacy HS256 and new RS256 tokens."""
		from cryptography.hazmat.primitives import serialization
		from cryptography.hazmat.primitives.asymmetric import rsa

		key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
		priv_pem = key.private_bytes(
			encoding=serialization.Encoding.PEM,
			format=serialization.PrivateFormat.PKCS8,
			encryption_algorithm=serialization.NoEncryption(),
		).decode("utf-8")

		self._set_conf(jwt_secrets={"v1": "legacy-hmac-secret", "v2": priv_pem}, jwt_current_kid="v2")

		# HS256 token for v1 validates
		hs256_token = self._token("legacy-hmac-secret", "v1")
		self.assertIsNone(self._validate(hs256_token))

		# RS256 token for v2 validates
		now = datetime.datetime.now(datetime.UTC)
		payload = {
			"sub": "Administrator",
			"iss": "oan_a2c_identity_gateway",
			"aud": "oan_a2c_client",
			"iat": now,
			"exp": now + datetime.timedelta(minutes=15),
			"roles": ["System Manager"],
			"user_type": "marketplace",
		}
		rsa_token = jwt.encode(payload, priv_pem, algorithm="RS256", headers={"kid": "v2"})
		self.assertIsNone(self._validate(rsa_token))

	def test_algorithm_confusion_attack_rejected(self):
		"""If an attacker signs a token using HS256 with the RSA public key for v2, it is rejected."""
		from cryptography.hazmat.primitives import serialization
		from cryptography.hazmat.primitives.asymmetric import rsa

		key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
		priv_pem = key.private_bytes(
			encoding=serialization.Encoding.PEM,
			format=serialization.PrivateFormat.PKCS8,
			encryption_algorithm=serialization.NoEncryption(),
		).decode("utf-8")
		pub_pem = (
			key.public_key()
			.public_bytes(
				encoding=serialization.Encoding.PEM, format=serialization.PublicFormat.SubjectPublicKeyInfo
			)
			.decode("utf-8")
		)

		self._set_conf(jwt_secrets={"v2": priv_pem}, jwt_current_kid="v2")

		now = datetime.datetime.now(datetime.UTC)
		# Forged token: client claims kid="v2" but tries to use HS256 algorithm with the public key
		import base64
		import hashlib
		import hmac

		def _b64url(data: bytes) -> str:
			return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")

		header = {"typ": "JWT", "alg": "HS256", "kid": "v2"}
		raw_payload = {
			"sub": "Administrator",
			"iss": "oan_a2c_identity_gateway",
			"aud": "oan_a2c_client",
			"iat": int(now.timestamp()),
			"exp": int((now + datetime.timedelta(minutes=15)).timestamp()),
			"roles": ["System Manager"],
			"user_type": "marketplace",
		}
		h_b64 = _b64url(json.dumps(header).encode("utf-8"))
		p_b64 = _b64url(json.dumps(raw_payload).encode("utf-8"))
		signing_input = f"{h_b64}.{p_b64}".encode("ascii")
		sig = hmac.new(pub_pem.encode("utf-8"), signing_input, hashlib.sha256).digest()
		forged_token = f"{h_b64}.{p_b64}.{_b64url(sig)}"

		with self.assertRaises(JWTUnauthorized) as context:
			self._validate(forged_token)
		self.assertIn("Invalid token", context.exception.message)
