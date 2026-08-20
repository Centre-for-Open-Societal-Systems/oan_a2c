import frappe


def execute():
	"""
	Add a unique index to A2C Saved Product on (farmer_profile, loan_product)
	to prevent duplicate bookmarks.
	"""
	if not frappe.db.table_exists("A2C Saved Product"):
		return

	# Superseded by rekey_saved_product_on_user, which re-keys this doctype onto
	# User and swaps this index for one on (user, loan_product). Kept because
	# patches.txt is append-only, and guarded so it is a no-op once that has run.
	if not frappe.db.has_column("A2C Saved Product", "farmer_profile"):
		return

	# Add a unique index. Check if it already exists first.
	# Frappe doesn't have a direct helper for multi-column unique indices on doctypes from JSON,
	# so we usually add them via ALTER TABLE.
	try:
		frappe.db.sql("""
			ALTER TABLE `tabA2C Saved Product`
			ADD UNIQUE INDEX `idx_unique_saved_product` (`farmer_profile`, `loan_product`)
		""")
	except Exception as e:
		if "Duplicate key name" not in str(e):
			raise
