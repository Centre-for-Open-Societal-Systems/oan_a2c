import frappe


def execute():
	"""Move the lead link from A2C Consent Request.lead to reference_doctype/name.

	The `lead` field was replaced by a polymorphic reference so a consent request
	can also stand alone in the self-service flow, where there is no lead. Frappe
	leaves the old column in place when a field is dropped from the doctype JSON,
	so the data is still readable here and this runs after model sync, once the
	reference fields exist.

	Idempotent: only rows that still have a `lead` value and no reference yet are
	touched, so a re-run is a no-op.
	"""
	if not frappe.db.table_exists("A2C Consent Request"):
		return

	if not frappe.db.has_column("A2C Consent Request", "lead"):
		return

	frappe.db.sql(
		"""
		UPDATE `tabA2C Consent Request`
		SET `reference_doctype` = 'A2C Lead',
			`reference_name` = `lead`
		WHERE ifnull(`lead`, '') != ''
		  AND ifnull(`reference_name`, '') = ''
		"""
	)
