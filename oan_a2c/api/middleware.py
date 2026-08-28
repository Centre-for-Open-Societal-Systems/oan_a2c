import json

import frappe
import jwt
from werkzeug.exceptions import HTTPException
from werkzeug.wrappers import Response

from oan_a2c.api.jwt_keys import JWTKeyConfigurationError, get_verification_key


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


def validate_jwt_request(request=None):
	"""
	Middleware bound to Frappe's auth_hooks.
	Intercepts and validates JWTs for the oan_a2c API namespace.
	"""
	# frappe.local.request is the Werkzeug request object set per-thread.
	# Using frappe.request here would be ambiguous — frappe.local.request is explicit
	# and matches what test stubs patch directly.
	path = frappe.local.request.path

	# We only care about our own API boundary.
	# Let Frappe handle desk access and standard APIs normally.
	if not path.startswith("/api/method/oan_a2c."):
		return

	# Whitelisted endpoints that don't require JWT validation
	if path in [
		"/api/method/oan_a2c.api.auth.login",
		"/api/method/oan_a2c.api.auth.forgot_password",
		"/api/method/oan_a2c.api.auth.reset_password",
		"/api/method/oan_a2c.api.auth.set_initial_password",
		"/api/method/oan_a2c.api.auth.refresh",
		"/api/method/oan_a2c.api.auth.logout",
		"/api/method/oan_a2c.api.v1.webhook_consent_data.receive_consent_data",
		"/api/method/oan_a2c.api.v1.webhooks.lead_inbound",
		"/api/method/oan_a2c.api.v1.auth.register_user",
		"/api/method/oan_a2c.api.openapi.get_openapi_spec",
		"/api/method/oan_a2c.api.openapi.docs",
		"/api/method/oan_a2c.api.openapi.redoc",
	]:
		return

	auth_header = frappe.get_request_header("Authorization")
	if not auth_header or not auth_header.startswith("Bearer "):
		# Forcing a hard boundary: If you hit our namespace, you need a JWT.
		raise JWTUnauthorized("Missing Authorization Header")

	token = auth_header.split(" ")[1]

	try:
		# The kid selects which key verifies this token rather than being compared
		# to a constant. That is what makes rotation possible: tokens minted under
		# the previous key keep validating until they expire on their own.
		header = jwt.get_unverified_header(token)
		kid = header.get("kid") if header else None

		try:
			secret = get_verification_key(kid)
		except JWTKeyConfigurationError:
			# A misconfigured server, not a bad token — reported separately so the
			# two are distinguishable in logs. The NSPF mindset demands we fail
			# securely in either case.
			raise JWTUnauthorized("System encryption key missing")

		if not secret:
			raise JWTUnauthorized("Invalid or missing Key ID ('kid') in JWT header.")

		# Decode and validate cryptographically
		payload = jwt.decode(
			token,
			secret,
			algorithms=["HS256"],
			issuer="oan_a2c_identity_gateway",
			audience="oan_a2c_client",
		)

		# Verify the user is active/enabled (revocation check). Both flags come from
		# one row read — the must-change check below costs no extra query.
		user_name = payload.get("sub")
		user_state = (
			frappe.db.get_value("User", user_name, ["enabled", "a2c_must_change_password"], as_dict=True)
			if user_name
			else None
		)
		if not user_state or not user_state.enabled:
			raise JWTUnauthorized("User is disabled or does not exist")

		# An admin issued this user a temporary password after the token was minted.
		# Rejecting here is what makes "reissue a temporary password" also mean
		# "end their current session now" — the point of the action when the reason
		# for it is a suspected compromise.
		if user_state.a2c_must_change_password:
			raise JWTUnauthorized("Password change required")

		# Log the user context into the Python thread memory for Frappe's ORM RBAC
		# Save and restore form_dict as frappe.set_user() resets local.form_dict = _dict()
		temp_form_dict = getattr(frappe.local, "form_dict", None)
		# nosemgrep: frappe-setuser -- reviewed: user derived from a cryptographically verified JWT + enabled check
		frappe.set_user(user_name)
		if temp_form_dict is not None:
			frappe.local.form_dict = temp_form_dict

	except jwt.ExpiredSignatureError:
		raise JWTUnauthorized("Token has expired")
	except jwt.InvalidTokenError:
		raise JWTUnauthorized("Invalid token")
