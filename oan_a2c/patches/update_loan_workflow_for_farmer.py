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
	return
