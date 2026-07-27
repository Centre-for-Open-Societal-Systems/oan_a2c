import frappe
from frappe import _
from pydantic import BaseModel, Field, field_validator

from oan_a2c.api.utils import SafeEmail, handle_api_errors, success_response, validate_request


class RegisterUserSchema(BaseModel):
	email: SafeEmail
	full_name: str = Field(..., min_length=2)
	password: str = Field(..., min_length=8, max_length=64)
	phone_number: str = Field(..., min_length=10, max_length=16)

	@field_validator("phone_number")
	@classmethod
	def validate_phone(cls, v: str) -> str:
		import re

		v = v.strip()
		digits_only = re.sub(r"\D", "", v)
		if not (10 <= len(digits_only) <= 15):
			raise ValueError("Phone number must contain between 10 and 15 digits.")
		if not re.match(r"^\+?[1-9]\d{9,14}$", v):
			raise ValueError(
				"Phone number must start with a valid country code (e.g., +251...) and contain only digits."
			)
		return v

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

	user_data = {
		"doctype": "User",
		"email": email,
		"first_name": full_name,
		"mobile_no": phone_number,
		"send_welcome_email": 0,
		"new_password": password,
	}
	if role:
		user_data["roles"] = [{"role": role}]

	user = frappe.get_doc(user_data)
	user.insert(ignore_permissions=True)

	return user
