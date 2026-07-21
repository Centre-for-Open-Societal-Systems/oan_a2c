import frappe
from frappe import _
from pydantic import BaseModel, Field
import re

from oan_a2c.api.utils import SafeEmail, handle_api_errors, success_response, validate_request

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

class ActivateBankSchema(BaseModel):
	pass

class InviteUserSchema(BaseModel):
	email: SafeEmail
	full_name: str = Field(..., min_length=2)
	role_profile: str = Field(..., min_length=2)

class DeactivateUserSchema(BaseModel):
	email: SafeEmail

# -----------------
# 2. register_bank
# -----------------
def normalize_tin(tin: str) -> str:
	return re.sub(r'[^A-Z0-9]', '', str(tin).upper())

@frappe.whitelist()
@validate_request(RegisterBankSchema)
@handle_api_errors
def register_bank(**kwargs):
	user = frappe.session.user
	if user == "Guest":
		frappe.throw(_("Authentication required"), frappe.AuthenticationError)
		
	bank_code = normalize_tin(kwargs.get("bank_code"))
	
	if frappe.db.exists("A2C Participating Bank", bank_code):
		frappe.throw(_("Bank with this TIN already exists."))

	# Create Bank, Role Profile, and User Permission in one transaction
	try:
		# 1. Create Bank
		bank = frappe.get_doc({
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
			"status": "Onboarding"
		})
		bank.insert(ignore_permissions=True)

		# 2. Assign Bank Admin Role Profile
		user_doc = frappe.get_doc("User", user)
		if not any(d.role_profile == "Bank Admin" for d in user_doc.user_profiles):
			user_doc.append("user_profiles", {"role_profile": "Bank Admin"})
			user_doc.save(ignore_permissions=True)
			
		# 3. Create User Permission
		if not frappe.db.exists("User Permission", {"user": user, "allow": "A2C Participating Bank", "for_value": bank.name}):
			perm = frappe.get_doc({
				"doctype": "User Permission",
				"user": user,
				"allow": "A2C Participating Bank",
				"for_value": bank.name,
				"is_default": 1
			})
			perm.insert(ignore_permissions=True)

		frappe.db.commit()
	except Exception as e:
		frappe.db.rollback()
		frappe.throw(_("Failed to register bank: {0}").format(str(e)))
	
	return success_response(data={"message": _("Bank registered successfully. Currently onboarding."), "bank_code": bank.name})

# -----------------
# 3. save_org_contacts
# -----------------
@frappe.whitelist()
@validate_request(SaveOrgContactsSchema)
@handle_api_errors
def save_org_contacts(**kwargs):
	user = frappe.session.user
	bank = frappe.db.get_value("User Permission", {"user": user, "allow": "A2C Participating Bank"}, "for_value")
	
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
def upload_kyc_document():
	user = frappe.session.user
	bank = frappe.db.get_value("User Permission", {"user": user, "allow": "A2C Participating Bank"}, "for_value")
	
	if not bank:
		frappe.throw(_("No bank associated with the current user."))
		
	# File upload is handled by Frappe's file manager, we expect filedata in the request
	file_doc = frappe.get_doc({
		"doctype": "File",
		"file_name": frappe.form_dict.get("filename"),
		"content": frappe.form_dict.get("filedata"),
		"attached_to_doctype": "A2C Participating Bank",
		"attached_to_name": bank,
		"attached_to_field": "kyc_document",
		"is_private": 1
	})
	file_doc.insert()
	
	frappe.db.set_value("A2C Participating Bank", bank, "kyc_document", file_doc.file_url)
	
	return success_response(data={"message": _("KYC document uploaded successfully."), "file_url": file_doc.file_url})

# -----------------
# 4. activate_bank
# -----------------
@frappe.whitelist()
@validate_request(ActivateBankSchema)
@handle_api_errors
def activate_bank():
	# Thin helper to let an authorized caller request go-live (e.g. notify sysadmin)
	user = frappe.session.user
	bank = frappe.db.get_value("User Permission", {"user": user, "allow": "A2C Participating Bank"}, "for_value")
	
	if not bank:
		frappe.throw(_("No bank associated with the current user."))
		
	# In a real scenario, this might trigger a workflow or notification to System Manager
	# Currently, the actual flip to 'Active' is done by SysMgr.
	return success_response(data={"message": _("Go-live requested. System Manager will review your application.")})

# -----------------
# 5. invite_user
# -----------------
@frappe.whitelist()
@validate_request(InviteUserSchema)
@handle_api_errors
def invite_user(email: str, full_name: str, role_profile: str):
	user = frappe.session.user
	bank = frappe.db.get_value("User Permission", {"user": user, "allow": "A2C Participating Bank"}, "for_value")
	
	if not bank:
		frappe.throw(_("No bank associated with the current user."))
		
	if not frappe.db.exists("User", email):
		import random, string
		temp_password = "".join(random.choices(string.ascii_letters + string.digits, k=10))
		new_user = frappe.get_doc({
			"doctype": "User",
			"email": email,
			"first_name": full_name,
			"send_welcome_email": 1,
			"new_password": temp_password
		})
		new_user.insert(ignore_permissions=True)
	
	try:
		user_doc = frappe.get_doc("User", email)
		if not any(d.role_profile == role_profile for d in user_doc.user_profiles):
			user_doc.append("user_profiles", {"role_profile": role_profile})
			user_doc.save(ignore_permissions=True)
			
		if not frappe.db.exists("User Permission", {"user": email, "allow": "A2C Participating Bank", "for_value": bank}):
			perm = frappe.get_doc({
				"doctype": "User Permission",
				"user": email,
				"allow": "A2C Participating Bank",
				"for_value": bank,
				"is_default": 1
			})
			perm.insert(ignore_permissions=True)
			
		frappe.db.commit()
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
	bank = frappe.db.get_value("User Permission", {"user": user, "allow": "A2C Participating Bank"}, "for_value")
	
	if not bank:
		frappe.throw(_("No bank associated with the current user."))
		
	target_bank = frappe.db.get_value("User Permission", {"user": email, "allow": "A2C Participating Bank"}, "for_value")
	if target_bank != bank:
		frappe.throw(_("Not permitted to deactivate a user from another bank."))
		
	frappe.db.set_value("User", email, "enabled", 0)
	
	return success_response(data={"message": _("User deactivated successfully.")})

class UpdateUserProfileSchema(BaseModel):
	email: SafeEmail
	full_name: str | None = None
	role_profile: str | None = None

# -----------------
# 7. list_users
# -----------------
@frappe.whitelist()
@handle_api_errors
def list_users():
	user = frappe.session.user
	bank = frappe.db.get_value("User Permission", {"user": user, "allow": "A2C Participating Bank"}, "for_value")
	
	if not bank:
		frappe.throw(_("No bank associated with the current user."))
		
	# Find all users that have a User Permission for this bank
	permissions = frappe.get_all("User Permission", filters={"allow": "A2C Participating Bank", "for_value": bank}, fields=["user"])
	bank_users = [p.user for p in permissions]
	
	users = frappe.get_all("User", filters={"name": ("in", bank_users)}, fields=["name", "email", "first_name", "enabled", "role_profile_name"])
	
	return success_response(data={"users": users})

# -----------------
# 8. update_user_profile
# -----------------
@frappe.whitelist()
@validate_request(UpdateUserProfileSchema)
@handle_api_errors
def update_user_profile(email: str, full_name: str | None = None, role_profile: str | None = None):
	user = frappe.session.user
	bank = frappe.db.get_value("User Permission", {"user": user, "allow": "A2C Participating Bank"}, "for_value")
	
	if not bank:
		frappe.throw(_("No bank associated with the current user."))
		
	target_bank = frappe.db.get_value("User Permission", {"user": email, "allow": "A2C Participating Bank"}, "for_value")
	if target_bank != bank:
		frappe.throw(_("Not permitted to update a user from another bank."))
		
	target_user = frappe.get_doc("User", email)
	
	if full_name:
		target_user.first_name = full_name
		
	if role_profile:
		# check if role profile is already assigned
		if not any(d.role_profile == role_profile for d in target_user.user_profiles):
			# in this simplified version we clear existing and assign new
			target_user.set("user_profiles", [])
			target_user.append("user_profiles", {"role_profile": role_profile})
			
	target_user.save(ignore_permissions=True)
	
	return success_response(data={"message": _("User profile updated successfully.")})
