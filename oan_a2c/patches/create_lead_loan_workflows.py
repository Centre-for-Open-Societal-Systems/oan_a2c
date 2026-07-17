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

# --- Master metadata -------------------------------------------------------

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
]


def execute():
	_ensure_workflow_states()
	_ensure_workflow_actions()
	_create_lead_workflow()
	_create_loan_workflow()
	_backfill_workflow_state()
	frappe.db.commit()


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
		{"state": "Active", "doc_status": "0", "allow_edit": "Development Agent"},
		{"state": "Verified", "doc_status": "0", "allow_edit": "Development Agent"},
		{"state": "Processed", "doc_status": "0", "allow_edit": "Bank Agent"},
		{"state": "Granted", "doc_status": "0", "allow_edit": "System Manager"},
		{"state": "Rejected", "doc_status": "0", "allow_edit": "System Manager"},
		{"state": "Dormant", "doc_status": "0", "allow_edit": "Development Agent"},
	]
	transitions = [
		{"state": "Active", "action": "Verify", "next_state": "Verified", "allowed": "Development Agent"},
		{
			"state": "Verified",
			"action": "Mark Processed",
			"next_state": "Processed",
			"allowed": "Development Agent",
		},
		{"state": "Processed", "action": "Grant", "next_state": "Granted", "allowed": "Bank Agent"},
		{"state": "Processed", "action": "Reject", "next_state": "Rejected", "allowed": "Bank Agent"},
		{"state": "Active", "action": "Reject", "next_state": "Rejected", "allowed": "Development Agent"},
		{"state": "Verified", "action": "Reject", "next_state": "Rejected", "allowed": "Development Agent"},
		{
			"state": "Active",
			"action": "Mark Dormant",
			"next_state": "Dormant",
			"allowed": "Development Agent",
		},
		{
			"state": "Verified",
			"action": "Mark Dormant",
			"next_state": "Dormant",
			"allowed": "Development Agent",
		},
		{"state": "Dormant", "action": "Reactivate", "next_state": "Active", "allowed": "Development Agent"},
	]
	_upsert_workflow("A2C Lead Workflow", "A2C Lead", states, transitions)


def _create_loan_workflow():
	# A2C Loan Application is submittable: Approved/Rejected submit the doc (docstatus 1).
	states = [
		{"state": "Draft", "doc_status": "0", "allow_edit": "Development Agent"},
		{"state": "Processing", "doc_status": "0", "allow_edit": "Bank Agent"},
		{"state": "Approved", "doc_status": "1", "allow_edit": "System Manager"},
		{"state": "Rejected", "doc_status": "1", "allow_edit": "System Manager"},
	]
	transitions = [
		{
			"state": "Draft",
			"action": "Send for Review",
			"next_state": "Processing",
			"allowed": "Development Agent",
		},
		{"state": "Processing", "action": "Approve", "next_state": "Approved", "allowed": "Bank Agent"},
		{"state": "Processing", "action": "Reject", "next_state": "Rejected", "allowed": "Bank Agent"},
	]
	_upsert_workflow("A2C Loan Application Workflow", "A2C Loan Application", states, transitions)


def _backfill_workflow_state():
	"""Map existing `status` -> `workflow_state`, and submit terminal loans to docstatus 1."""
	# Leads: workflow_state mirrors status 1:1 (same option names).
	for name, status in frappe.get_all("A2C Lead", fields=["name", "status"], as_list=True):
		frappe.db.set_value("A2C Lead", name, "workflow_state", status, update_modified=False)

	# Loans: blank/legacy statuses default to Draft; Approved/Rejected become docstatus 1.
	for name, status, docstatus in frappe.get_all(
		"A2C Loan Application", fields=["name", "status", "docstatus"], as_list=True
	):
		state = status if status in ("Draft", "Processing", "Approved", "Rejected") else "Draft"
		frappe.db.set_value("A2C Loan Application", name, "workflow_state", state, update_modified=False)
		# Submit existing terminal records so their docstatus matches the new workflow.
		if state in ("Approved", "Rejected") and docstatus == 0:
			frappe.db.set_value("A2C Loan Application", name, "docstatus", 1, update_modified=False)
