# Copyright (c) 2026, OpenAgriNet and contributors
# For license information, please see license.txt
"""
Creates the "A2C Marketplace Admin" role: the platform-level admin that sits
above Bank Admin / Bank Agent and is unbound by bank tenant isolation (sees all
banks), as recognised by oan_a2c.a2c_marketplace.permissions.

Idempotent: safe to re-run.
"""

import frappe

from oan_a2c.a2c_marketplace.permissions import MARKETPLACE_ADMIN_ROLE


def execute():
	if frappe.db.exists("Role", MARKETPLACE_ADMIN_ROLE):
		return

	frappe.get_doc(
		{
			"doctype": "Role",
			"role_name": MARKETPLACE_ADMIN_ROLE,
			"desk_access": 1,
		}
	).insert(ignore_permissions=True)
