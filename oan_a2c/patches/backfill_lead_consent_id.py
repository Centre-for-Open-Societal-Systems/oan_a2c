import frappe


def execute():
	"""Populate A2C Lead.consent_id with each lead's most recent consent attempt.

	`consent_id` is a denormalised cache of the latest A2C Consent Request raised for
	the lead, written by request_otp from here on. Leads created before the field
	existed need it filled once, or get_basic_profile falls back to a sort over the
	consent-request table for the rest of their life.

	Runs after backfill_consent_request_reference, so `reference_name` is already
	populated from the old `lead` column and is the only link this needs to read.

	Note this is the latest *attempt*, not the latest *approved* one -- matching what
	request_otp writes. The Verified gate in a2c_lead.py deliberately does not read
	this field for that reason.

	Idempotent: only leads with no cached value are touched.
	"""
	if not frappe.db.table_exists("A2C Lead") or not frappe.db.table_exists("A2C Consent Request"):
		return

	if not frappe.db.has_column("A2C Lead", "consent_id"):
		return

	frappe.db.sql(
		"""
		UPDATE `tabA2C Lead` lead
		SET lead.`consent_id` = (
			SELECT cr.name
			FROM `tabA2C Consent Request` cr
			WHERE cr.`reference_doctype` = 'A2C Lead'
			  AND cr.`reference_name` = lead.name
			ORDER BY cr.creation DESC
			LIMIT 1
		)
		WHERE ifnull(lead.`consent_id`, '') = ''
		"""
	)
