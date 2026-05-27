import frappe
from frappe import _
import jwt
import datetime
from frappe.auth import LoginManager
from frappe.core.doctype.user.user import reset_password as reset_password_core
from frappe.core.doctype.user.user import update_password


@frappe.whitelist(allow_guest=True)
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
		frappe.local.response["http_status_code"] = 401
		return {
			"exception": "frappe.exceptions.AuthenticationError",
			"message": _("Incorrect email or password.")
		}

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

	token = jwt.encode(payload, secret, algorithm="HS256")

	# Fetch the user's linked bank via User Permissions (populated once
	# the Participating Bank DocType and permission fixtures are active).
	bank = None
	if "Bank Agent" in roles:
		bank = frappe.db.get_value(
			"User Permission",
			{"user": usr, "allow": "Participating Bank"},
			"for_value"
		)

	# Return the inner dict only — Frappe's @whitelist wrapper automatically
	# envelopes the return value in {"message": <return_value>} on the wire.
	return {
		"status": "success",
		"token": token,
		"user": {
			"email": usr,
			"full_name": user.full_name,
			"roles": roles,
			"bank": bank
		}
	}


@frappe.whitelist(allow_guest=True)
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

	return {
		"status": "success",
		"message": _("Password reset instructions have been sent to your registered email.")
	}


@frappe.whitelist(allow_guest=True)
def reset_password(email, key, new_password):
	"""
	Decoupled bridge: accepts the key from the reset email link and sets a new password.
	"""
	user = frappe.db.get_value("User", {"email": email, "reset_password_key": key}, "name")

	if not user:
		frappe.local.response["http_status_code"] = 401
		return {
			"exception": "frappe.exceptions.AuthenticationError",
			"message": _("Invalid or expired reset token.")
		}

	try:
		# user= must be passed explicitly. In a stateless (guest) context, omitting it
		# causes Frappe to default to frappe.session.user which is "Guest", not the
		# target account — resulting in a silent no-op or a permission error.
		update_password(new_password=new_password, logout_all_sessions=True, key=key, user=user)
		return {
			"status": "success",
			"message": _("Your password has been successfully updated. You may now login.")
		}
	except Exception as e:
		frappe.local.response["http_status_code"] = 400
		return {
			"exception": "ValidationError",
			"message": str(e)
		}
