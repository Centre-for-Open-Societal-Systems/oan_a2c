import re

import frappe
from frappe import _
from pydantic import BaseModel, Field

from oan_a2c.a2c_marketplace.roles import BANK_ADMIN_ROLE, BANK_AGENT_ROLE
from oan_a2c.api.utils import SafeEmail, handle_api_errors, success_response, validate_request
from oan_a2c.api.v1.auth import RegisterUserSchema, create_user_account

RegisterSellerSchema = RegisterUserSchema


def resolve_assignable_role(role: str, allowed: set[str]) -> str:
	"""Validate a client-supplied role against an allowlist, or reject it.

	Canonical `Role` names only (from a2c_marketplace.roles). This is the ONLY
	gate on the client-supplied `role` in invite_user/update_user_profile — never
	append a raw client string to User.roles, or a caller can hand themselves
	System Manager / A2C Administrator, or resurrect a retired plain-named role.
	"""
	if role not in allowed:
		frappe.throw(_("Invalid role."), frappe.ValidationError)
	return role


@frappe.whitelist(allow_guest=True)
@validate_request(RegisterSellerSchema)
@handle_api_errors
def register_seller(email: str, full_name: str, password: str, phone_number: str):
	"""
	Guest accessible: Creates a User with the A2C Bank Admin role.
	"""
	create_user_account(
		email=email,
		full_name=full_name,
		password=password,
		phone_number=phone_number,
		role=BANK_ADMIN_ROLE,
	)
	return success_response(data={"message": _("Seller registered successfully. You may now login.")})


class RegisterBankSchema(BaseModel):
	bank_name: str = Field(..., min_length=2)
	bank_code: str = Field(..., min_length=2)
	entity_type: str = Field(...)
	registered_street: str = Field(..., min_length=2)
	registered_city: str = Field(..., min_length=2)
	registered_country: str = Field(..., min_length=2)
	registered_postal_code: str = Field(..., min_length=2)
	registered_email: SafeEmail
	registered_phone: str = Field(..., min_length=2)


class SaveOrgContactsSchema(BaseModel):
	gro_name: str
	gro_mobile: str
	ops_name: str
	ops_mobile: str


class UploadKycSchema(BaseModel):
	filename: str = Field(..., min_length=4, max_length=30, pattern=r"^.+\.pdf$")
	# Max length approx 15MB for base64
	filedata: str = Field(..., min_length=10, max_length=15000000)


class ActivateBankSchema(BaseModel):
	pass


class UpdateBankStatusSchema(BaseModel):
	bank_code: str = Field(..., min_length=2)
	new_status: str = Field(..., pattern="^(Onboarding|Active|Suspended)$")


class InviteUserSchema(BaseModel):
	email: SafeEmail
	full_name: str = Field(..., min_length=2)
	role: str = Field(..., min_length=2)
	password: str = Field(..., min_length=6)


class DeactivateUserSchema(BaseModel):
	email: SafeEmail


# -----------------
# 2. register_bank
# -----------------
def normalize_tin(tin: str) -> str:
	return re.sub(r"[^A-Z0-9]", "", str(tin).upper())


@frappe.whitelist()
@validate_request(RegisterBankSchema)
@handle_api_errors
def register_bank(**kwargs):
	user = frappe.session.user
	if user == "Guest":
		frappe.throw(_("Authentication required"), frappe.AuthenticationError)

	if frappe.db.exists("User Permission", {"user": user, "allow": "A2C Participating Bank"}):
		frappe.throw(_("User is already associated with an organization."))

	bank_code = normalize_tin(kwargs.get("bank_code"))

	existing_bank = frappe.db.exists("A2C Participating Bank", {"bank_code": bank_code})
	if existing_bank:
		frappe.get_doc(
			{
				"doctype": "ToDo",
				"description": f"Duplicate bank registration attempt for TIN {bank_code} by {user}.",
				"reference_type": "A2C Participating Bank",
				"reference_name": existing_bank,
				"allocated_to": "Administrator",
				"status": "Open",
			}
		).insert(ignore_permissions=True)
		return success_response(
			data={"message": _("Your registration attempt has been flagged for admin review.")}
		)

	# Create Bank, Role Profile, and User Permission in one transaction
	try:
		# 1. Create Bank
		bank = frappe.get_doc(
			{
				"doctype": "A2C Participating Bank",
				"bank_code": bank_code,
				"bank_name": kwargs.get("bank_name"),
				"entity_type": kwargs.get("entity_type"),
				"registered_street": kwargs.get("registered_street"),
				"registered_city": kwargs.get("registered_city"),
				"registered_country": kwargs.get("registered_country"),
				"registered_postal_code": kwargs.get("registered_postal_code"),
				"registered_email": kwargs.get("registered_email"),
				"registered_phone": kwargs.get("registered_phone"),
				"status": "Onboarding",
			}
		)
		bank.insert(ignore_permissions=True)

		# 2. Create User Permission
		perm = frappe.get_doc(
			{
				"doctype": "User Permission",
				"user": user,
				"allow": "A2C Participating Bank",
				"for_value": bank.name,
				"is_default": 1,
			}
		)
		perm.insert(ignore_permissions=True)
	except Exception as e:
		frappe.db.rollback()
		frappe.throw(_("Failed to register bank: {0}").format(str(e)))

	return success_response(
		data={
			"message": _("Bank registered successfully. Currently onboarding."),
			"bank_code": bank.bank_code,
			"bank_id": bank.name,
		}
	)


# -----------------
# 3. save_org_contacts
# -----------------
@frappe.whitelist()
@validate_request(SaveOrgContactsSchema)
@handle_api_errors
def save_org_contacts(**kwargs):
	user = frappe.session.user
	bank = frappe.db.get_value(
		"User Permission", {"user": user, "allow": "A2C Participating Bank"}, "for_value"
	)

	if not bank:
		frappe.throw(_("No bank associated with the current user."))

	doc = frappe.get_doc("A2C Participating Bank", bank)
	# Check if caller is Bank Admin (ideally via perm or role check, but doc.save() handles standard permissions)

	doc.gro_name = kwargs.get("gro_name")
	doc.gro_mobile = kwargs.get("gro_mobile")
	doc.ops_name = kwargs.get("ops_name")
	doc.ops_mobile = kwargs.get("ops_mobile")
	doc.save()

	return success_response(data={"message": _("Contacts saved successfully.")})


# -----------------
# 3b. upload_kyc_document
# -----------------
@frappe.whitelist()
@handle_api_errors
@validate_request(UploadKycSchema)
def upload_kyc_document(**kwargs):
	user = frappe.session.user
	bank = frappe.db.get_value(
		"User Permission", {"user": user, "allow": "A2C Participating Bank"}, "for_value"
	)

	if not bank:
		frappe.throw(_("No bank associated with the current user."))

	import base64

	try:
		decoded = base64.b64decode(kwargs.get("filedata"), validate=True)
	except Exception:
		frappe.throw(_("Invalid file: content is not valid Base64."), frappe.ValidationError)

	if not decoded.startswith(b"%PDF-"):
		frappe.throw(_("Invalid file: only PDF documents are accepted."), frappe.ValidationError)

	try:
		file_doc = frappe.get_doc(
			{
				"doctype": "File",
				"file_name": kwargs.get("filename"),
				"content": kwargs.get("filedata"),
				"decode": 1,
				"attached_to_doctype": "A2C Participating Bank",
				"attached_to_name": bank,
				"attached_to_field": "kyc_document",
				"is_private": 1,
			}
		)
		file_doc.insert(ignore_permissions=True)
	except Exception:
		frappe.throw(_("Failed to save uploaded file."))

	frappe.db.set_value("A2C Participating Bank", bank, "kyc_document", file_doc.file_url)

	return success_response(
		data={"message": _("KYC document uploaded successfully."), "file_url": file_doc.file_url}
	)


# -----------------
# 4a. get_bank_status
# -----------------
@frappe.whitelist()
@handle_api_errors
def get_bank_status():
	user = frappe.session.user
	bank = frappe.db.get_value(
		"User Permission", {"user": user, "allow": "A2C Participating Bank"}, "for_value"
	)

	if not bank:
		frappe.throw(_("No bank associated with the current user."))

	status = frappe.db.get_value("A2C Participating Bank", bank, "status")
	return success_response(data={"status": status})


# -----------------
# 4b. update_bank_status
# -----------------
@frappe.whitelist()
@handle_api_errors
@validate_request(UpdateBankStatusSchema)
def update_bank_status(**kwargs):
	bank_code = kwargs.get("bank_code")
	new_status = kwargs.get("new_status")

	user = frappe.session.user
	user_doc = frappe.get_doc("User", user)
	is_admin = any(d.role == BANK_ADMIN_ROLE for d in user_doc.roles)

	if not is_admin:
		frappe.throw(_("Only Bank Admins can update bank status."), frappe.PermissionError)

	if not frappe.db.exists("A2C Participating Bank", {"bank_code": bank_code}):
		frappe.throw(_("Bank {0} not found").format(bank_code), frappe.DoesNotExistError)

	doc = frappe.get_doc("A2C Participating Bank", {"bank_code": bank_code})

	if doc.status == new_status:
		return success_response(data={"message": _("Status is already {0}").format(new_status)})

	doc.status = new_status
	doc.save(ignore_permissions=True)

	return success_response(data={"message": _("Bank status updated to {0}").format(new_status)})


# -----------------
# 5. invite_user
# -----------------
@frappe.whitelist()
@validate_request(InviteUserSchema)
@handle_api_errors
def invite_user(email: str, full_name: str, role: str, password: str):
	role = resolve_assignable_role(role, {BANK_ADMIN_ROLE, BANK_AGENT_ROLE})

	user = frappe.session.user
	bank = frappe.db.get_value(
		"User Permission", {"user": user, "allow": "A2C Participating Bank"}, "for_value"
	)

	if not bank:
		frappe.throw(_("No bank associated with the current user."))

	if frappe.db.exists("User", email):
		is_in_this_bank = frappe.db.exists(
			"User Permission", {"user": email, "allow": "A2C Participating Bank", "for_value": bank}
		)
		if is_in_this_bank:
			return success_response(data={"message": _("User has already joined.")})

		is_in_other_bank = frappe.db.exists(
			"User Permission", {"user": email, "allow": "A2C Participating Bank"}
		)
		if is_in_other_bank:
			# Fake success to prevent info leak
			return success_response(data={"message": _("User invited successfully.")})
	else:
		create_user_account(email=email, full_name=full_name, password=password, phone_number="", role=role)

	try:
		user_doc = frappe.get_doc("User", email)
		if not any(d.role == role for d in user_doc.roles):
			user_doc.append("roles", {"role": role})
			user_doc.save(ignore_permissions=True)

		if not frappe.db.exists(
			"User Permission", {"user": email, "allow": "A2C Participating Bank", "for_value": bank}
		):
			has_default = frappe.db.exists(
				"User Permission", {"user": email, "allow": "A2C Participating Bank", "is_default": 1}
			)
			perm = frappe.get_doc(
				{
					"doctype": "User Permission",
					"user": email,
					"allow": "A2C Participating Bank",
					"for_value": bank,
					"is_default": 0 if has_default else 1,
				}
			)
			perm.insert(ignore_permissions=True)
	except Exception as e:
		frappe.db.rollback()
		frappe.throw(_("Failed to invite user: {0}").format(str(e)))

	return success_response(data={"message": _("User invited successfully.")})


# -----------------
# 6. deactivate_user
# -----------------
@frappe.whitelist()
@validate_request(DeactivateUserSchema)
@handle_api_errors
def deactivate_user(email: str):
	user = frappe.session.user
	bank = frappe.db.get_value(
		"User Permission", {"user": user, "allow": "A2C Participating Bank"}, "for_value"
	)

	if not bank:
		frappe.throw(_("No bank associated with the current user."))

	target_bank = frappe.db.get_value(
		"User Permission", {"user": email, "allow": "A2C Participating Bank"}, "for_value"
	)
	if target_bank != bank:
		frappe.throw(_("Not permitted to deactivate a user from another bank."))

	frappe.db.set_value("User", email, "enabled", 0)

	return success_response(data={"message": _("User deactivated successfully.")})


class UpdateUserProfileSchema(BaseModel):
	email: SafeEmail
	full_name: str | None = None
	role: str | None = None


# -----------------
# 7. list_users
# -----------------
@frappe.whitelist()
@handle_api_errors
def list_users():
	user = frappe.session.user
	bank = frappe.db.get_value(
		"User Permission", {"user": user, "allow": "A2C Participating Bank"}, "for_value"
	)

	if not bank:
		frappe.throw(_("No bank associated with the current user."))

	# Find all users that have a User Permission for this bank
	permissions = frappe.get_all(
		"User Permission", filters={"allow": "A2C Participating Bank", "for_value": bank}, fields=["user"]
	)
	bank_users = [p.user for p in permissions]

	users = frappe.get_all(
		"User", filters={"name": ("in", bank_users)}, fields=["name", "email", "first_name", "enabled"]
	)

	return success_response(data={"users": users})


# -----------------
# 8. update_user_profile
# -----------------
@frappe.whitelist()
@validate_request(UpdateUserProfileSchema)
@handle_api_errors
def update_user_profile(email: str, full_name: str | None = None, role: str | None = None):
	user = frappe.session.user
	bank = frappe.db.get_value(
		"User Permission", {"user": user, "allow": "A2C Participating Bank"}, "for_value"
	)

	if not bank:
		frappe.throw(_("No bank associated with the current user."))

	target_bank = frappe.db.get_value(
		"User Permission", {"user": email, "allow": "A2C Participating Bank"}, "for_value"
	)
	if target_bank != bank:
		frappe.throw(_("Not permitted to update a user from another bank."))

	target_user = frappe.get_doc("User", email)

	if full_name:
		target_user.first_name = full_name

	if role:
		role = resolve_assignable_role(role, {BANK_ADMIN_ROLE, BANK_AGENT_ROLE})
		# check if role is already assigned
		if not any(d.role == role for d in target_user.roles):
			target_user.append("roles", {"role": role})

	target_user.save(ignore_permissions=True)

	return success_response(data={"message": _("User profile updated successfully.")})
