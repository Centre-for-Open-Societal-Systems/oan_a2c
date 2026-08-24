import frappe
from frappe import _

# The archetype states, in lifecycle order. Platform constants: a bank names the
# stages *inside* `In Transition`, it never renames these. Anything reporting on
# loan state should bucket by these rather than by a stage label, which is
# tenant-defined free text and differs between banks.
#
# `Rejected` is a first-class archetype, deliberately separate from `Completed`:
# the workflow carries In Transition -> Rejected transitions for both Bank Agent
# and Bank Admin, and the legacy-status backfill maps a declined loan to
# `Rejected` rather than folding it into `Completed`. Keep them apart -- a
# disbursed loan and a declined one are not the same outcome, and collapsing them
# is one-way: nothing downstream can recover the distinction afterwards.
#
# `Cancelled` has no transition into it yet; the state exists so the vocabulary is
# complete, but nothing can currently reach it.
ARCHETYPE_STATES = ("Active", "In Transition", "Completed", "Rejected", "Cancelled")


def resolve_bank_stage(bank, status_or_stage):
	"""
	Given a string that might be an archetype state or a bank stage (by id, external code, or label),
	return a dict with 'archetype_state' and 'stage_id' (if resolved to a specific stage).
	"""
	# Direct match for archetype states
	if status_or_stage in ARCHETYPE_STATES:
		return {"archetype_state": status_or_stage, "stage_id": None, "stage_label": status_or_stage}

	# Otherwise try to match a bank stage
	stage = frappe.get_all(  # bank-scope-exempt: bank explicitly filtered
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
	stage = frappe.get_all(  # bank-scope-exempt: bank explicitly filtered
		"A2C Loan Status Stage",
		filters={"bank": bank, "archetype_state": "In Transition"},
		fields=["stage_id", "label", "archetype_state"],
		order_by="sequence asc, creation asc",
		limit=1,
	)
	return stage[0] if stage else None
