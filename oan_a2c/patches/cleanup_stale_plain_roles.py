# Copyright (c) 2026, OpenAgriNet and contributors
# For license information, please see license.txt
"""
Retire the pre-rename plain-named roles for good.

`rename_a2c_roles` already renamed Bank Admin/Bank Agent/Development Agent to
their A2C-prefixed names, but it runs once. The plain roles came back because
invite_user/update_user_profile accepted an arbitrary client `role` string and
re-created the Role + Has Role link (now gated by an allowlist). This patch
merges any lingering plain role into its A2C counterpart — frappe.rename_doc
with merge=True moves every link reference (Has Role, DocPerm, Workflow, Role
Profile, ...) — then deletes any plain role with no A2C counterpart to merge into.

Idempotent: a no-op once the plain roles are gone.
"""

import frappe

from oan_a2c.a2c_marketplace.roles import (
	BANK_ADMIN_ROLE,
	BANK_AGENT_ROLE,
	DEVELOPMENT_AGENT_ROLE,
)

# plain (retired) name -> canonical A2C name
ROLE_MAP = {
	"Bank Admin": BANK_ADMIN_ROLE,
	"Bank Agent": BANK_AGENT_ROLE,
	"Development Agent": DEVELOPMENT_AGENT_ROLE,
}


def execute():
	for old_role, new_role in ROLE_MAP.items():
		if not frappe.db.exists("Role", old_role):
			continue

		if frappe.db.exists("Role", new_role):
			# Merge moves all link references from old_role onto new_role and
			# deletes old_role.
			frappe.rename_doc("Role", old_role, new_role, merge=True, force=True)
		else:
			# No target to merge into: just rename in place.
			frappe.rename_doc("Role", old_role, new_role, force=True)

	frappe.db.commit()
