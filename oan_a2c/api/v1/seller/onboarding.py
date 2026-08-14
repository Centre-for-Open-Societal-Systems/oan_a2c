import re

import frappe
from frappe import _
from pydantic import BaseModel, Field, field_validator

from oan_a2c.a2c_marketplace.permissions import is_bank_unbound, require_bank_role
from oan_a2c.a2c_marketplace.roles import (
	ADMIN_ROLE,
	BANK_ADMIN_ROLE,
	BANK_AGENT_ROLE,
	DEVELOPMENT_AGENT_ROLE,
	FARMER_ROLE,
)
from oan_a2c.api.utils import (
	RequiredPhone,
	SafeEmail,
	handle_api_errors,
	success_response,
	validate_request,
)
from oan_a2c.api.v1.auth import create_user_account

ROLE_LEVELS: dict[str, int] = {
	ADMIN_ROLE: 1,
	"System Manager": 1,
	BANK_ADMIN_ROLE: 2,
	BANK_AGENT_ROLE: 3,
	DEVELOPMENT_AGENT_ROLE: 3,
	FARMER_ROLE: 4,
}
_ALL_A2C_ROLES = frozenset(ROLE_LEVELS)


def _get_user_level(user: str) -> int:
	levels = [ROLE_LEVELS[r] for r in frappe.get_roles(user) if r in ROLE_LEVELS]
	return min(levels) if levels else 99


def resolve_assignable_role(role: str, allowed: set[str]) -> str:
	"""Validate a client-supplied role against an allowlist, or reject it.

	Canonical `Role` names only (from a2c_marketplace.roles). This is the ONLY
	gate on the client-supplied `role` in invite_user — never append a raw client
	string to User.roles, or a caller can hand themselves System Manager /
	A2C Administrator, or resurrect a retired plain-named role. (update_user does
	its own level-based role check; see ROLE_LEVELS.)
	"""
	if role not in allowed:
		frappe.throw(_("Invalid role."), frappe.ValidationError)
	return role


class RegisterBankSchema(BaseModel):
	bank_name: str = Field(..., min_length=2, max_length=140)
	bank_code: str = Field(..., min_length=2, max_length=140)
	entity_type: str = Field(..., min_length=2, max_length=140)
	registered_street: str = Field(..., min_length=2, max_length=255)
	registered_kebele_village: str | None = Field(None, max_length=140)
	registered_woreda_district: str | None = Field(None, max_length=140)
	registered_zone: str | None = Field(None, max_length=140)
	registered_region: str = Field(..., min_length=2, max_length=140)
	registered_country: str = Field(..., min_length=2, max_length=140)
	registered_postal_code: str = Field(..., min_length=2, max_length=20)
	registered_email: SafeEmail
	registered_phone: RequiredPhone
	website: str | None = Field(None, max_length=255)


class UpdateBankProfileSchema(BaseModel):
	bank_name: str | None = Field(None, max_length=140)
	brand_name: str | None = Field(None, max_length=140)
	website: str | None = Field(None, max_length=255)
	registered_street: str | None = Field(None, max_length=255)
	registered_kebele_village: str | None = Field(None, max_length=140)
	registered_woreda_district: str | None = Field(None, max_length=140)
	registered_zone: str | None = Field(None, max_length=140)
	registered_region: str | None = Field(None, max_length=140)
	registered_country: str | None = Field(None, max_length=140)
	registered_postal_code: str | None = Field(None, max_length=20)
	registered_email: SafeEmail | None = None
	registered_phone: RequiredPhone | None = None
	logo: str | None = Field(None, max_length=255)


class SaveOrgContactsSchema(BaseModel):
	gro_name: str = Field(..., min_length=1, max_length=140)
	gro_mobile: RequiredPhone
	ops_name: str = Field(..., min_length=1, max_length=140)
	ops_mobile: RequiredPhone


class UploadKycSchema(BaseModel):
	filename: str = Field(..., min_length=4, max_length=255, pattern=r"^.+\.pdf$")
	# Max length approx 15MB for base64
	filedata: str = Field(..., min_length=10, max_length=15000000)


class UploadImageSchema(BaseModel):
	filename: str = Field(..., min_length=4, max_length=100, pattern=r"^.+\.(?i)(png|jpe?g|webp)$")
	# Max length approx 5MB for base64
	filedata: str = Field(..., min_length=10, max_length=7000000)

	@field_validator("filedata")
	@classmethod
	def validate_image_data(cls, v: str) -> str:
		import base64

		try:
			decoded = base64.b64decode(v, validate=True)
		except Exception:
			raise ValueError("Content is not valid Base64.")

		if len(decoded) > 5 * 1024 * 1024:
			raise ValueError("File size exceeds 5MB limit.")

		is_png = decoded.startswith(b"\x89PNG\r\n\x1a\n")
		is_jpeg = decoded.startswith(b"\xff\xd8")
		is_webp = decoded.startswith(b"RIFF") and len(decoded) >= 12 and decoded[8:12] == b"WEBP"

		if not (is_png or is_jpeg or is_webp):
			raise ValueError("File content is not a valid PNG, JPEG, or WebP image.")

		return v


class ActivateBankSchema(BaseModel):
	pass


class UpdateBankStatusSchema(BaseModel):
	bank_code: str | None = None
	new_status: str = Field(..., pattern="^(In Review|Active|Suspended)$")


class InviteUserSchema(BaseModel):
	email: SafeEmail
	full_name: str = Field(..., min_length=2, max_length=140)
	role: str = Field(..., min_length=2, max_length=140)
	password: str = Field(..., min_length=8, max_length=64)

	@field_validator("password")
	@classmethod
	def validate_pwd(cls, v: str) -> str:
		if not any(c.isalpha() for c in v) or not any(c.isdigit() for c in v):
			raise ValueError("Password must contain at least one letter and one number.")
		return v


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
			data={
				"message": _("Bank registered successfully. Currently in review."),
				"bank_code": bank_code,
				"bank_id": existing_bank,
			}
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
				"registered_kebele_village": kwargs.get("registered_kebele_village"),
				"registered_woreda_district": kwargs.get("registered_woreda_district"),
				"registered_zone": kwargs.get("registered_zone"),
				"registered_region": kwargs.get("registered_region"),
				"registered_country": kwargs.get("registered_country"),
				"registered_postal_code": kwargs.get("registered_postal_code"),
				"registered_email": kwargs.get("registered_email"),
				"registered_phone": kwargs.get("registered_phone"),
				"website": kwargs.get("website"),
				"status": "In Review",
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
			"message": _("Bank registered successfully. Currently in review."),
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
@validate_request(UploadKycSchema)
@handle_api_errors
@require_bank_role(BANK_ADMIN_ROLE)
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
# 3d. upload_image
# -----------------
@frappe.whitelist()
@handle_api_errors
@validate_request(UploadImageSchema)
def upload_image(**kwargs):
	user = frappe.session.user
	if user == "Guest":
		frappe.throw(_("Authentication required"), frappe.AuthenticationError)

	try:
		file_doc = frappe.get_doc(
			{
				"doctype": "File",
				"file_name": kwargs.get("filename"),
				"content": kwargs.get("filedata"),
				"decode": 1,
				"is_private": 0,
			}
		)
		file_doc.insert(ignore_permissions=True)
	except Exception:
		frappe.throw(_("Failed to save uploaded image."))

	return success_response(
		data={"message": _("Image uploaded successfully."), "file_url": file_doc.file_url}
	)


# -----------------
# 3c. get_bank_profile
# -----------------
@frappe.whitelist()
@handle_api_errors
def get_bank_profile():
	user = frappe.session.user
	bank = frappe.db.get_value(
		"User Permission", {"user": user, "allow": "A2C Participating Bank"}, "for_value"
	)

	if not bank:
		frappe.throw(_("No bank associated with the current user."))

	doc = frappe.get_doc("A2C Participating Bank", bank)

	data = {
		"bank_id": doc.name,
		"bank_code": doc.bank_code,
		"bank_name": doc.bank_name,
		"brand_name": doc.brand_name,
		"entity_type": doc.entity_type,
		"logo": doc.logo,
		"registered_street": doc.registered_street,
		"registered_kebele_village": doc.registered_kebele_village,
		"registered_woreda_district": getattr(doc, "registered_woreda_district", None),
		"registered_zone": getattr(doc, "registered_zone", None),
		"registered_region": getattr(doc, "registered_region", None),
		"registered_country": getattr(doc, "registered_country", None),
		"registered_postal_code": getattr(doc, "registered_postal_code", None),
		"registered_email": doc.registered_email,
		"registered_phone": doc.registered_phone,
		"website": doc.website,
		"status": doc.status,
	}

	# KYC (compliance) and GRO/ops contacts are Bank Admin only.
	# Agents get the basic bank profile without them.
	user_doc = frappe.get_doc("User", user)
	if any(d.role == BANK_ADMIN_ROLE for d in user_doc.roles):
		data.update(
			{
				"gro_name": doc.gro_name,
				"gro_mobile": doc.gro_mobile,
				"ops_name": doc.ops_name,
				"ops_mobile": doc.ops_mobile,
				"kyc_document": doc.kyc_document,
				"kyc_document_uploaded": bool(doc.kyc_document),
				"org_grievance_updated": bool(doc.gro_name and doc.gro_mobile),
			}
		)

	return success_response(data=data)


# -----------------
# 3c-2. update_bank_profile
# -----------------
@frappe.whitelist()
@validate_request(UpdateBankProfileSchema)
@handle_api_errors
def update_bank_profile(**kwargs):
	user = frappe.session.user
	bank = frappe.db.get_value(
		"User Permission", {"user": user, "allow": "A2C Participating Bank"}, "for_value"
	)
	if not bank:
		frappe.throw(_("No bank associated with the current user."))

	user_doc = frappe.get_doc("User", user)
	if not any(d.role == BANK_ADMIN_ROLE for d in user_doc.roles):
		frappe.throw(_("Only Bank Admins can update the organization profile."), frappe.PermissionError)

	doc = frappe.get_doc("A2C Participating Bank", bank)

	editable_fields = [
		"bank_name",
		"brand_name",
		"website",
		"registered_street",
		"registered_kebele_village",
		"registered_woreda_district",
		"registered_zone",
		"registered_region",
		"registered_country",
		"registered_postal_code",
		"registered_email",
		"registered_phone",
		"logo",
	]

	for field in editable_fields:
		if field in kwargs and kwargs.get(field) is not None:
			new_val = kwargs.get(field)
			if field == "logo" and doc.logo and doc.logo != new_val:
				old_file = frappe.db.get_value("File", {"file_url": doc.logo}, "name")
				if old_file:
					frappe.delete_doc("File", old_file, ignore_permissions=True, force=True)
			setattr(doc, field, new_val)

	doc.save(ignore_permissions=True)
	return success_response(data={"message": _("Organization profile updated successfully.")})


# -----------------
# 4. update_bank_status
# -----------------
@frappe.whitelist()
@handle_api_errors
@validate_request(UpdateBankStatusSchema)
def update_bank_status(**kwargs):
	bank_code = kwargs.get("bank_code")
	new_status = kwargs.get("new_status")

	user = frappe.session.user

	if is_bank_unbound(user):
		if not bank_code:
			frappe.throw(_("bank_code is required for administrators."), frappe.ValidationError)
		bank_id = frappe.db.get_value("A2C Participating Bank", {"bank_code": bank_code}, "name")
		if not bank_id:
			frappe.throw(_("Bank {0} not found").format(bank_code), frappe.DoesNotExistError)
	else:
		user_doc = frappe.get_doc("User", user)
		if not any(d.role == BANK_ADMIN_ROLE for d in user_doc.roles):
			frappe.throw(_("Only Bank Admins can update bank status."), frappe.PermissionError)

		bank_id = frappe.db.get_value(
			"User Permission", {"user": user, "allow": "A2C Participating Bank"}, "for_value"
		)
		if not bank_id:
			frappe.throw(_("No bank associated with the current user."), frappe.PermissionError)

	doc = frappe.get_doc("A2C Participating Bank", bank_id)

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
@require_bank_role(BANK_ADMIN_ROLE)
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
# 6. list_users
# -----------------
@frappe.whitelist()
@handle_api_errors
@require_bank_role(BANK_ADMIN_ROLE)
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
	bank_users = [p.user for p in permissions if p.user != user]

	users = frappe.get_all(
		"User",
		filters={"name": ("in", bank_users)},
		fields=["name", "email", "first_name", "enabled", "last_active"],
	)

	roles = frappe.get_all(
		"Has Role",
		filters={"parent": ("in", bank_users), "role": ("in", (BANK_ADMIN_ROLE, BANK_AGENT_ROLE))},
		fields=["parent", "role"],
	)

	user_role_map = {r.parent: r.role for r in roles}

	for u in users:
		u["role"] = user_role_map.get(u.name)

	return success_response(data={"users": users})


# -----------------
# 7. update_user
# -----------------
class UpdateUserSchema(BaseModel):
	email: SafeEmail
	full_name: str | None = Field(None, max_length=140)
	role: str | None = Field(None, max_length=140)
	enabled: bool | None = None


@frappe.whitelist()
@validate_request(UpdateUserSchema)
@handle_api_errors
def update_user(
	email: str, full_name: str | None = None, role: str | None = None, enabled: bool | None = None
):
	caller = frappe.session.user
	caller_roles = set(frappe.get_roles(caller))
	caller_level = _get_user_level(caller)

	# Only two tiers may manage users at all. Platform admins (level 1) act
	# across banks; Bank Admins act only within their own bank. Everyone else
	# (Bank Agent, Dev Agent, Farmer) is denied even toward a lower level.
	is_platform_admin = caller_level == 1
	is_bank_admin = BANK_ADMIN_ROLE in caller_roles
	if not (is_platform_admin or is_bank_admin):
		frappe.throw(_("You do not have permission to manage users."), frappe.PermissionError)

	if email == caller:
		frappe.throw(_("You cannot modify your own account through this endpoint."), frappe.ValidationError)

	if not frappe.db.exists("User", email):
		frappe.throw(_("User not found."), frappe.DoesNotExistError)

	target_roles = set(frappe.get_roles(email))
	target_level = _get_user_level(email)

	# Guard against reaching a peer or a superior (strictly-lower rule).
	if caller_level >= target_level:
		frappe.throw(
			_("You can only manage users with a lower privilege level than your own."),
			frappe.PermissionError,
		)

	# Bank Admins (when not also a platform admin) may only manage Bank Agents,
	# and only within their own bank. Farmers / Dev Agents are platform-managed.
	if is_bank_admin and not is_platform_admin:
		if BANK_AGENT_ROLE not in target_roles:
			frappe.throw(_("Bank Admins can only manage Bank Agents."), frappe.PermissionError)

		caller_bank = frappe.db.get_value(
			"User Permission", {"user": caller, "allow": "A2C Participating Bank"}, "for_value"
		)
		if not caller_bank:
			frappe.throw(_("No bank associated with the current user."), frappe.PermissionError)
		target_bank = frappe.db.get_value(
			"User Permission", {"user": email, "allow": "A2C Participating Bank"}, "for_value"
		)
		if target_bank != caller_bank:
			frappe.throw(_("Not permitted to manage a user from another bank."), frappe.PermissionError)

	if role is not None:
		if role not in ROLE_LEVELS:
			frappe.throw(_("Invalid role."), frappe.ValidationError)
		if ROLE_LEVELS[role] <= caller_level:
			frappe.throw(
				_("You can only assign roles with a lower privilege level than your own."),
				frappe.PermissionError,
			)
		# A Bank Admin's only assignable role is Bank Agent — never move a user
		# into a platform role (Dev Agent / Farmer) from the bank console.
		if is_bank_admin and not is_platform_admin and role != BANK_AGENT_ROLE:
			frappe.throw(_("Bank Admins can only assign the Bank Agent role."), frappe.PermissionError)

	target_user = frappe.get_doc("User", email)

	if full_name is not None:
		target_user.first_name = full_name

	if role is not None:
		target_user.roles = [r for r in target_user.roles if r.role not in _ALL_A2C_ROLES]
		target_user.append("roles", {"role": role})

	if enabled is not None:
		target_user.enabled = 1 if enabled else 0

	target_user.save(ignore_permissions=True)

	return success_response(message=_("User updated successfully."))
