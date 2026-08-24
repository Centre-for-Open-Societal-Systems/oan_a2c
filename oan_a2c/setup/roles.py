# Copyright (c) 2026, OpenAgriNet and contributors
# For license information, please see license.txt
"""
Provision and maintain app-owned roles declaratively in code.

Runs idempotently on both `after_install` and `after_migrate`.
"""

import frappe

from oan_a2c.a2c_marketplace.roles import (
	ADMIN_ROLE,
	BANK_ADMIN_ROLE,
	BANK_AGENT_ROLE,
	DEVELOPMENT_AGENT_ROLE,
	FARMER_ROLE,
)

# App roles and their desk_access requirements.
# desk_access = 0 for FARMER_ROLE ensures farmer accounts remain Website Users
# without consuming Desk seats or accessing /app desk navigation.
APP_ROLES = [
	{"role_name": ADMIN_ROLE, "desk_access": 1},
	{"role_name": BANK_ADMIN_ROLE, "desk_access": 1},
	{"role_name": BANK_AGENT_ROLE, "desk_access": 1},
	{"role_name": DEVELOPMENT_AGENT_ROLE, "desk_access": 1},
	{"role_name": FARMER_ROLE, "desk_access": 0},
]


def setup_roles():
	"""Ensure all A2C roles exist with the correct desk_access configuration."""
	for role in APP_ROLES:
		role_name = role["role_name"]
		desk_access = role["desk_access"]
		if frappe.db.exists("Role", role_name):
			current_desk_access = frappe.db.get_value("Role", role_name, "desk_access")
			if current_desk_access != desk_access:
				frappe.db.set_value("Role", role_name, "desk_access", desk_access)
		else:
			frappe.get_doc(
				{
					"doctype": "Role",
					"role_name": role_name,
					"desk_access": desk_access,
				}
			).insert(ignore_permissions=True)
