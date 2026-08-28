import frappe

from oan_a2c.a2c_marketplace.roles import FARMER_ROLE


def execute():
	"""Create the A2C Farmer role with desk_access = 0.

	desk_access is the whole point: User.set_system_user() classifies any holder
	of a desk-access role as a System User, which would give every farmer desk
	access and consume a seat. With desk_access = 0 they are Website Users.
	"""
	if frappe.db.exists("Role", FARMER_ROLE):
		frappe.db.set_value("Role", FARMER_ROLE, "desk_access", 0)
		return

	frappe.get_doc(
		{
			"doctype": "Role",
			"role_name": FARMER_ROLE,
			"desk_access": 0,
		}
	).insert(ignore_permissions=True)
