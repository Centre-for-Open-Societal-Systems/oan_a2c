"""Remap legacy loan statuses to the archetype vocabulary, then backfill their stage."""

import frappe

# Legacy status -> archetype status. Mirrors create_lead_loan_workflows._LEGACY_STATUS_MAP;
# duplicated here because that patch is already shipped on develop, so re-editing it would
# not reach any site that already has it in Patch Log. See update_loan_workflow_for_farmer.py
# for why the delete-and-rerun trick only works while a patch is still unshipped.
_LEGACY_STATUS_MAP = {
	"Draft": "Active",
	"Processing": "In Transition",
	"Approved": "Completed",
	"Rejected": "Rejected",
}

# States that carry docstatus 1 in the current workflow definition.
_SUBMITTED_STATES = ("Completed", "Rejected")


def execute():
	"""Fix two things left over from the workflow-vocabulary migration:

	1. Applications still holding a legacy status (created or last touched before
	   the migration reached this site) never had `status`/`workflow_state` remapped
	   to the archetype vocabulary, and terminal ones never got submitted.
	2. Applications already on the archetype status ``In Transition`` but with no
	   bank pipeline stage assigned, because the status alone doesn't identify one.

	Idempotent: rows already on an archetype status are left alone in step 1, and
	rows that already have a stage are left alone in step 2, so running this again
	after everything is fixed is a no-op.
	"""
	# 1. Remap legacy statuses.
	for name, status, docstatus in frappe.get_all(
		"A2C Loan Application", fields=["name", "status", "docstatus"], as_list=True
	):  # bank-scope-exempt: migration runs as Administrator over every bank by design
		target = _LEGACY_STATUS_MAP.get(status)
		if not target:
			continue

		frappe.db.set_value(
			"A2C Loan Application",
			name,
			{"status": target, "workflow_state": target},
			update_modified=False,
		)
		if target in _SUBMITTED_STATES and docstatus == 0:
			frappe.db.set_value("A2C Loan Application", name, "docstatus", 1, update_modified=False)

	# 2. Assign the default "Processed" stage to applications now sitting at
	#    "In Transition" with no stage of their own.
	for application in frappe.get_all(
		"A2C Loan Application",
		filters={"status": "In Transition"},
		fields=["name", "bank", "stage_id"],
	):  # bank-scope-exempt: migration runs as Administrator over every bank by design
		if application.stage_id or not application.bank:
			continue

		stage = frappe.db.get_value(
			"A2C Loan Status Stage",
			{"bank": application.bank, "label": "Processed"},
			["stage_id", "label"],
			as_dict=True,
		)
		if not stage:
			continue

		frappe.db.set_value(
			"A2C Loan Application",
			application.name,
			{"stage_id": stage.stage_id, "stage_label": stage.label},
			update_modified=False,
		)

	frappe.db.commit()
