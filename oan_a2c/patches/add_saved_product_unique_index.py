import frappe

def execute():
	"""
	Add a unique index to A2C Saved Product on (farmer_profile, loan_product)
	to prevent duplicate bookmarks.
	"""
	if not frappe.db.table_exists("A2C Saved Product"):
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
