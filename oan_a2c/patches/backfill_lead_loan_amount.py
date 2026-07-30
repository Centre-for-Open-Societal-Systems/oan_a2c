"""Backfill A2C Lead.loan_amount from existing A2C Credit Information rows.

loan_amount was added to A2C Lead as a denormalized snapshot so the leads list
can sort/filter by amount at the DB layer. New/updated credit info stays in sync
via doc hooks (sync_lead_loan_amount); this patch seeds the value for leads that
already had credit info before the field existed.

Leads are effectively 1:1 with Credit Information; if any lead has more than one,
the most recently created credit row wins (mirrors the "latest" reading).
"""

import frappe


def execute():
	# Latest credit info per lead (ORDER so the last write per lead wins the dict).
	rows = frappe.get_all(
		"A2C Credit Information",
		fields=["lead", "loan_amount"],
		order_by="creation asc",
	)
	latest = {r.lead: r.loan_amount for r in rows if r.lead}

	for lead, amount in latest.items():
		if not frappe.db.exists("A2C Lead", lead):
			continue
		frappe.db.set_value("A2C Lead", lead, "loan_amount", amount or 0, update_modified=False)
