import frappe


def execute():
	"""Drop what auth used before it moved to oan_auth_service.

	- The `A2C User Refresh Token` doctype. Refresh tokens now live in
	  `OAN User Refresh Token`; sessions held in the old table are not carried over.
	- The `User.a2c_must_change_password` custom field, replaced by
	  `oan_must_change_password` (copied by `migrate_must_change_password`, which runs first).

	Idempotent.
	"""
	if frappe.db.exists("DocType", "A2C User Refresh Token"):
		frappe.delete_doc("DocType", "A2C User Refresh Token", force=True, ignore_permissions=True)

	field = "User-a2c_must_change_password"
	if frappe.db.exists("Custom Field", field):
		frappe.delete_doc("Custom Field", field, force=True, ignore_permissions=True)
