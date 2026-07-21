import frappe
from frappe import _
from pydantic import BaseModel, Field, field_validator

from oan_a2c.api.utils import SafeEmail, handle_api_errors, success_response, validate_request

class RegisterUserSchema(BaseModel):
	email: SafeEmail
	full_name: str = Field(..., min_length=2)
	password: str = Field(..., min_length=8, max_length=64)
	phone_number: str = Field(..., min_length=8)

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

@frappe.whitelist(allow_guest=True)
@validate_request(RegisterUserSchema)
@handle_api_errors
def register_user(email: str, full_name: str, password: str, phone_number: str):
	"""
	Step 1 of onboarding: Creates a User without any bank roles.
	Guest accessible.
	"""
	if frappe.db.exists("User", email):
		frappe.throw(_("User with this email already exists."))

	user = frappe.get_doc({
		"doctype": "User",
		"email": email,
		"first_name": full_name,
		"mobile_no": phone_number,
		"send_welcome_email": 0,
		"new_password": password
	})
	user.insert(ignore_permissions=True)
	
	# nosemgrep: frappe-manual-commit -- reviewed: persist user registration
	frappe.db.commit()

	return success_response(data={"message": _("User registered successfully. You may now login.")})
