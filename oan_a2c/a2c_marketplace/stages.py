import frappe
from frappe import _


def resolve_bank_stage(bank, status_or_stage):
	"""
	Given a string that might be an archetype state or a bank stage (by id, external code, or label),
	return a dict with 'archetype_state' and 'stage_id' (if resolved to a specific stage).
	"""
	# Direct match for archetype states
	archetypes = ["Active", "In Transition", "Completed", "Cancelled"]
	if status_or_stage in archetypes:
		return {"archetype_state": status_or_stage, "stage_id": None, "stage_label": status_or_stage}

	# Otherwise try to match a bank stage
	stage = frappe.db.get_all(
		"A2C Loan Status Stage",
		filters={"bank": bank},
		or_filters={"stage_id": status_or_stage, "external_code": status_or_stage, "label": status_or_stage},
		fields=["stage_id", "label", "archetype_state"],
		limit=1,
	)

	if stage:
		return {
			"archetype_state": stage[0].archetype_state,
			"stage_id": stage[0].stage_id,
			"stage_label": stage[0].label,
		}

	# Invalid stage
	frappe.throw(
		_("Invalid status or stage '{0}' for bank {1}").format(status_or_stage, bank), frappe.ValidationError
	)


def get_initial_pipeline_stage(bank):
	"""Returns the first stage for 'In Transition' for the given bank, or None."""
	stage = frappe.db.get_all(
		"A2C Loan Status Stage",
		filters={"bank": bank, "archetype_state": "In Transition"},
		fields=["stage_id", "label", "archetype_state"],
		order_by="sequence asc, creation asc",
		limit=1,
	)
	return stage[0] if stage else None
