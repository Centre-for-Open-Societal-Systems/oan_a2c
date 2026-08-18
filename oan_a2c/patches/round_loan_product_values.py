import frappe


def execute():
	"""Round min_interest_rate, max_interest_rate, min_amount, and max_amount to 2 decimal places for all A2C Loan Products."""
	frappe.reload_doc("a2c_marketplace", "doctype", "a2c_loan_product")

	products = frappe.get_all(  # bank-scope-exempt: running as patch
		"A2C Loan Product",
		fields=["name", "min_interest_rate", "max_interest_rate", "min_amount", "max_amount"],
	)

	count = 0
	for product in products:
		needs_update = False
		update_dict = {}

		for field in ["min_interest_rate", "max_interest_rate", "min_amount", "max_amount"]:
			val = product.get(field)
			if val is not None:
				rounded_val = round(float(val), 2)
				if val != rounded_val:
					update_dict[field] = rounded_val
					needs_update = True

		if needs_update:
			frappe.db.set_value("A2C Loan Product", product.name, update_dict, update_modified=False)
			count += 1

	frappe.db.commit()
	print(f"Rounded values to 2 decimal places for {count} Loan Products.")
