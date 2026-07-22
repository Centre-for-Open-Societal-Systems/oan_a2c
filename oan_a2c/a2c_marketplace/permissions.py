import frappe
from frappe import _

# Platform-level admin that sits above Bank Admin / Bank Agent and is unbound by
# bank tenant isolation (sees all banks). Created by the
# create_marketplace_admin_role patch.
MARKETPLACE_ADMIN_ROLE = "A2C Marketplace Admin"

# Roles exempt from bank scoping. System Manager is kept alongside the app role
# so that a plain site admin (not necessarily the Administrator user, who bypasses
# everything in Frappe core) is never locked out of data for support/migrations.
BANK_UNBOUND_ROLES = {MARKETPLACE_ADMIN_ROLE, "System Manager"}


def is_bank_unbound(user=None):
	"""True if the user bypasses bank tenant isolation (sees all banks)."""
	if not user:
		user = frappe.session.user
	return bool(BANK_UNBOUND_ROLES.intersection(frappe.get_roles(user)))


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
		frappe.throw(_("No bank is assigned to this user."), frappe.PermissionError)

	if is_list:
		filters.append(["bank", "=", bank])
	else:
		filters["bank"] = bank
	return filters


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


def bank_scope_doc(doc, user=None):
	"""
	has_permission doc hook.
	Returns True if allowed, False otherwise.
	"""
	if not user:
		user = frappe.session.user

	if is_bank_unbound(user):
		return True

	bank = get_user_bank(user)

	if not bank:
		return False

	return doc.bank == bank
