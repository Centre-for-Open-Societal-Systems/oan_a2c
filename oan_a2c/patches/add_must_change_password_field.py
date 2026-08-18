# Copyright (c) 2026, OpenAgriNet and contributors
# For license information, please see license.txt
"""
Add the `a2c_must_change_password` flag to User.

A Bank Admin hands out a temporary password when inviting an agent, and again
when reissuing one for a forgotten password. Until this field existed that
password was permanent — the agent could keep signing in with a credential
their admin knows, indefinitely.

The flag means "this password was chosen by someone else and must be rotated".
`api.auth.login` refuses to mint a token while it is set, the JWT middleware
rejects any token already issued to a flagged user, and
`api.auth.set_initial_password` clears it.

Idempotent: create_custom_field is a no-op once the field exists.
"""

from frappe.custom.doctype.custom_field.custom_field import create_custom_field

FIELDNAME = "a2c_must_change_password"


def execute():
	create_custom_field(
		"User",
		{
			"fieldname": FIELDNAME,
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
		},
	)
