import frappe


def execute():
	# Rename existing roles to have the A2C prefix
	role_map = {
		"Bank Admin": "A2C Bank Admin",
		"Bank Agent": "A2C Bank Agent",
		"Development Agent": "A2C Development Agent",
	}

	for old_role, new_role in role_map.items():
		if frappe.db.exists("Role", old_role):
			if not frappe.db.exists("Role", new_role):
				frappe.rename_doc("Role", old_role, new_role, force=True)
			else:
				# Use merge=True to automatically update ALL database link references
				# (User, Workflow, DocPerm, Custom DocPerm, Role Profile, etc.) from old_role to new_role
				frappe.rename_doc("Role", old_role, new_role, merge=True, force=True)

			if frappe.db.exists("Role", new_role):
				doc = frappe.get_doc("Role", new_role)
				if not doc.desk_access:
					doc.desk_access = 1
					doc.save(ignore_permissions=True)

	# Delete Bank Product Manager and any stale links if it exists
	stale_roles = ["Bank Product Manager"]
	for role_name in stale_roles:
		if frappe.db.exists("Role", role_name):
			# Clean up all linked records across tables before deleting
			frappe.db.delete("Has Role", {"role": role_name})
			frappe.db.delete("Custom DocPerm", {"role": role_name})
			frappe.db.delete("DocPerm", {"role": role_name})
			frappe.db.sql("DELETE FROM `tabWorkflow Transition` WHERE allowed = %s", role_name)
			frappe.db.sql("DELETE FROM `tabWorkflow Document State` WHERE allow = %s", role_name)
			frappe.db.sql("DELETE FROM `tabRole Profile Role` WHERE role = %s", role_name)
			frappe.delete_doc("Role", role_name, force=True, ignore_permissions=True, ignore_doctypes=True)
