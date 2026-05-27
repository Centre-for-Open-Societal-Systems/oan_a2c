import frappe
from frappe import _


@frappe.whitelist(allow_guest=False)
def lead_inbound(phone_number=None, lead_source="Missed Call", external_ref_id=None, timestamp=None):
	"""
	Automated lead intake from external telco systems (IVR / missed call gateways).

	Authentication: standard Frappe token auth (Authorization: token <key>:<secret>).
	The JWT middleware in oan_a2c.api.middleware skips non-JWT endpoints automatically
	because this endpoint uses Frappe's native API key/secret scheme — no Bearer token needed.

	Idempotency contract (spec §4.4):
	  - Primary Check: By External Reference ID. If a lead already exists with this
	    external reference, we idempotently update it and return success.
	  - Secondary Check: By Phone Number (within active funnel: Open, Initiated).
	    If an active lead exists, we update it rather than duplicating.
	  - Else: Create a fresh lead mapping phone_number and external_id.
	"""
	frappe.has_permission("A2C Lead", "create", throw=True)

	if not phone_number:
		frappe.throw(_("phone_number is required"), frappe.MandatoryError)

	# Validate and sanitize lead_source against permitted Select choices
	allowed_sources = ("Missed Call", "IVR", "SMS", "Agent Entry")
	if lead_source not in allowed_sources:
		lead_source = "Missed Call"

	# 1. Primary Deduplication Check: By External Reference ID
	if external_ref_id:
		existing_by_ref = frappe.db.get_value(
			"A2C Lead",
			{"external_id": external_ref_id},
			"name"
		)
		if existing_by_ref:
			return _update_existing_lead(existing_by_ref, lead_source, external_ref_id, timestamp)

	# 2. Secondary Deduplication Check: By Phone Number (Active funnel)
	active_statuses = ("Open", "Initiated")
	existing_by_phone = frappe.db.get_value(
		"A2C Lead",
		{"phone_number": phone_number, "status": ("in", active_statuses)},
		"name",
	)
	if existing_by_phone:
		return _update_existing_lead(existing_by_phone, lead_source, external_ref_id, timestamp)

	# 3. No match found — create a fresh lead
	new_lead = frappe.new_doc("A2C Lead")
	new_lead.phone_number = phone_number
	new_lead.external_id = external_ref_id
	new_lead.lead_source = lead_source
	new_lead.status = "Open"
	new_lead.call_notes = _build_event_note(lead_source, external_ref_id, timestamp)
	new_lead.insert(ignore_permissions=False)

	return {
		"status": "success",
		"lead_id": new_lead.name,
		"message": "Lead captured successfully.",
	}


def _update_existing_lead(lead_name, lead_source, external_ref_id, timestamp):
	"""Idempotently updates an existing lead's audit/call logs to prevent duplicate entries."""
	existing_doc = frappe.get_doc("A2C Lead", lead_name)

	event_note = _build_event_note(lead_source, external_ref_id, timestamp)
	if existing_doc.call_notes:
		existing_doc.call_notes = existing_doc.call_notes + "\n\n" + event_note
	else:
		existing_doc.call_notes = event_note

	# If the lead did not have an external_id set, populate it now
	if external_ref_id and not existing_doc.external_id:
		existing_doc.external_id = external_ref_id

	existing_doc.save(ignore_permissions=False)

	return {
		"status": "success",
		"lead_id": lead_name,
		"message": "Existing active lead updated with new event.",
	}


def _build_event_note(lead_source, external_ref_id, timestamp):
	"""Formats a single inbound event into a human-readable audit line."""
	parts = [f"Source: {lead_source}"]
	if external_ref_id:
		parts.append(f"Ref ID: {external_ref_id}")
	if timestamp:
		parts.append(f"Timestamp: {timestamp}")
	return " | ".join(parts)
