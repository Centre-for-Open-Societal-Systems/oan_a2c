import frappe

from oan_a2c.a2c_marketplace.roles import FARMER_ROLE

WORKFLOW = "A2C Loan Application Workflow"

# Must match the action apply_status_transition resolves for this hop. The map in
# api/utils.py sends ("Draft", "Processing") to "Send for Review", and
# apply_workflow looks a transition up by (state, action, allowed-role) -- so a
# farmer row carrying any other action name is never found, and submission fails
# with "not a valid transition" even though the row exists.
ACTION = "Send for Review"


def execute():
	"""Let a farmer move their own Draft application to Processing.

	Frappe authorises a transition per row: get_transitions() matches
	`transition.state == current_state and transition.allowed in roles`, so each
	role needs its own row. This mirrors how Approve/Reject already carry one row
	for Bank Agent and one for Bank Admin.

	The durable definition lives in fixtures/workflow.json -- fixtures sync *after*
	patches on every migrate and overwrite the whole child table, so a row added
	only here is wiped by the next migrate. This patch exists to repair sites that
	already migrated; keep the two in step.
	"""
	if not frappe.db.exists("Workflow", WORKFLOW):
		return

	wf = frappe.get_doc("Workflow", WORKFLOW)

	if any(
		t.state == "Draft" and t.action == ACTION and t.allowed == FARMER_ROLE for t in wf.transitions
	):
		return

	wf.append(
		"transitions",
		{
			"state": "Draft",
			"action": ACTION,
			"next_state": "Processing",
			"allowed": FARMER_ROLE,
			"allow_self_approval": 1,
		},
	)
	wf.save(ignore_permissions=True)
