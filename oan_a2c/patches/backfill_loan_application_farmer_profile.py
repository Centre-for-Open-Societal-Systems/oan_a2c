import frappe


def execute():
	"""Populate the new farmer_profile column on existing applications.

	Every pre-B2C application reached its profile through its lead, so the lead
	is the only source of truth for the backfill. Applications with no lead or
	no profile on the lead are left null and are reported, not guessed at.
	"""
	frappe.db.sql(
		"""
		UPDATE `tabA2C Loan Application` app
		INNER JOIN `tabA2C Lead` lead ON lead.name = app.lead_id
		SET app.farmer_profile = lead.farmer_profile
		WHERE app.farmer_profile IS NULL
		  AND lead.farmer_profile IS NOT NULL
		"""
	)  # bank-scope-exempt: migration over all tenants by design

	orphans = frappe.db.count("A2C Loan Application", {"farmer_profile": ["is", "not set"]})
	if orphans:
		frappe.logger().warning(
			f"backfill_loan_application_farmer_profile: {orphans} application(s) have no "
			"farmer_profile; they are invisible to farmer scoping until repaired."
		)
