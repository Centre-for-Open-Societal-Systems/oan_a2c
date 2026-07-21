import frappe

def get_user_bank(user=None):
	"""
	Returns the bank_code (A2C Participating Bank) bound to the user.
	Uses User Permission where allow="A2C Participating Bank".
	"""
	if not user:
		user = frappe.session.user
	
	# System Manager is unbound and sees all
	if "System Manager" in frappe.get_roles(user):
		return None
		
	permissions = frappe.get_all(
		"User Permission",
		filters={"user": user, "allow": "A2C Participating Bank"},
		fields=["for_value"]
	)
	
	if permissions:
		return permissions[0].for_value
		
	return None

def bank_scope_query(user):
	"""
	Query hook for bank-scoped DocTypes.
	Returns the SQL condition for `tab{doctype}`.
	"""
	if not user:
		user = frappe.session.user

	if "System Manager" in frappe.get_roles(user):
		return "" # Sees all
		
	bank = get_user_bank(user)
	
	if not bank:
		# User has no bank, fail closed
		return "1=0"
		
	# Important: In frappe query hooks, the condition shouldn't contain `tab{doctype}` hardcoded if it can be dynamic.
	# Sometimes we need to use a generic condition or just the fieldname.
	# Actually, standard frappe condition is just table alias or backticked field.
	# Let's use `tab{doctype}`.bank safely if possible.
	doctype = frappe.flags.current_doctype or frappe.as_dict().get("doctype")
	# Just `bank = 'value'` is usually sufficient if joined correctly, but safe way is with backticks.
	return f"`bank` = {frappe.db.escape(bank)}"

def bank_scope_doc(doc, user=None):
	"""
	has_permission doc hook.
	Returns True if allowed, False otherwise.
	"""
	if not user:
		user = frappe.session.user
		
	if "System Manager" in frappe.get_roles(user):
		return True
		
	bank = get_user_bank(user)
	
	if not bank:
		return False
		
	return doc.bank == bank
