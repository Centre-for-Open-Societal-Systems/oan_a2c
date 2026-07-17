import datetime
import hashlib
from typing import Optional

import frappe
import jwt
from frappe import _
from frappe.auth import LoginManager
from frappe.core.doctype.user.user import update_password
from pydantic import BaseModel, Field, field_validator

from oan_a2c.api.utils import SafeEmail, handle_api_errors, success_response, validate_request


class LoginSchema(BaseModel):
	usr: str = Field(..., min_length=1)
	pwd: str = Field(..., min_length=1)
	remember_me: bool = Field(default=False)


class ForgotPasswordSchema(BaseModel):
	email: SafeEmail = None


class ResetPasswordSchema(BaseModel):
	email: SafeEmail = None
	key: str = Field(..., min_length=1)
	new_password: str = Field(..., min_length=1)


class RefreshTokenSchema(BaseModel):
	refresh_token: str = Field(..., min_length=1)


class LogoutSchema(BaseModel):
	refresh_token: str = Field(..., min_length=1)


def generate_access_token(usr: str, roles: list) -> str:
	secret = frappe.conf.get("encryption_key")
	if not secret:
		frappe.throw(_("System configuration error: missing encryption_key"))

	now = datetime.datetime.now(datetime.UTC)
	payload = {
		"sub": usr,
		"iss": "oan_a2c_identity_gateway",
		"iat": now,
		"exp": now + datetime.timedelta(minutes=15),
		"roles": roles,
	}
	return jwt.encode(payload, secret, algorithm="HS256", headers={"kid": "v1"})


def generate_refresh_token(usr: str, remember_me: bool = False) -> str:
	raw_token = frappe.generate_hash(length=40)
	token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()

	from frappe.utils import now_datetime

	expiry = now_datetime() + datetime.timedelta(days=30 if remember_me else 1)

	token_doc = frappe.get_doc(
		{
			"doctype": "A2C User Refresh Token",
			"user": usr,
			"token_hash": token_hash,
			"expiry": expiry,
			"remember_me": 1 if remember_me else 0,
		}
	)
	token_doc.insert(ignore_permissions=True)
	frappe.db.commit()
	return raw_token


@frappe.whitelist(allow_guest=True)
@validate_request(LoginSchema)
@handle_api_errors
def login(usr=None, pwd=None, remember_me=False):
	"""
	Authenticates a user and returns a short-lived access JWT and a database-backed refresh token.
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

	# Generate new access token and database-backed refresh token
	token = generate_access_token(usr, roles)
	refresh_token = generate_refresh_token(usr, remember_me)

	# Fetch the user's linked bank via User Permissions (populated once
	# the Participating Bank DocType and permission fixtures are active).
	bank = None
	if "Bank Agent" in roles:
		bank = frappe.db.get_value(
			"User Permission", {"user": usr, "allow": "Participating Bank"}, "for_value"
		)

	return success_response(
		data={
			"token": token,
			"refresh_token": refresh_token,
			"user": {"email": usr, "full_name": user.full_name, "roles": roles, "bank": bank},
		}
	)


@frappe.whitelist(allow_guest=True)
@validate_request(ForgotPasswordSchema)
@handle_api_errors
def forgot_password(email):
	"""
	Generates a 6-digit OTP for password recovery. Sends via SMS if available, otherwise Email.
	"""
	import random
	import string

	try:
		user = frappe.db.get_value("User", {"email": email}, ["name", "mobile_no"], as_dict=True)
		if user:
			otp = "".join(random.choices(string.digits, k=6))

			# Save key in user document to work with frappe's update_password
			frappe.db.set_value("User", user.name, "reset_password_key", otp)
			frappe.db.commit()

			if user.mobile_no:
				frappe.send_sms(
					[user.mobile_no], f"Your A2C password reset OTP is {otp}. Do not share this with anyone."
				)
			else:
				frappe.sendmail(
					recipients=[email],
					subject="Password Reset OTP",
					message=f"Your A2C password reset OTP is: <b>{otp}</b>. Do not share this with anyone.",
				)
	except Exception:
		frappe.logger().warning(
			f"forgot_password: OTP reset flow raised (expected for unknown users, "
			f"but investigate if frequent): {frappe.get_traceback(with_context=False)}"
		)

	return success_response(
		message=_("If your email is registered, a password reset OTP has been sent via email or SMS.")
	)


@frappe.whitelist(allow_guest=True)
@validate_request(ResetPasswordSchema)
@handle_api_errors
def reset_password(email, key, new_password):
	"""
	Decoupled bridge: accepts the 6-digit OTP key and sets a new password.
	"""
	user = frappe.db.get_value("User", {"email": email, "reset_password_key": key}, "name")

	if not user:
		raise frappe.AuthenticationError(_("Invalid or expired reset OTP."))

	# user= must be passed explicitly. In a stateless (guest) context, omitting it
	# causes Frappe to default to frappe.session.user which is "Guest", not the
	# target account — resulting in a silent no-op or a permission error.
	update_password(new_password=new_password, logout_all_sessions=True, key=key, user=user)

	# Clear the key after successful reset just in case
	frappe.db.set_value("User", user, "reset_password_key", "")
	frappe.db.commit()

	return success_response(message=_("Your password has been successfully updated. You may now login."))


@frappe.whitelist(allow_guest=True)
@validate_request(RefreshTokenSchema)
@handle_api_errors
def refresh(refresh_token):
	"""
	Validates the refresh token, performs rotation, and returns a new access & refresh token.
	"""
	token_hash = hashlib.sha256(refresh_token.encode("utf-8")).hexdigest()

	token_records = frappe.get_all(
		"A2C User Refresh Token",
		filters={"token_hash": token_hash},
		fields=["name", "user", "expiry", "remember_me"],
	)

	if not token_records:
		raise frappe.AuthenticationError(_("Invalid or expired refresh token."))

	record = token_records[0]

	from frappe.utils import get_datetime, now_datetime

	expiry_dt = get_datetime(record["expiry"])
	if expiry_dt < now_datetime():
		frappe.delete_doc("A2C User Refresh Token", record["name"], ignore_permissions=True)
		frappe.db.commit()
		raise frappe.AuthenticationError(_("Refresh token has expired."))

	user_enabled = frappe.db.get_value("User", record["user"], "enabled")
	if not user_enabled:
		frappe.delete_doc("A2C User Refresh Token", record["name"], ignore_permissions=True)
		frappe.db.commit()
		raise frappe.AuthenticationError(_("User is disabled or does not exist."))

	# Token Rotation: Delete the used token
	frappe.delete_doc("A2C User Refresh Token", record["name"], ignore_permissions=True)

	user_name = record["user"]
	user = frappe.get_doc("User", user_name)
	roles = [d.role for d in user.roles]

	new_access_token = generate_access_token(user_name, roles)
	new_refresh_token = generate_refresh_token(user_name, bool(record["remember_me"]))

	frappe.db.commit()

	return success_response(data={"token": new_access_token, "refresh_token": new_refresh_token})


@frappe.whitelist(allow_guest=True)
@validate_request(LogoutSchema)
@handle_api_errors
def logout(refresh_token):
	"""
	Revokes the provided refresh token by deleting it from the database.
	"""
	token_hash = hashlib.sha256(refresh_token.encode("utf-8")).hexdigest()

	token_records = frappe.get_all(
		"A2C User Refresh Token", filters={"token_hash": token_hash}, fields=["name"]
	)

	if token_records:
		frappe.delete_doc("A2C User Refresh Token", token_records[0]["name"], ignore_permissions=True)
		frappe.db.commit()

	return success_response(message=_("Logged out successfully."))


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
			"User Permission", {"user": frappe.session.user, "allow": "Participating Bank"}, "for_value"
		)

	return success_response(
		data={"email": user.email, "full_name": user.full_name, "roles": roles, "bank": bank}
	)
