import json

import frappe
import jwt
from werkzeug.exceptions import HTTPException
from werkzeug.wrappers import Response


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
		"/api/method/oan_a2c.api.auth.refresh",
		"/api/method/oan_a2c.api.auth.logout",
		"/api/method/oan_a2c.api.v1.webhook_consent_data.receive_consent_data",
		"/api/method/oan_a2c.api.v1.webhooks.lead_inbound",
		"/api/method/oan_a2c.api.v1.websub_subscriber.callback",
	]:
		return

	auth_header = frappe.get_request_header("Authorization")
	if not auth_header or not auth_header.startswith("Bearer "):
		# Forcing a hard boundary: If you hit our namespace, you need a JWT.
		raise JWTUnauthorized("Missing Authorization Header")

	token = auth_header.split(" ")[1]
	secret = frappe.conf.get("encryption_key")

	if not secret:
		# A server configuration error. The NSPF mindset demands we fail securely.
		raise JWTUnauthorized("System encryption key missing")

	try:
		# Verify Key ID (kid) in the JWT header
		header = jwt.get_unverified_header(token)
		if not header or header.get("kid") != "v1":
			raise JWTUnauthorized("Invalid or missing Key ID ('kid') in JWT header. Expected 'kid': 'v1'.")

		# Decode and validate cryptographically
		payload = jwt.decode(token, secret, algorithms=["HS256"])

		# Verify the user is active/enabled (revocation check)
		user_name = payload.get("sub")
		if not user_name or not frappe.db.get_value("User", user_name, "enabled"):
			raise JWTUnauthorized("User is disabled or does not exist")

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
