import json

import frappe
import jwt
from werkzeug.exceptions import HTTPException
from werkzeug.wrappers import Response

from oan_a2c.api.jwt_keys import JWTKeyConfigurationError, get_verification_key, get_verification_material


class JWTUnauthorized(HTTPException):
	def __init__(self, message):
		super().__init__()
		self.message = message

	def get_response(self, environ=None):
		return Response(
			json.dumps({"error": "Unauthorized", "message": self.message}),
			status=401,
			mimetype="application/json",
		)


# namespace prefix -> config for that consumer.
_NAMESPACES: dict[str, dict] = {}


def register_namespace(prefix: str, exempt_paths: list[str] | None = None, revocation_check=None):
	"""Declare an API namespace as JWT-protected.

	prefix           e.g. "/api/v1" or "/api/method/oan_a2c."
	exempt_paths     full paths reachable without a token (login, refresh, webhooks)
	revocation_check optional callable(user) -> str | None; a returned string is
	                 the rejection reason. Lets a consumer invalidate live tokens
	                 on its own conditions without this app knowing the rule.
	"""
	if not (prefix.startswith("/api/") or prefix == "/api" or prefix.startswith("/v1")):
		raise ValueError(
			f"Namespace prefix {prefix!r} must start with '/api/' or '/v1/'. Matching is by "
			"request path, and a prefix that cannot appear in one would register a "
			"namespace that silently never matches."
		)

	if prefix in _NAMESPACES:
		_NAMESPACES[prefix]["exempt_paths"].update(exempt_paths or [])
		if revocation_check:
			_NAMESPACES[prefix]["revocation_check"] = revocation_check
	else:
		_NAMESPACES[prefix] = {
			"exempt_paths": set(exempt_paths or []),
			"revocation_check": revocation_check,
		}


def _match_namespace(path: str) -> dict | None:
	"""Return the config for the namespace owning `path`, or None."""
	best = None
	best_len = -1

	for prefix, config in _NAMESPACES.items():
		if path.startswith(prefix) and len(prefix) > best_len:
			best, best_len = config, len(prefix)

	return best


PUBLIC_EXEMPT_PATHS = {
	# Legacy RPC paths
	"/api/method/oan_a2c.api.auth.login",
	"/api/method/oan_a2c.api.auth.forgot_password",
	"/api/method/oan_a2c.api.auth.reset_password",
	"/api/method/oan_a2c.api.auth.set_initial_password",
	"/api/method/oan_a2c.api.auth.refresh",
	"/api/method/oan_a2c.api.auth.logout",
	"/api/method/oan_a2c.api.v1.webhook_consent_data.receive_consent_data",
	"/api/method/oan_a2c.api.v1.webhooks.lead_inbound",
	"/api/method/oan_a2c.api.v1.auth.register_user",
	# REST v1 paths
	"/v1/auth/login",
	"/v1/auth/register",
	"/v1/auth/token/refresh",
	"/v1/auth/logout",
	"/v1/auth/password/forgot",
	"/v1/auth/password/reset",
	"/v1/auth/password/initial",
	"/v1/webhooks/consent-data",
	"/v1/webhooks/leads",
	# REST v1 paths with /api prefix
	"/api/v1/auth/login",
	"/api/v1/auth/register",
	"/api/v1/auth/token/refresh",
	"/api/v1/auth/logout",
	"/api/v1/auth/password/forgot",
	"/api/v1/auth/password/reset",
	"/api/v1/auth/password/initial",
	"/api/v1/webhooks/consent-data",
	"/api/v1/webhooks/leads",
}


def validate_jwt_request(request=None):
	"""
	Middleware bound to Frappe's auth_hooks.
	Intercepts and validates JWTs for the oan_a2c API namespace.
	"""
	request = request or getattr(frappe.local, "request", None)
	if request is None:
		return

	path = request.path

	# 1. Match against registered namespaces if present
	config = _match_namespace(path)
	if config is not None:
		if path in config["exempt_paths"] or path.rstrip("/") in config["exempt_paths"]:
			return
	else:
		# Fallback to static check for backward compatibility
		is_a2c_boundary = (
			path.startswith("/api/method/oan_a2c.") or path.startswith("/v1/") or path.startswith("/api/v1/")
		)
		if not is_a2c_boundary:
			return

		if path in PUBLIC_EXEMPT_PATHS or path.rstrip("/") in PUBLIC_EXEMPT_PATHS:
			return

	auth_header = frappe.get_request_header("Authorization")
	if not auth_header or not auth_header.startswith("Bearer "):
		raise JWTUnauthorized("Missing Authorization Header")

	token = auth_header.split(" ")[1]

	try:
		header = jwt.get_unverified_header(token)
		kid = header.get("kid") if header else None

		try:
			# TEMPORARY: Auto-detects RS256 vs HS256 to allow backward-compatibility with
			# legacy HMAC deployments. Will be strictly RS256 once all environments migrate.
			material = get_verification_material(kid)
		except JWTKeyConfigurationError:
			raise JWTUnauthorized("System encryption key missing")

		if not material:
			raise JWTUnauthorized("Invalid or missing Key ID ('kid') in JWT header.")

		verif_key, expected_alg = material

		# Decode and validate cryptographically enforcing server-side algorithm
		payload = jwt.decode(
			token,
			verif_key,
			algorithms=[expected_alg],
			issuer="oan_a2c_identity_gateway",
			audience="oan_a2c_client",
		)

		user_name = payload.get("sub")
		user_state = (
			frappe.db.get_value("User", user_name, ["enabled", "a2c_must_change_password"], as_dict=True)
			if user_name
			else None
		)
		if not user_state or not user_state.enabled:
			raise JWTUnauthorized("User is disabled or does not exist")

		if user_state.a2c_must_change_password:
			raise JWTUnauthorized("Password change required")

		# Custom revocation check from namespace configuration if supplied
		if config and config.get("revocation_check"):
			reason = config["revocation_check"](user_name)
			if reason:
				raise JWTUnauthorized(reason)

		temp_form_dict = getattr(frappe.local, "form_dict", None)
		# nosemgrep: frappe-setuser -- reviewed: user derived from a cryptographically verified JWT + enabled check
		frappe.set_user(user_name)
		if temp_form_dict is not None:
			frappe.local.form_dict = temp_form_dict

		frappe.local.oan_a2c_claims = payload

	except jwt.ExpiredSignatureError:
		raise JWTUnauthorized("Token has expired")
	except jwt.InvalidTokenError:
		raise JWTUnauthorized("Invalid token")
