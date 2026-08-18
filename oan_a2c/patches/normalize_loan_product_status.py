# Copyright (c) 2026, OpenAgriNet and contributors
# For license information, please see license.txt
"""
Normalise legacy A2C Loan Product statuses onto the current vocabulary.

The status field originally offered `Draft / Active / Archived` (default `Draft`).
It was later changed to `Pending Approval / Active / Rejected`, and `Archived` has
now been reintroduced, so the field is `Pending Approval / Active / Rejected /
Archived`.

Rows created before the rename can still hold the retired `Draft` value, which is no
longer a valid Select option. Map them onto the new entry state; every other value
(`Active`, `Rejected`, `Archived`, `Pending Approval`) is already valid and left
untouched. A blank/NULL status also falls back to the default entry state.

Idempotent: once the migration has run there are no `Draft`/blank rows left, so a
re-run updates nothing.
"""

import frappe

ENTRY_STATUS = "Pending Approval"


def execute():
	frappe.reload_doc("a2c_marketplace", "doctype", "a2c_loan_product")

	# One-time schema-normalisation migration run as admin; it intentionally rewrites
	# legacy statuses across every bank, so bank scoping does not apply.
	updated = frappe.db.sql(  # bank-scope-exempt: global admin migration, all banks
		"""
		UPDATE `tabA2C Loan Product`
		SET `status` = %s
		WHERE `status` = 'Draft' OR `status` IS NULL OR `status` = ''
		""",
		ENTRY_STATUS,
	)

	frappe.db.commit()
	print(f"Normalised {updated or 0} legacy Loan Product statuses to '{ENTRY_STATUS}'.")
