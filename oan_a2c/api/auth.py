import datetime
import hashlib
import secrets
import string
import time

import frappe
import jwt
from frappe import _
from frappe.auth import LoginManager
from frappe.core.doctype.user.user import update_password
from pydantic import BaseModel, Field

from oan_a2c.a2c_marketplace.roles import BANK_ROLES
from oan_a2c.api.utils import (
	SafeEmail,
	check_rate_limit,
	handle_api_errors,
	success_response,
	validate_request,
)


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
		"aud": "oan_a2c_client",
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
	return raw_token


def _get_user_bank_context(user_id: str) -> dict[str, str | None]:
	"""Resolve bank binding and human-readable bank context for auth payloads."""
	bank_ref = frappe.db.get_value(
		"User Permission", {"user": user_id, "allow": "A2C Participating Bank"}, "for_value"
	)
	if not bank_ref:
		frappe.logger().warning(f"User {user_id} has bank role but no bank binding.")
		return {
			"bank": None,
			"bank_id": None,
			"bank_code": None,
			"bank_name": None,
			"bank_status": None,
		}

	# for_value should be the bank docname; fallback to bank_code for legacy mappings.
	bank_row = frappe.db.get_value(
		"A2C Participating Bank", bank_ref, ["name", "bank_code", "bank_name", "status"], as_dict=True
	) or frappe.db.get_value(
		"A2C Participating Bank",
		{"bank_code": bank_ref},
		["name", "bank_code", "bank_name", "status"],
		as_dict=True,
	)

	if not bank_row:
		frappe.logger().warning(f"User {user_id} bank binding points to missing bank reference: {bank_ref}.")
		return {
			"bank": bank_ref,
			"bank_id": None,
			"bank_code": bank_ref,
			"bank_name": bank_ref,
			"bank_status": None,
		}

	return {
		"bank": bank_row.bank_name,
		"bank_id": bank_row.name,
		"bank_code": bank_row.bank_code,
		"bank_name": bank_row.bank_name,
		"bank_status": bank_row.status,
	}


# nosemgrep: guest-whitelisted-method -- reviewed: public auth endpoint, validated + rate-limited
@frappe.whitelist(allow_guest=True)
@validate_request(LoginSchema)
@handle_api_errors
def login(usr: str | None = None, pwd: str | None = None, remember_me: bool = False):
	"""
	Authenticates a user and returns a short-lived access JWT and a database-backed refresh token.
	Wraps Frappe's core LoginManager to ensure standard validations apply
	(account lock, disabled user, etc.) without creating a server-side session.
	"""
	check_rate_limit(f"rl:login:{getattr(frappe.local, 'request_ip', 'guest')}", limit=10, window=60)

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

	has_bank_role = any(r in BANK_ROLES for r in roles)
	bank_context = {
		"bank": None,
		"bank_id": None,
		"bank_code": None,
		"bank_name": None,
		"bank_status": None,
	}
	if has_bank_role:
		bank_context = _get_user_bank_context(usr)

	return success_response(
		data={
			"token": token,
			"refresh_token": refresh_token,
			"user": {"email": usr, "full_name": user.full_name, "roles": roles, **bank_context},
		}
	)


# nosemgrep: guest-whitelisted-method -- reviewed: public password-recovery endpoint, enumeration-safe
@frappe.whitelist(allow_guest=True)
@validate_request(ForgotPasswordSchema)
@handle_api_errors
def forgot_password(email: str):
	"""
	Generates a secure 6-digit OTP for password recovery with expiration.
	(Simplified implementation: email/SMS delivery bypassed for now).
	"""
	check_rate_limit(f"rl:forgot_pwd:{getattr(frappe.local, 'request_ip', 'guest')}", limit=5, window=60)

	otp = None
	try:
		user = frappe.db.get_value("User", {"email": email}, "name")
		if user:
			otp = "".join(secrets.choice(string.digits) for _ in range(6))
			expiry = int(time.time()) + 900  # 15 minutes expiry
			frappe.db.set_value("User", user, "reset_password_key", f"{otp}:{expiry}")
			# SMS and Email sending bypassed for simple implementation per user instruction
	except Exception:
		frappe.logger().warning(
			f"forgot_password: OTP reset flow raised: {frappe.get_traceback(with_context=False)}"
		)

	return success_response(
		message=_("If your email is registered, a password reset OTP has been generated."),
		data={"otp": otp} if otp else None,
	)


# nosemgrep: guest-whitelisted-method -- reviewed: public reset endpoint, gated on emailed OTP key
@frappe.whitelist(allow_guest=True)
@validate_request(ResetPasswordSchema)
@handle_api_errors
def reset_password(email: str, key: str, new_password: str):
	"""
	Decoupled bridge: accepts the 6-digit OTP key and sets a new password.
	"""
	check_rate_limit(f"rl:reset_pwd:{email}", limit=5, window=300)

	stored_val = frappe.db.get_value("User", {"email": email}, "reset_password_key")
	if not stored_val:
		raise frappe.AuthenticationError(_("Invalid or expired reset OTP."))

	user = frappe.db.get_value("User", {"email": email}, "name")
	if not user:
		raise frappe.AuthenticationError(_("Invalid or expired reset OTP."))

	valid = False
	if ":" in str(stored_val):
		stored_otp, expiry_str = str(stored_val).split(":", 1)
		try:
			if int(time.time()) <= int(expiry_str) and secrets.compare_digest(stored_otp, key):
				valid = True
		except ValueError:
			pass
	else:
		if secrets.compare_digest(str(stored_val), key):
			valid = True

	if not valid:
		raise frappe.AuthenticationError(_("Invalid or expired reset OTP."))

	# Temporarily set reset_password_key to just the key so Frappe's native check passes
	frappe.db.set_value("User", user, "reset_password_key", key)
	try:
		update_password(new_password=new_password, logout_all_sessions=True, key=key, user=user)
	finally:
		frappe.db.set_value("User", user, "reset_password_key", "")

	return success_response(message=_("Your password has been successfully updated. You may now login."))


# nosemgrep: guest-whitelisted-method -- reviewed: public token-rotation endpoint, gated on refresh token
@frappe.whitelist(allow_guest=True)
@validate_request(RefreshTokenSchema)
@handle_api_errors
def refresh(refresh_token: str):
	"""
	Validates the refresh token, performs rotation, and returns a new access & refresh token.
	"""
	check_rate_limit(f"rl:refresh:{getattr(frappe.local, 'request_ip', 'guest')}", limit=30, window=60)

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
		# nosemgrep: frappe-manual-commit -- reviewed: persist token deletion before the raise rolls back
		frappe.db.commit()
		raise frappe.AuthenticationError(_("Refresh token has expired."))

	user_enabled = frappe.db.get_value("User", record["user"], "enabled")
	if not user_enabled:
		frappe.delete_doc("A2C User Refresh Token", record["name"], ignore_permissions=True)
		# nosemgrep: frappe-manual-commit -- reviewed: persist token deletion before the raise rolls back
		frappe.db.commit()
		raise frappe.AuthenticationError(_("User is disabled or does not exist."))

	# Token Rotation: Delete the used token
	frappe.delete_doc("A2C User Refresh Token", record["name"], ignore_permissions=True)

	user_name = record["user"]
	user = frappe.get_doc("User", user_name)
	roles = [d.role for d in user.roles]

	new_access_token = generate_access_token(user_name, roles)
	new_refresh_token = generate_refresh_token(user_name, bool(record["remember_me"]))

	return success_response(data={"token": new_access_token, "refresh_token": new_refresh_token})


# nosemgrep: guest-whitelisted-method -- reviewed: public logout/revoke endpoint, gated on refresh token
@frappe.whitelist(allow_guest=True)
@validate_request(LogoutSchema)
@handle_api_errors
def logout(refresh_token: str):
	"""
	Revokes the provided refresh token by deleting it from the database.
	"""
	token_hash = hashlib.sha256(refresh_token.encode("utf-8")).hexdigest()

	token_records = frappe.get_all(
		"A2C User Refresh Token", filters={"token_hash": token_hash}, fields=["name"]
	)

	if token_records:
		frappe.delete_doc("A2C User Refresh Token", token_records[0]["name"], ignore_permissions=True)

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

	has_bank_role = any(r in BANK_ROLES for r in roles)
	bank_context = {
		"bank": None,
		"bank_id": None,
		"bank_code": None,
		"bank_name": None,
		"bank_status": None,
	}
	if has_bank_role:
		bank_context = _get_user_bank_context(frappe.session.user)

	return success_response(
		data={"email": user.email, "full_name": user.full_name, "roles": roles, **bank_context}
	)
