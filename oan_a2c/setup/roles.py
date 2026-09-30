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
# Only the platform admin works in the Frappe Desk. Everyone else uses the JWT API
# through their own portal, so their roles have desk_access = 0: holders stay Website
# Users, never see /app, and do not consume Desk seats.
APP_ROLES = [
	{"role_name": ADMIN_ROLE, "desk_access": 1},
	{"role_name": BANK_ADMIN_ROLE, "desk_access": 0},
	{"role_name": BANK_AGENT_ROLE, "desk_access": 0},
	{"role_name": DEVELOPMENT_AGENT_ROLE, "desk_access": 0},
	{"role_name": FARMER_ROLE, "desk_access": 0},
]


def setup_roles():
	"""Ensure all A2C roles exist with the correct desk_access configuration."""
	for role in APP_ROLES:
		role_name = role["role_name"]
		desk_access = role["desk_access"]
		if frappe.db.exists("Role", role_name):
			role_doc = frappe.get_doc("Role", role_name)
			if role_doc.desk_access != desk_access:
				# Save the document, not db.set_value: Role.on_update re-evaluates every
				# holder's user_type (System vs Website User) when desk_access changes.
				role_doc.desk_access = desk_access
				role_doc.save(ignore_permissions=True)
		else:
			frappe.get_doc(
				{
					"doctype": "Role",
					"role_name": role_name,
					"desk_access": desk_access,
				}
			).insert(ignore_permissions=True)
