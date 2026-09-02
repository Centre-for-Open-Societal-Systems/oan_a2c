import functools

import frappe
from frappe import _

# Role names live in one place (oan_a2c.a2c_marketplace.roles).
from oan_a2c.a2c_marketplace.roles import (
	ADMIN_ROLE,
	BANK_UNBOUND_ROLES,
	DEVELOPMENT_AGENT_ROLE,
	FARMER_ROLE,
)


class BankNotOnboarded(frappe.PermissionError):
	"""Raised when a bank-bound user has no A2C Participating Bank binding yet.

	Subclasses PermissionError so it still fails closed everywhere a plain
	PermissionError would (and stays a 403), but handle_api_errors catches it
	first to return a distinct BANK_NOT_ONBOARDED code + onboarding hint instead
	of an opaque "Permission denied". This is the "role but no org registered"
	case, NOT a cross-bank access attempt.
	"""


class BankNotActive(frappe.PermissionError):
	"""Raised when a bank is registered but not yet Active (In Review/Suspended).

	Subclasses PermissionError so it still fails closed as a 403, but
	handle_api_errors catches it first to return a distinct BANK_NOT_ACTIVE code
	so the client can tell "finish KYC / await approval" apart from a generic
	permission denial. This is the "registered but not approved to trade" case,
	distinct from BankNotOnboarded (no bank binding at all).
	"""


def is_bank_unbound(user=None):
	"""True if the user bypasses bank tenant isolation (sees all banks)."""
	if not user:
		user = frappe.session.user
	return bool(BANK_UNBOUND_ROLES.intersection(frappe.get_roles(user)))


def is_farmer(user=None):
	"""True if the user is a marketplace applicant (scoped by ownership, not bank)."""
	if not user:
		user = frappe.session.user
	return FARMER_ROLE in frappe.get_roles(user)


def is_development_agent(user=None):
	"""True if the user is a Development Agent (platform field agent)."""
	if not user:
		user = frappe.session.user
	return DEVELOPMENT_AGENT_ROLE in frappe.get_roles(user)


def can_see_onboarding_step(user=None) -> bool:
	"""True for farmers and bank-unbound staff (Dev Agents, Platform Admins).

	Bank-scoped users (Bank Admins, Bank Agents) only review submitted applications
	and do not see the farmer's private onboarding wizard step.
	"""
	if not user:
		user = frappe.session.user
	return is_bank_unbound(user) or is_farmer(user)


def get_user_farmer_profile(user=None):
	"""The A2C Farmer Profile bound to `user`, or None before consent binds one.

	A registered farmer who has never completed a consent has no profile. That is
	a normal state, not an error: it means they have nothing to see yet.

	Deliberately NOT @frappe.request_cache, unlike get_user_bank below. Two reasons:

	  1. A farmer's profile can come into existence *during* a request -- the consent
	     flow creates it, and the claim branch below binds it. A cached `None` from an
	     earlier call in the same request would then make every later scope check
	     resolve to `1=0`, and the farmer would see nothing until their next request.
	  2. This function writes (the claim below). Caching a function with a side
	     effect hides whether the write happened, and this one runs from permission
	     hooks that fire many times per request.

	The reads it performs are single indexed lookups, which is the right price for
	always being correct about what the caller can see.
	"""
	if not user:
		user = frappe.session.user
	profile = frappe.db.get_value("A2C Farmer Profile", {"user": user}, "name")
	if not profile and user and is_farmer(user) and user != "Administrator":
		latest_consent = frappe.db.get_value(
			"A2C Consent Request",
			{"owner": user, "status": "Approved"},
			"name",
			order_by="creation desc",
		)
		if latest_consent:
			profile = frappe.db.get_value("A2C Farmer Profile", {"consent_id": latest_consent}, "name")
			if profile:
				# Never claim a profile already bound to another account: `user` scopes
				# loan-application visibility, so overwriting it would transfer that
				# account's applications to this one.
				bound_to = frappe.db.get_value("A2C Farmer Profile", profile, "user")
				if bound_to and bound_to != user:
					profile = None
				elif not frappe.flags.read_only:
					# The binding is a repair, not a precondition: `profile` is already
					# resolved and returned either way. Skipping the write on a read-only
					# connection (replica reads) therefore costs nothing but a repeat of
					# this lookup next request, whereas attempting it would raise.
					frappe.db.set_value("A2C Farmer Profile", profile, "user", user, update_modified=False)
	return profile


@frappe.request_cache
def get_user_bank(user=None):
	"""
	Returns the bank_code (A2C Participating Bank) bound to the user.
	Uses User Permission where allow="A2C Participating Bank".
	"""
	if not user:
		user = frappe.session.user

	# Platform admins / System Managers are unbound and see all
	if is_bank_unbound(user):
		return None

	permissions = frappe.get_all(
		"User Permission", filters={"user": user, "allow": "A2C Participating Bank"}, fields=["for_value"]
	)

	if permissions:
		return permissions[0].for_value

	return None


def get_bank_members(bank, roles=None):
	"""Inverse of get_user_bank: users bound to `bank`, optionally filtered by role.

	get_user_bank answers "which bank does this user belong to?"; notifications need
	the other direction — "which users belong to this bank?". Both read the same
	source of truth: User Permission where allow="A2C Participating Bank" (here
	filtered by for_value=bank instead of by user).

	When `roles` is given (e.g. BANK_ROLES), members are narrowed to those holding at
	least one of those roles, so a stale User Permission on a user who no longer has a
	bank role does not receive notifications.
	"""
	if not bank:
		return []

	users = frappe.get_all(
		"User Permission",
		filters={"allow": "A2C Participating Bank", "for_value": bank},
		pluck="user",
	)
	if roles:
		role_set = set(roles)
		users = [u for u in users if role_set.intersection(frappe.get_roles(u))]
	return users


def bank_filters(user=None, base=None):
	"""
	Return a filters dict scoped to the caller's bank, for use with
	frappe.get_all / frappe.db.get_all on bank-scoped DocTypes.

	frappe.get_all bypasses the permission_query_conditions hook
	(bank_scope_query), so any endpoint that uses it must apply this helper
	to stay isolated. This is the single source of truth for that scoping and
	fails closed, exactly like bank_scope_query:

	  - unbound admin    -> no bank filter added (sees all)
	  - user with a bank -> bank filter added
	  - user with none   -> raises frappe.PermissionError (never returns all)

	`base` is merged first so callers can add their own filters. Supports both
	filter forms frappe.get_all accepts:
	  - dict:  bank_filters(base={"status": "Active"})  -> {..., "bank": <bank>}
	  - list:  bank_filters(base=[["status", "=", "Active"]])
	           -> [..., ["bank", "=", <bank>]]
	"""
	is_list = isinstance(base, (list, tuple))
	filters = list(base) if is_list else dict(base or {})

	if not user:
		user = frappe.session.user

	if is_bank_unbound(user):
		return filters

	bank = get_user_bank(user)
	if not bank:
		# Rare: a non-admin user with no bank binding is an onboarding/config bug.
		# log_error surfaces it in the Error Log UI so ops notice; low volume, so
		# the DB write cost is acceptable here (unlike per-request deny logging).
		frappe.log_error(
			title="Bank scope: user without bank binding",
			message=f"user={user} hit a bank-scoped query with no A2C Participating Bank User Permission.",
		)
		frappe.throw(_("No bank is assigned to this user."), BankNotOnboarded)

	if is_list:
		filters.append(["bank", "=", bank])
	else:
		filters["bank"] = bank
	return filters


def require_bank_role(role: str):
	"""Decorator that enforces the caller holds `role`. Must sit below @handle_api_errors."""

	def decorator(fn):
		@functools.wraps(fn)
		def wrapper(*args, **kwargs):
			user_doc = frappe.get_doc("User", frappe.session.user)
			if not any(d.role == role for d in user_doc.roles):
				frappe.throw(_("Only {0}s can perform this action.").format(role), frappe.PermissionError)
			return fn(*args, **kwargs)

		return wrapper

	return decorator


def bank_scoped(func=None, *, require_bank=True):
	"""Resolve the caller's bank scope, fail closed, and inject it as `bank`.

	Must be the innermost decorator (below @handle_api_errors).

	Resolution:
	  - unbound admin -> bank=None (rejected if require_bank=True).
	  - non-admin with a bank -> bank=<code>.
	  - non-admin with no binding -> rejected (fail closed).
	"""

	def decorator(f):
		@functools.wraps(f)
		def wrapper(*args, **kwargs):
			# Never trust a client-supplied `bank`; we resolve it from the session.
			kwargs.pop("bank", None)

			if is_bank_unbound():
				if require_bank:
					# Not an onboarding problem: an unbound admin has no single bank
					# to act as. Plain permission denial.
					frappe.throw(_("Select a specific bank to perform this action."), frappe.PermissionError)
				bank = None
			else:
				bank = get_user_bank()
				if not bank:
					frappe.throw(_("No bank is assigned to this user."), BankNotOnboarded)

			return f(*args, bank=bank, **kwargs)

		return wrapper

	if func is not None:
		return decorator(func)
	return decorator


def bank_scope_query(user):
	"""
	Query hook for bank-scoped DocTypes.
	Returns the SQL condition for `tab{doctype}`.
	"""
	if not user:
		user = frappe.session.user

	if is_bank_unbound(user):
		return ""  # Sees all

	bank = get_user_bank(user)

	if not bank:
		# User has no bank, fail closed
		return "1=0"

	# Important: In frappe query hooks, the condition shouldn't contain `tab{doctype}` hardcoded if it can be dynamic.
	# Sometimes we need to use a generic condition or just the fieldname.
	# Just `bank = 'value'` is usually sufficient if joined correctly, but safe way is with backticks.
	return f"`bank` = {frappe.db.escape(bank)}"


# "Self Service" marks an application a farmer raised through the B2C flow, as
# opposed to one a Development Agent raised in the CRM. Kept as one constant used
# by both the query hook and the single-doc hook below, so the two can't drift.
SELF_SERVICE = "Self Service"
AGENT_SOURCED_ONLY = f"ifnull(`application_source`, 'Agent') != '{SELF_SERVICE}'"


def is_platform_admin(user=None) -> bool:
	"""True for the operators of the platform itself, who are exempt from every
	scope narrowing below -- as opposed to Development Agents, who are merely
	bank-unbound."""
	if not user:
		user = frappe.session.user
	roles = frappe.get_roles(user)
	return user == "Administrator" or "System Manager" in roles or ADMIN_ROLE in roles


def loan_application_scope_query(user=None):
	if not user:
		user = frappe.session.user

	# Farmers are scoped by ownership, not by bank. Checked first: a farmer has no
	# A2C Participating Bank binding, so bank_scope_query would fail them closed at
	# 1=0. No Draft gate here -- Draft is the farmer's own working stage (unlike a
	# bank user, for whom Draft is the Development Agent's private stage).
	if is_farmer(user) and not is_bank_unbound(user):
		profile = get_user_farmer_profile(user)
		if not profile:
			return "1=0"
		# Scope on farmer_profile alone, not on `owner`. The profile is 1:1 with the
		# user, so it is already the object-level key -- and an application a
		# Development Agent raised *for* this farmer is owned by the agent. Adding
		# `owner` would hide the farmer's own agent-assisted applications from them.
		return f"`farmer_profile` = {frappe.db.escape(profile)}"

	base = bank_scope_query(user)

	if is_bank_unbound(user):
		if is_platform_admin(user):
			return base
		# Development Agents run the agent-operated CRM pipeline; self-service
		# applications a farmer raised directly are not theirs to work. Filtered on
		# an indexed column on the row itself -- never by materialising the set of
		# farmer users into a NOT IN, which grows with the user base and cannot use
		# an index.
		return f"({base}) and {AGENT_SOURCED_ONLY}" if base else AGENT_SOURCED_ONLY

	# Bank users (Bank Admin, Bank Agent) DO see self-service applications, but only
	# once the workflow moves them past the Active stage (which is farmer's private).
	status_gate = "`status` != 'Active'"
	return f"({base}) and {status_gate}" if base else status_gate


def loan_product_scope_query(user=None):
	"""permission_query_conditions for A2C Loan Product.

	Farmers and Development Agents browse the marketplace across every bank
	but only ever see Active products -- Draft, Pending Approval, Rejected and Archived
	belong to the owning bank's catalog workspace.
	Platform admins see all products across all banks and statuses.
	Everyone else keeps plain bank scoping.
	"""
	if not user:
		user = frappe.session.user

	if is_platform_admin(user):
		return ""

	if is_farmer(user) or is_development_agent(user):
		return "`status` = 'Active'"

	return bank_scope_query(user)


def farmer_own_profile_query(user=None):
	"""permission_query_conditions for A2C Farmer Profile."""
	if not user:
		user = frappe.session.user
	if not is_farmer(user) or is_bank_unbound(user):
		return ""
	profile = get_user_farmer_profile(user)
	return f"`name` = {frappe.db.escape(profile)}" if profile else "1=0"


def farmer_own_consent_query(user=None):
	"""permission_query_conditions for A2C Consent Request.

	Scoped on `owner` -- the user who called request_otp and opened the request.
	Deliberately NOT on the `farmer` field: that holds the *OpenG2P* farmer id
	returned by Fayda, not an A2C Farmer Profile name, so comparing it to a profile
	matches nothing and would silently fail open into "farmer sees no consents".
	"""
	if not user:
		user = frappe.session.user
	if not is_farmer(user) or is_bank_unbound(user):
		return ""
	return f"`owner` = {frappe.db.escape(user)}"


def bank_scope_doc(doc, user=None):
	"""
	has_permission doc hook.
	Returns True if allowed, False otherwise.
	"""
	if not user:
		user = frappe.session.user

	if is_bank_unbound(user):
		if is_platform_admin(user):
			return True
		# Mirror of AGENT_SOURCED_ONLY for single-doc reads: a Development Agent
		# does not work self-service applications.
		if doc.doctype == "A2C Loan Application":
			return (doc.get("application_source") or "Agent") != SELF_SERVICE
		# Development Agents browse products across banks, but only Active products.
		if doc.doctype == "A2C Loan Product":
			return doc.get("status") == "Active"
		return True

	# Mirror of the query hooks above, for single-doc reads (get_doc, has_permission).
	if is_farmer(user):
		if doc.doctype == "A2C Loan Product":
			return doc.get("status") == "Active"
		if doc.doctype == "A2C Loan Application":
			profile = get_user_farmer_profile(user)
			allowed = bool(profile) and doc.get("farmer_profile") == profile
			if not allowed:
				frappe.logger("bank_scope").warning(
					f"Denied cross-farmer access: user={user} profile={profile} "
					f"{doc.doctype}={doc.name} doc_profile={doc.get('farmer_profile')}"
				)
			return allowed
		return False

	bank = get_user_bank(user)
	allowed = bool(bank) and doc.bank == bank

	if allowed and doc.doctype == "A2C Loan Application":
		# Lifecycle gate: Bank users can't read applications until the workflow marks them visible.
		if doc.get("status") == "Active":
			frappe.logger("bank_scope").info(
				f"Denied hidden loan application to bank user: user={user} {doc.doctype}={doc.name}"
			)
			return False

	if not allowed:
		# Potentially high volume (probing), so use the file logger, not log_error:
		# cheap and won't bloat the Error Log / DB. Ship logs/ to your aggregator
		# in production to make this an audit trail.
		frappe.logger("bank_scope").warning(
			f"Denied cross-bank access: user={user} bank={bank} "
			f"{doc.doctype}={doc.name} doc_bank={doc.get('bank')}"
		)

	return allowed


def saved_product_own_query(user=None):
	"""permission_query_conditions for A2C Saved Product.

	Bookmarking is open to every role that browses the catalog -- farmers, bank
	users and development agents alike -- so row visibility is the only thing
	standing between one user's saved list and another's. Platform admins keep the
	unscoped view for support.
	"""
	if not user:
		user = frappe.session.user
	if is_platform_admin(user):
		return ""
	return f"`user` = {frappe.db.escape(user)}"


def saved_product_own_doc(doc, ptype=None, user=None):
	"""has_permission for A2C Saved Product -- the doc-level twin of the query above.

	permission_query_conditions only fires on list queries, so on its own it leaves
	`frappe.client.get("A2C Saved Product", "<name>")` open to anyone holding read
	DocPerm: the row would be another user's bookmark. Writes are already safe (the
	controller stamps `user` from the session), but reads and deletes by name need
	this to make "you only see what you saved" true outside the API layer too.
	"""
	# `create` is checked before validate() stamps `user`, so the row is still
	# blank here -- and the controller forces it to the session user anyway,
	# which makes creating into someone else's list impossible regardless.
	if ptype == "create":
		return True
	if not user:
		user = frappe.session.user
	if is_platform_admin(user):
		return True
	return doc.get("user") == user


def farmer_own_profile_doc(doc, ptype=None, user=None):
	if not user:
		user = frappe.session.user
	if is_platform_admin(user):
		return True
	if not is_farmer(user) or is_bank_unbound(user):
		return True  # Non-farmers (like bank agents) rely on base Role Permissions Manager
	profile = get_user_farmer_profile(user)
	return bool(profile) and doc.name == profile


def farmer_own_consent_doc(doc, ptype=None, user=None):
	if not user:
		user = frappe.session.user
	if is_platform_admin(user):
		return True
	if not is_farmer(user) or is_bank_unbound(user):
		return True
	return doc.owner == user
