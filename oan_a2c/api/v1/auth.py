import frappe


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
	through oan_auth_service's POST /api/v1/auth/password/initial.
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
		"oan_must_change_password": 1 if must_change_password else 0,
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
