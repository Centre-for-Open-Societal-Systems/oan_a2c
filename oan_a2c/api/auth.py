from typing import Literal

import frappe
from frappe import _
from pydantic import BaseModel, Field, field_validator

from oan_a2c.a2c_marketplace.roles import (
	ADMIN_ROLE,
	BANK_ADMIN_ROLE,
	BANK_AGENT_ROLE,
	BANK_ROLES,
	DEVELOPMENT_AGENT_ROLE,
	FARMER_ROLE,
)
from oan_a2c.api.router import prefixed
from oan_a2c.api.utils import (
	check_rate_limit,
	handle_api_errors,
	success_response,
	validate_password_complexity,
	validate_request,
)

me_route = prefixed("/api/v1/me")


class UpdateProfileSchema(BaseModel):
	full_name: str | None = Field(default=None)
	phone_number: str | None = Field(default=None)
	language: str | None = Field(default=None)
	user_image: str | None = Field(default=None)
	gender: (
		Literal[
			"Male",
			"Female",
			"Other",
			"Transgender",
			"Genderqueer",
			"Non-Conforming",
			"Prefer not to say",
		]
		| None
	) = Field(default=None)


class ChangePasswordSchema(BaseModel):
	current_password: str = Field(..., min_length=1)
	new_password: str = Field(..., min_length=8, max_length=64)

	@field_validator("new_password")
	@classmethod
	def validate_new_password(cls, v: str) -> str:
		return validate_password_complexity(v)


def _classify_user_type(roles: list[str]) -> str:
	"""Map a user's role list to a single portal kind string.

	Priority mirrors BANK_UNBOUND_ROLES in roles.py: bank-scoped roles are
	checked first so a user who holds both Bank Admin and Administrator is
	treated as bank_admin (the more specific, narrower identity).
	"""
	role_set = set(roles)
	if BANK_ADMIN_ROLE in role_set:
		return "bank_admin"
	if BANK_AGENT_ROLE in role_set:
		return "bank_agent"
	if DEVELOPMENT_AGENT_ROLE in role_set:
		return "dev_agent"
	if ADMIN_ROLE in role_set:
		return "marketplace"
	if FARMER_ROLE in role_set:
		return "farmer"
	return "unknown"


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


def require_sso_for_development_agents(user: str) -> str | None:
	"""`revocation_check` for the a2c API namespaces: Development Agents sign in only through SSO.

	A Development Agent account that was not provisioned by the OpenG2P IAM service
	(an old local account with a password) is refused, whatever token it holds.
	IAM-provisioned users carry their roles from IAM, so the role alone decides.
	"""
	from oan_auth_service.api.iam_users import is_delegated_user

	if is_delegated_user(user):
		return None
	if DEVELOPMENT_AGENT_ROLE in frappe.get_roles(user):
		return "Development Agents must sign in through SSO"
	return None


def access_token_claims(user: str, roles: list[str]) -> dict:
	"""`on_access_token_claims` hook: put the user type in every access token.

	The frontend decides which portal a user may see from this claim without a
	network call, so it has to travel in the token.
	"""
	return {"user_type": _classify_user_type(roles)}


def on_user_profile(user_doc, roles: list[str]) -> tuple[str, dict]:
	"""`on_user_profile` hook for oan_auth_service's `GET /api/v1/auth/me`.

	Adds the a2c-specific identity fields (user type and bank binding) under
	`data.profiles.a2c`.
	"""
	data = {"user_type": _classify_user_type(roles)}
	if any(r in BANK_ROLES for r in roles):
		data.update(_get_user_bank_context(user_doc.name))
	return "a2c", data


@me_route("/profile", methods=("GET",), summary="Get user profile")
@handle_api_errors
def get_user_profile():
	"""
	Returns detailed profile information for the profile screen,
	keeping the main get_me endpoint lightweight.
	"""
	if frappe.session.user == "Guest":
		frappe.throw(_("Not permitted"), frappe.AuthenticationError)

	user = frappe.get_doc("User", frappe.session.user)
	roles = [d.role for d in user.roles]

	has_bank_role = any(r in BANK_ROLES for r in roles)
	bank_context = {
		"bank_name": None,
	}
	if has_bank_role:
		bank_context = _get_user_bank_context(frappe.session.user)

	def _get_user_role_label(user_roles):
		role_set = set(user_roles)
		if BANK_ADMIN_ROLE in role_set:
			return "Bank Admin"
		if BANK_AGENT_ROLE in role_set:
			return "Bank Agent"
		if DEVELOPMENT_AGENT_ROLE in role_set:
			return "Development Agent"
		if ADMIN_ROLE in role_set:
			return "Marketplace Admin"
		if FARMER_ROLE in role_set:
			return "Farmer"
		return user_roles[0] if user_roles else "User"

	user_role_label = _get_user_role_label(roles)
	organization = bank_context.get("bank_name") or "OpenAgriNet"
	employee_id = getattr(user, "employee_id", None) or getattr(user, "employee", None) or user.name

	member_since = ""
	if getattr(user, "creation", None):
		try:
			member_since = user.creation.strftime("%B %Y")
		except Exception:
			pass

	return success_response(
		data={
			"personal_information": {
				"user_image": getattr(user, "user_image", None),
				"full_name": user.full_name,
				"email_address": user.email,
				"gender": getattr(user, "gender", None),
				"phone_number": getattr(user, "mobile_no", None) or getattr(user, "phone", None),
				"language": getattr(user, "language", None) or "English",
			},
			"account_information": {
				"user_role": user_role_label,
				"organization": organization,
				"employee_id": employee_id,
				"member_since": member_since,
			},
		}
	)


# Clients send human-readable English language names (e.g. "Swahili"), but the
# Frappe Language doctype is keyed by ISO code ("sw") and stores the *native*
# name ("Kiswahili"), so a raw `user.language = "Swahili"` fails the Link check.
# These aliases map the common English exonyms to their Language codes.
_LANGUAGE_ALIASES = {
	"english": "en",
	"amharic": "am",
	"swahili": "sw",
	"oromo": "om",
	"afaan oromo": "om",
	"oromiffa": "om",
	"tigrinya": "ti",
	"tigrigna": "ti",
}


def _resolve_language(value):
	"""Resolve a client-supplied language to a valid Language code.

	Accepts the ISO code (the Link key, e.g. 'sw'), the stored native
	language_name (e.g. 'Kiswahili'), or a common English exonym ('Swahili').
	Returns None for an empty value (clears the field). Raises a clear
	ValidationError if the language cannot be resolved to an existing record.
	"""
	raw = (value or "").strip()
	if not raw:
		return None

	# 1. Exact Language code (the Link key), e.g. 'sw'.
	if frappe.db.exists("Language", raw):
		return raw
	# 2. Stored native language_name, e.g. 'Kiswahili'.
	by_name = frappe.db.get_value("Language", {"language_name": raw}, "name")
	if by_name:
		return by_name
	# 3. Common English exonym, e.g. 'Swahili' -> 'sw'.
	code = _LANGUAGE_ALIASES.get(raw.lower())
	if code and frappe.db.exists("Language", code):
		return code

	frappe.throw(
		_(
			"Unsupported language '{0}'. Provide a valid language code (e.g. 'en', 'am', 'sw') or name."
		).format(raw),
		frappe.ValidationError,
	)


def _user_owned_file(file_url: str | None, user: str) -> str | None:
	"""File name if `user` uploaded it, else None.

	Mirrors ``_bank_owned_file`` in api/v1/seller/onboarding.py. ``user_image`` arrives
	as an arbitrary caller-supplied string, so it must never be trusted as a reference
	to adopt or delete: without this check a user can point ``user_image`` at any
	``file_url`` in the system -- including /private/files KYC, loan and consent
	documents -- and have the next profile update delete it.
	"""
	if not file_url:
		return None
	row = frappe.db.get_value("File", {"file_url": file_url}, ["name", "owner"], as_dict=True)
	if not row or row.owner != user:
		return None
	return row.name


@me_route("/profile", methods=("PATCH",), summary="Update user profile")
@validate_request(UpdateProfileSchema)
@handle_api_errors
def update_profile(
	full_name: str | None = None,
	phone_number: str | None = None,
	language: str | None = None,
	user_image: str | None = None,
	gender: str | None = None,
):
	"""
	Updates the authenticated user's profile details.

	Note on Image Uploads: The client should first upload the image via POST /v1/images
	and pass the returned `file_url` here as `user_image`. (Frappe's own
	/api/method/upload_file is not an option: it sits outside the /v1 namespace the JWT
	middleware covers, so a Bearer-token caller is treated as Guest there.) The URL must
	belong to a file this user uploaded -- any other reference is rejected with 403.
	Pass an empty string to clear the avatar.
	"""
	if frappe.session.user == "Guest":
		frappe.throw(_("Not permitted"), frappe.AuthenticationError)

	user = frappe.get_doc("User", frappe.session.user)

	if full_name is not None:
		user.first_name = full_name.strip()
		user.last_name = ""
	if phone_number is not None:
		user.mobile_no = phone_number.strip()
	if language is not None:
		user.language = _resolve_language(language)
	if gender is not None:
		user.gender = gender.strip()
	if user_image is not None:
		user_image = user_image.strip()
		# An empty string clears the avatar; any other value must name a file this user
		# actually uploaded, or it is a reference to someone else's document.
		if user_image and not _user_owned_file(user_image, frappe.session.user):
			frappe.throw(_("Invalid image reference."), frappe.PermissionError)
		if user.user_image and user.user_image != user_image:
			# Ownership-gated as well, so a previously stored hostile value cannot be
			# used to delete another user's file.
			old_file = _user_owned_file(user.user_image, frappe.session.user)
			if old_file:
				frappe.delete_doc("File", old_file, ignore_permissions=True, force=True)
		user.user_image = user_image

	# Note on ignore_permissions: We use this because giving users global "Write"
	# access to the User DocType is a security risk. By ignoring permissions here,
	# we securely allow users to update ONLY their own specific allowed profile
	# fields (name, phone, language, photo) without granting them raw table permissions.
	user.save(ignore_permissions=True)

	return get_user_profile()


@me_route("/password", methods=("PATCH",), summary="Change password")
@validate_request(ChangePasswordSchema)
@handle_api_errors
def change_password(current_password: str, new_password: str):
	if frappe.session.user == "Guest":
		frappe.throw(_("Not permitted"), frappe.AuthenticationError)

	check_rate_limit(f"rl:change_pwd:{frappe.session.user}", limit=5, window=300)

	from frappe.utils.password import check_password
	from frappe.utils.password import update_password as update_password_db

	try:
		check_password(frappe.session.user, current_password)
	except frappe.exceptions.AuthenticationError:
		frappe.clear_messages()
		raise frappe.AuthenticationError(_("Current password is incorrect."))

	update_password_db(user=frappe.session.user, pwd=new_password, logout_all_sessions=False)

	return success_response(message=_("Password changed successfully."))
