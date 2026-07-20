import frappe


def create_role(role_name):
	if not frappe.db.exists("Role", role_name):
		doc = frappe.new_doc("Role")
		doc.role_name = role_name
		doc.insert(ignore_permissions=True)


def create_workflow_state(state_name):
	if not frappe.db.exists("Workflow State", state_name):
		doc = frappe.new_doc("Workflow State")
		doc.workflow_state_name = state_name
		doc.insert(ignore_permissions=True)


def create_workflow_action(action_name):
	if not frappe.db.exists("Workflow Action Master", action_name):
		doc = frappe.new_doc("Workflow Action Master")
		doc.workflow_action_name = action_name
		doc.insert(ignore_permissions=True)


def setup_lead_workflow():
	states = ["Active", "Verified", "Processed", "Granted", "Rejected", "Dormant"]
	for s in states:
		create_workflow_state(s)

	actions = ["Verify", "Mark Processed", "Grant", "Reject", "Mark Dormant", "Reactivate"]
	for a in actions:
		create_workflow_action(a)

	if not frappe.db.exists("Workflow", "A2C Lead Workflow"):
		wf = frappe.new_doc("Workflow")
		wf.workflow_name = "A2C Lead Workflow"
		wf.document_type = "A2C Lead"
		wf.is_active = 1
		wf.workflow_state_field = "workflow_state"

		# States
		for s in states:
			wf.append("states", {"state": s, "doc_status": 0, "allow_edit": "System Manager"})

		# Transitions
		transitions = [
			("Active", "Verify", "Verified", "Development Agent"),
			("Verified", "Mark Processed", "Processed", "Development Agent"),
			("Processed", "Grant", "Granted", "Bank Agent"),
			("Processed", "Reject", "Rejected", "Bank Agent"),
			("Active", "Reject", "Rejected", "Development Agent"),
			("Verified", "Reject", "Rejected", "Development Agent"),
			("Active", "Mark Dormant", "Dormant", "Development Agent"),
			("Verified", "Mark Dormant", "Dormant", "Development Agent"),
			("Dormant", "Reactivate", "Active", "Development Agent"),
		]

		for state, action, next_state, role in transitions:
			wf.append(
				"transitions", {"state": state, "action": action, "next_state": next_state, "allowed": role}
			)

		wf.insert(ignore_permissions=True)


def setup_loan_workflow():
	states = [
		{"name": "Draft", "doc_status": 0},
		{"name": "Processing", "doc_status": 0},
		{"name": "Approved", "doc_status": 1},
		{"name": "Rejected", "doc_status": 1},
	]
	for s in states:
		create_workflow_state(s["name"])

	actions = ["Send for Review", "Approve", "Reject"]
	for a in actions:
		create_workflow_action(a)

	if not frappe.db.exists("Workflow", "A2C Loan Application Workflow"):
		wf = frappe.new_doc("Workflow")
		wf.workflow_name = "A2C Loan Application Workflow"
		wf.document_type = "A2C Loan Application"
		wf.is_active = 1
		wf.workflow_state_field = "workflow_state"

		for s in states:
			wf.append(
				"states", {"state": s["name"], "doc_status": s["doc_status"], "allow_edit": "System Manager"}
			)

		transitions = [
			("Draft", "Send for Review", "Processing", "Development Agent"),
			("Processing", "Approve", "Approved", "Bank Agent"),
			("Processing", "Reject", "Rejected", "Bank Agent"),
		]

		for state, action, next_state, role in transitions:
			wf.append(
				"transitions", {"state": state, "action": action, "next_state": next_state, "allowed": role}
			)

		wf.insert(ignore_permissions=True)


def execute():
	create_role("Development Agent")
	create_role("Bank Agent")
	setup_lead_workflow()
	setup_loan_workflow()
	# nosemgrep: frappe-manual-commit -- reviewed: one-off setup script persists workflow creation
	frappe.db.commit()
	print("Workflows created successfully!")
