import frappe
from frappe import _
from pydantic import BaseModel, Field, field_validator

from oan_a2c.a2c_marketplace.roles import BANK_ADMIN_ROLE, DEVELOPMENT_AGENT_ROLE
from oan_a2c.api.utils import (
	RequiredPhone,
	SafeEmail,
	check_rate_limit,
	handle_api_errors,
	success_response,
	validate_request,
)

SELF_REGISTERABLE_ROLES = {BANK_ADMIN_ROLE, DEVELOPMENT_AGENT_ROLE}


class RegisterUserSchema(BaseModel):
	email: SafeEmail
	full_name: str = Field(..., min_length=2)
	password: str = Field(..., min_length=8, max_length=64)
	phone_number: RequiredPhone
	role: str = Field(default=BANK_ADMIN_ROLE, min_length=2, max_length=140)

	@field_validator("password")
	@classmethod
	def validate_password_complexity(cls, v: str) -> str:
		if not any(char.isalpha() for char in v):
			raise ValueError("Password must contain at least one letter.")
		if not any(char.isdigit() for char in v):
			raise ValueError("Password must contain at least one number.")
		if not any(not char.isalnum() for char in v):
			raise ValueError("Password must contain at least one special character.")
		return v


def create_user_account(
	email: str,
	full_name: str,
	password: str,
	phone_number: str,
	role: str | None = None,
):
	if frappe.db.exists("User", email):
		return frappe.get_doc("User", email)

	if frappe.db.exists("User", {"mobile_no": phone_number}):
		return None

	first_name, _sep, last_name = full_name.strip().partition(" ")

	user_data = {
		"doctype": "User",
		"email": email,
		"first_name": first_name,
		"last_name": last_name,
		"mobile_no": phone_number,
		"send_welcome_email": 0,
		"new_password": password,
	}
	if role:
		user_data["roles"] = [{"role": role}]

	user = frappe.get_doc(user_data)

	log_count = len(frappe.local.message_log) if getattr(frappe.local, "message_log", None) else 0
	try:
		user.insert(ignore_permissions=True)
	except (frappe.UniqueValidationError, frappe.DuplicateEntryError):
		frappe.db.rollback()
		if hasattr(frappe.local, "message_log"):
			frappe.local.message_log = frappe.local.message_log[:log_count]
		return None

	return user


# nosemgrep: guest-whitelisted-method -- reviewed: public registration endpoint, role allowlisted + rate-limited
@frappe.whitelist(allow_guest=True)
@validate_request(RegisterUserSchema)
@handle_api_errors
def register_user(email: str, full_name: str, password: str, phone_number: str, role: str = BANK_ADMIN_ROLE):
	check_rate_limit(f"rl:register_user:{getattr(frappe.local, 'request_ip', 'guest')}", limit=5, window=60)

	if role not in SELF_REGISTERABLE_ROLES:
		frappe.throw(_("Invalid role."), frappe.ValidationError)

	if frappe.db.exists("User", email):
		return success_response(
			data={
				"message": _("You already have an account. Please log in."),
				"already_exists": True,
			}
		)

	if frappe.db.exists("User", {"mobile_no": phone_number}):
		frappe.throw(
			_("A user with this name or phone number is already registered."),
			frappe.ValidationError,
		)

	create_user_account(
		email=email,
		full_name=full_name,
		password=password,
		phone_number=phone_number,
		role=role,
	)
	return success_response(data={"message": _("Account created successfully.")})
