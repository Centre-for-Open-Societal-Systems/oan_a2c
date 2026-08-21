import frappe

def execute():
	default_stages = [
		{"label": "Submitted", "archetype_state": "In Transition", "sequence": 1},
		{"label": "Processed", "archetype_state": "In Transition", "sequence": 2},
		{"label": "Verified", "archetype_state": "In Transition", "sequence": 3},
		{"label": "Approved", "archetype_state": "In Transition", "sequence": 4},
		{"label": "Disbursed", "archetype_state": "Completed", "sequence": 5},
		{"label": "Rejected", "archetype_state": "Completed", "sequence": 6},
	]
	
	for bank in frappe.get_all("A2C Participating Bank", pluck="name"):
		if not frappe.db.exists("A2C Loan Status Stage", {"bank": bank}):
			for stage in default_stages:
				frappe.get_doc(
					{
						"doctype": "A2C Loan Status Stage",
						"bank": bank,
						"label": stage["label"],
						"archetype_state": stage["archetype_state"],
						"sequence": stage["sequence"],
					}
				).insert(ignore_permissions=True)
