# Copyright (c) 2026, OpenAgriNet and contributors
# For license information, please see license.txt
"""
Creates the A2C Lead and A2C Loan Application workflows declaratively, plus the
Workflow State / Workflow Action master records they depend on, and backfills
`workflow_state` (and Loan `docstatus`) on existing records.

Idempotent: safe to re-run. See development/workflow_design_lead_loan.md for the
authoritative design (states, transitions, role gates, submittable decision).
"""

import frappe

from oan_a2c.a2c_marketplace.roles import BANK_AGENT_ROLE, DEVELOPMENT_AGENT_ROLE

# --- Master metadata -------------------------------------------------------

# Roles referenced by the workflow transitions/allow_edit below. `allowed` is a
# required Link to Role, so these must exist before the workflows are inserted.
# On an incremental upgrade they already exist (renamed from plain names or
# synced via the Role fixture); on a clean-slate install neither is true yet
# because fixtures load *after* patches — hence we create them here. System
# Manager is a Frappe built-in and always present.
WORKFLOW_ROLES = (DEVELOPMENT_AGENT_ROLE, BANK_AGENT_ROLE)

# Workflow State -> style (cosmetic only; Frappe ships these styles)
WORKFLOW_STATES = {
	"Active": "Primary",
	"Verified": "Info",
	"Processed": "Warning",
	"Granted": "Success",
	"Rejected": "Danger",
	"Dormant": "Inverse",
	"Draft": "Primary",
	"Processing": "Warning",
	"Approved": "Success",
	"In Transition": "Warning",
	"Completed": "Success",
	"Cancelled": "Inverse",
}

# Workflow Action master records (the "buttons")
WORKFLOW_ACTIONS = [
	"Verify",
	"Mark Processed",
	"Grant",
	"Reject",
	"Mark Dormant",
	"Reactivate",
	"Send for Review",
	"Approve",
	"Submit",
	"Complete",
]


def execute():
	_ensure_roles()
	_ensure_workflow_states()
	_ensure_workflow_actions()
	_create_lead_workflow()
	_create_loan_workflow()
	_seed_default_stages()
	_backfill_workflow_state()
	frappe.db.commit()


def _seed_default_stages():
	default_stages = [
		{"label": "Submitted", "archetype_state": "In Transition", "sequence": 1},
		{"label": "Processed", "archetype_state": "In Transition", "sequence": 2},
		{"label": "Verified", "archetype_state": "In Transition", "sequence": 3},
		{"label": "Approved", "archetype_state": "In Transition", "sequence": 4},
		{"label": "Disbursed", "archetype_state": "Completed", "sequence": 5},
		{"label": "Rejected", "archetype_state": "Completed", "sequence": 6},
	]
	# For each bank in the system, ensure default stages exist
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


def _ensure_roles():
	for role_name in WORKFLOW_ROLES:
		if not frappe.db.exists("Role", role_name):
			frappe.get_doc(
				{
					"doctype": "Role",
					"role_name": role_name,
					"desk_access": 1,
				}
			).insert(ignore_permissions=True)


def _ensure_workflow_states():
	for state, style in WORKFLOW_STATES.items():
		if not frappe.db.exists("Workflow State", state):
			frappe.get_doc(
				{
					"doctype": "Workflow State",
					"workflow_state_name": state,
					"style": style,
				}
			).insert(ignore_permissions=True)


def _ensure_workflow_actions():
	for action in WORKFLOW_ACTIONS:
		if not frappe.db.exists("Workflow Action Master", action):
			frappe.get_doc(
				{
					"doctype": "Workflow Action Master",
					"workflow_action_name": action,
				}
			).insert(ignore_permissions=True)


def _upsert_workflow(name, doctype, states, transitions):
	"""Create or replace a Workflow doc with the given states/transitions."""
	if frappe.db.exists("Workflow", name):
		frappe.delete_doc("Workflow", name, ignore_permissions=True, force=True)

	wf = frappe.new_doc("Workflow")
	wf.workflow_name = name
	wf.document_type = doctype
	wf.is_active = 1
	wf.workflow_state_field = "workflow_state"
	wf.send_email_alert = 0

	for s in states:
		wf.append("states", s)
	for t in transitions:
		wf.append("transitions", t)

	wf.insert(ignore_permissions=True)


def _create_lead_workflow():
	# A2C Lead is non-submittable: every state stays at docstatus 0.
	states = [
		{"state": "Active", "doc_status": "0", "allow_edit": "A2C Development Agent"},
		{"state": "Verified", "doc_status": "0", "allow_edit": "A2C Development Agent"},
		{"state": "Processed", "doc_status": "0", "allow_edit": "A2C Bank Agent"},
		{"state": "Granted", "doc_status": "0", "allow_edit": "System Manager"},
		{"state": "Rejected", "doc_status": "0", "allow_edit": "System Manager"},
		{"state": "Dormant", "doc_status": "0", "allow_edit": "A2C Development Agent"},
	]
	transitions = [
		{"state": "Active", "action": "Verify", "next_state": "Verified", "allowed": "A2C Development Agent"},
		{
			"state": "Verified",
			"action": "Mark Processed",
			"next_state": "Processed",
			"allowed": "A2C Development Agent",
		},
		{"state": "Processed", "action": "Grant", "next_state": "Granted", "allowed": "A2C Bank Agent"},
		{"state": "Processed", "action": "Reject", "next_state": "Rejected", "allowed": "A2C Bank Agent"},
		{"state": "Active", "action": "Reject", "next_state": "Rejected", "allowed": "A2C Development Agent"},
		{
			"state": "Verified",
			"action": "Reject",
			"next_state": "Rejected",
			"allowed": "A2C Development Agent",
		},
		{
			"state": "Active",
			"action": "Mark Dormant",
			"next_state": "Dormant",
			"allowed": "A2C Development Agent",
		},
		{
			"state": "Verified",
			"action": "Mark Dormant",
			"next_state": "Dormant",
			"allowed": "A2C Development Agent",
		},
		{
			"state": "Dormant",
			"action": "Reactivate",
			"next_state": "Active",
			"allowed": "A2C Development Agent",
		},
	]
	_upsert_workflow("A2C Lead Workflow", "A2C Lead", states, transitions)


def _create_loan_workflow():
	# A2C Loan Application is submittable: Completed submits the doc (docstatus 1).
	states = [
		{"state": "Active", "doc_status": "0", "allow_edit": "A2C Development Agent"},
		{"state": "In Transition", "doc_status": "0", "allow_edit": "A2C Bank Agent"},
		{"state": "Completed", "doc_status": "1", "allow_edit": "System Manager"},
		{"state": "Cancelled", "doc_status": "2", "allow_edit": "System Manager"},
	]
	transitions = [
		{
			"state": "Active",
			"action": "Submit",
			"next_state": "In Transition",
			"allowed": "A2C Development Agent",
			"allow_self_approval": 1,
		},
		{
			"state": "Active",
			"action": "Submit",
			"next_state": "In Transition",
			"allowed": "A2C Farmer",
			"allow_self_approval": 1,
		},
		{
			"state": "In Transition",
			"action": "Complete",
			"next_state": "Completed",
			"allowed": "A2C Bank Agent",
			"allow_self_approval": 1,
		},
		{
			"state": "In Transition",
			"action": "Complete",
			"next_state": "Completed",
			"allowed": "A2C Bank Admin",
			"allow_self_approval": 1,
		},
	]
	_upsert_workflow("A2C Loan Application Workflow", "A2C Loan Application", states, transitions)


def _backfill_workflow_state():
	"""Map existing `status` -> `workflow_state`, and submit terminal loans to docstatus 1."""
	# Leads: workflow_state mirrors status 1:1 (same option names).
	for name, status in frappe.get_all("A2C Lead", fields=["name", "status"], as_list=True):
		frappe.db.set_value("A2C Lead", name, "workflow_state", status, update_modified=False)

	# Loans: legacy statuses mapped to Archetype statuses.
	# Draft -> Active
	# Processing -> In Transition
	# Approved / Rejected -> Completed
	# Migration runs as Administrator over every bank's records by design. bank-scope-exempt
	for name, status, docstatus in frappe.get_all(
		"A2C Loan Application", fields=["name", "status", "docstatus"], as_list=True
	):  # bank-scope-exempt
		if status == "Draft":
			state = "Active"
		elif status == "Processing":
			state = "In Transition"
		elif status in ("Approved", "Rejected"):
			state = "Completed"
		else:
			state = "Active"

		frappe.db.set_value("A2C Loan Application", name, "workflow_state", state, update_modified=False)
		frappe.db.set_value("A2C Loan Application", name, "status", state, update_modified=False)

		# Submit existing terminal records so their docstatus matches the new workflow.
		if state == "Completed" and docstatus == 0:
			frappe.db.set_value("A2C Loan Application", name, "docstatus", 1, update_modified=False)
