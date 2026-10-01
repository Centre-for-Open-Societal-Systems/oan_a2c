# Copyright (c) 2026, OpenAgriNet and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class A2CDashboardSnapshot(Document):
	"""Written only by a2c_marketplace.dashboard_rollup; see that module."""


def on_doctype_update():
	frappe.db.add_index("A2C Dashboard Snapshot", ["snapshot_date", "family", "bank"])
