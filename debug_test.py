import frappe

frappe.init(site="mysite.localhost")
frappe.connect()

doc = frappe.get_doc(
	{
		"doctype": "A2C Loan Product",
		"product_name": "Test Product",
		"bank": "TESTLKPBANKA",
		"min_interest_rate": 5,
		"max_amount": 1000,
		"tenure_months": 12,
		"status": "Draft",
	}
).insert(ignore_permissions=True)

print("Before status change, is_new:", doc.is_new())
print("Status before:", doc.status)
doc.status = "Active"

for f in doc.CONTENT_FIELDS:
	print(
		f"{f} changed?",
		doc.has_value_changed(f),
		repr(doc.get(f)),
		repr(doc.get_doc_before_save().get(f) if doc.get_doc_before_save() else None),
	)

doc.save(ignore_permissions=True)
print("Status after:", doc.status)

frappe.db.rollback()
