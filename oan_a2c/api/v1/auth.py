import frappe
from frappe import _
from pydantic import BaseModel, Field, field_validator

from oan_a2c.a2c_marketplace.roles import BANK_ADMIN_ROLE, DEVELOPMENT_AGENT_ROLE, FARMER_ROLE
from oan_a2c.api.utils import (
	RequiredPhone,
	SafeEmail,
	check_rate_limit,
	handle_api_errors,
	success_response,
	validate_password_complexity,
	validate_request,
)

SELF_REGISTERABLE_ROLES = {BANK_ADMIN_ROLE, DEVELOPMENT_AGENT_ROLE, FARMER_ROLE}


class RegisterUserSchema(BaseModel):
	# SafeEmail is Annotated[str | None, ...], so this validates the format but cannot
	# enforce presence -- validate_request always supplies the signature default, so a
	# missing address arrives here as a legitimate None. register_user does the
	# presence check; it is the only thing standing between a farmer and an account
	# they can never log in to.
	email: SafeEmail | None = None
	full_name: str = Field(..., min_length=2)
	password: str = Field(..., min_length=8, max_length=64)
	phone_number: RequiredPhone
	role: str = Field(default=BANK_ADMIN_ROLE, min_length=2, max_length=140)

	@field_validator("password")
	@classmethod
	def validate_password(cls, v: str) -> str:
		return validate_password_complexity(v)


def create_user_account(
	email: str,
	full_name: str,
	password: str,
	phone_number: str,
	role: str | None = None,
	must_change_password: bool = False,
):
	"""Create a User with a password.

	`must_change_password` marks the password as admin-issued and temporary: the
	account authenticates but cannot open a session until the user rotates it
	through api.auth.set_initial_password. Self-registration leaves it False —
	that password is already the user's own.
	"""
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
		"a2c_must_change_password": 1 if must_change_password else 0,
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
def register_user(
	full_name: str, password: str, phone_number: str, email: str | None = None, role: str = BANK_ADMIN_ROLE
):
	# KNOWN ISSUE -- registration is loose in two related ways, both left as-is for now.
	#
	# 1. The per-IP budget went from 5/min to 50/min for development convenience and
	#    has not been lowered again.
	# 2. A phone number that is already registered now returns a success envelope
	#    carrying `already_exists: true`, rather than an error. That is good UX ("you
	#    already have an account, please log in") but it is also an oracle.
	#
	# Together they let one IP test 50 phone numbers a minute and learn, for each,
	# whether it belongs to a registered user. The same is true of the email branch
	# just below. Before production, decide whether the enumeration is acceptable for
	# this product (it may well be, for a phone-first flow in a known user base) and
	# if not, lower the per-IP cap and make both "already registered" responses
	# indistinguishable from a successful registration.
	check_rate_limit(f"rl:register_user:{getattr(frappe.local, 'request_ip', 'guest')}", limit=50, window=60)
	check_rate_limit(f"rl:register_phone:{phone_number}", limit=5, window=60)

	if role not in SELF_REGISTERABLE_ROLES:
		frappe.throw(_("Invalid role."), frappe.ValidationError)

	# Farmers register with a real email like every other role. Production identity
	# comes from the Fayda registry over OAuth; email + password is the development
	# stand-in until that lands. The phone number is still collected -- it is how the
	# consent webhook matches a User to an A2C Farmer Profile -- but it is not an
	# identity, and nothing may derive a login from it.
	if not email:
		frappe.throw(_("Email is required for this role."), frappe.ValidationError)

	if frappe.db.exists("User", email):
		return success_response(
			data={
				"message": _("You already have an account. Please log in."),
				"already_exists": True,
			}
		)

	if frappe.db.exists("User", {"mobile_no": phone_number}):
		return success_response(
			data={
				"message": _("You already have an account. Please log in."),
				"already_exists": True,
			}
		)

	create_user_account(
		email=email,
		full_name=full_name,
		password=password,
		phone_number=phone_number,
		role=role,
	)
	return success_response(data={"message": _("Account created successfully.")})
