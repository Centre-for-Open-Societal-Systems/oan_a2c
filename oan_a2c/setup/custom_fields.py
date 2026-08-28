# Copyright (c) 2026, OpenAgriNet and contributors
# For license information, please see license.txt
"""
Custom fields this app adds to standard (Frappe-owned) doctypes.

These live in code and are installed idempotently from both the `after_install`
and `after_migrate` hooks. This is the mechanism ERPNext and other official
Frappe apps use to own fields on doctypes they don't ship.

Why not a patch: `install_app()` calls `set_all_patches_as_completed()`, which
records every entry in `patches.txt` as already-run *without executing it*. So a
patch that creates a custom field never runs on a fresh install (e.g. CI's
`bench new-site` + `install-app`), and the column is missing. `after_install`
runs on that path; `after_migrate` keeps already-provisioned sites self-healing.

`create_custom_fields` is idempotent (it updates in place), so running on every
migrate is safe.
"""

from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

CUSTOM_FIELDS = {
	"User": [
		{
			"fieldname": "a2c_must_change_password",
			"label": "A2C: Must Change Password",
			"fieldtype": "Check",
			"insert_after": "send_welcome_email",
			"default": "0",
			"read_only": 1,
			"no_copy": 1,
			"description": (
				"Set when an admin issues a temporary password. "
				"Cleared once the user sets a password of their own."
			),
		}
	],
}


def setup_custom_fields():
	create_custom_fields(CUSTOM_FIELDS, ignore_validate=True)
