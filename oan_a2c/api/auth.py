import frappe
from frappe import _
import jwt
import datetime
from frappe.auth import LoginManager
from frappe.core.doctype.user.user import reset_password as reset_password_core
from frappe.core.doctype.user.user import update_password
from oan_a2c.api.utils import success_response, handle_api_errors, validate_request, SafeEmail
from pydantic import BaseModel, Field, field_validator
from typing import Optional

class LoginSchema(BaseModel):
	usr: str = Field(..., min_length=1)
	pwd: str = Field(..., min_length=1)

class ForgotPasswordSchema(BaseModel):
	email: SafeEmail = None

class ResetPasswordSchema(BaseModel):
	email: SafeEmail = None
	key: str = Field(..., min_length=1)
	new_password: str = Field(..., min_length=1)


@frappe.whitelist(allow_guest=True)
@validate_request(LoginSchema)
@handle_api_errors
def login(usr=None, pwd=None):
	"""
	Authenticates a user and returns a stateless JWT.
	Wraps Frappe's core LoginManager to ensure standard validations apply
	(account lock, disabled user, etc.) without creating a server-side session.
	"""
	try:
		login_manager = LoginManager()
		# authenticate() validates credentials and raises AuthenticationError on failure.
		# We deliberately skip post_login() — it writes a session record to the DB and
		# sets a cookie, which contradicts our stateless JWT architecture.
		login_manager.authenticate(usr, pwd)
	except frappe.exceptions.AuthenticationError:
		frappe.clear_messages()
		raise frappe.AuthenticationError(_("Incorrect email or password."))

	user = frappe.get_doc("User", usr)
	roles = [d.role for d in user.roles]

	secret = frappe.conf.get("encryption_key")
	if not secret:
		frappe.throw(_("System configuration error: missing encryption_key"))

	# Use timezone-aware UTC — datetime.utcnow() is deprecated in Python 3.12+
	now = datetime.datetime.now(datetime.timezone.utc)

	payload = {
		"sub": usr,
		"iss": "oan_a2c_identity_gateway",
		"iat": now,
		"exp": now + datetime.timedelta(hours=1),
		"roles": roles
	}

	token = jwt.encode(payload, secret, algorithm="HS256", headers={"kid": "v1"})

	# Fetch the user's linked bank via User Permissions (populated once
	# the Participating Bank DocType and permission fixtures are active).
	bank = None
	if "Bank Agent" in roles:
		bank = frappe.db.get_value(
			"User Permission",
			{"user": usr, "allow": "Participating Bank"},
			"for_value"
		)

	return success_response(
		data={
			"token": token,
			"user": {
				"email": usr,
				"full_name": user.full_name,
				"roles": roles,
				"bank": bank
			}
		}
	)


@frappe.whitelist(allow_guest=True)
@validate_request(ForgotPasswordSchema)
@handle_api_errors
def forgot_password(email):
	"""
	Triggers Frappe's native secure password recovery flow via email.
	Inherits: active-user validation, system email templates, 24h link expiry.
	"""
	try:
		reset_password_core(email)
	except Exception:
		# Do not leak whether the email exists — return success unconditionally.
		pass

	return success_response(
		message=_("Password reset instructions have been sent to your registered email.")
	)


@frappe.whitelist(allow_guest=True)
@validate_request(ResetPasswordSchema)
@handle_api_errors
def reset_password(email, key, new_password):
	"""
	Decoupled bridge: accepts the key from the reset email link and sets a new password.
	"""
	user = frappe.db.get_value("User", {"email": email, "reset_password_key": key}, "name")

	if not user:
		raise frappe.AuthenticationError(_("Invalid or expired reset token."))

	# user= must be passed explicitly. In a stateless (guest) context, omitting it
	# causes Frappe to default to frappe.session.user which is "Guest", not the
	# target account — resulting in a silent no-op or a permission error.
	update_password(new_password=new_password, logout_all_sessions=True, key=key, user=user)
	return success_response(
		message=_("Your password has been successfully updated. You may now login.")
	)


@frappe.whitelist()
@handle_api_errors
def get_me():
	"""
	Returns the authenticated user's profile details: name, email, roles, and linked bank.
	"""
	if frappe.session.user == "Guest":
		frappe.throw(_("Not permitted"), frappe.AuthenticationError)

	user = frappe.get_doc("User", frappe.session.user)
	roles = [d.role for d in user.roles]

	# Fetch the user's linked bank via User Permissions
	bank = None
	if "Bank Agent" in roles:
		bank = frappe.db.get_value(
			"User Permission",
			{"user": frappe.session.user, "allow": "Participating Bank"},
			"for_value"
		)

	return success_response(
		data={
			"email": user.email,
			"full_name": user.full_name,
			"roles": roles,
			"bank": bank
		}
	)


