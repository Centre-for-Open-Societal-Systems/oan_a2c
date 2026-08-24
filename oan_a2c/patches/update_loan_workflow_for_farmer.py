import frappe

LEGACY_PATCH = "oan_a2c.patches.create_lead_loan_workflows"


def execute():
	"""Re-apply create_lead_loan_workflows so its rewritten definition actually lands.

	Why this exists
	---------------
	`create_lead_loan_workflows` is listed near the top of patches.txt and was
	recorded in Patch Log on every site that migrated before this branch. Frappe
	skips a patch whose name is already in Patch Log, so the *rewrite* of that
	patch -- the new archetype states (Active / In Transition / Completed /
	Rejected / Cancelled), the Submit + Complete actions, the farmer transition and
	the legacy-status backfill -- would never execute on an existing site. The
	DocType JSON and every call site would move to the new vocabulary while the
	installed Workflow stayed on Draft / Processing / Approved, and
	apply_status_transition would fail to resolve any transition at all.

	Editing an already-executed patch is normally forbidden for exactly this
	reason. It is tolerable here only because the rewrite has not shipped to any
	environment yet, so no site has run either version of the new definition.

	What it does
	------------
	1. Drops the Patch Log row for the legacy patch, so the name is no longer
	   "already applied" and a future edit-and-migrate cycle behaves normally.
	2. Calls its execute() directly, so the new workflow lands in *this* migrate
	   rather than the next one.

	Both steps are idempotent: _upsert_workflow overwrites the definition in place
	and the status backfill only touches rows that still carry a legacy status.
	"""
	frappe.db.delete("Patch Log", {"patch": LEGACY_PATCH})

	from oan_a2c.patches import create_lead_loan_workflows

	create_lead_loan_workflows.execute()
