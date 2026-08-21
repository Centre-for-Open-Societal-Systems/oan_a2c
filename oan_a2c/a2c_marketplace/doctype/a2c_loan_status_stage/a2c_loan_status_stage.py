# Copyright (c) 2026, OpenAgriNet and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document


class A2CLoanStatusStage(Document):
	def before_insert(self):
		if not self.bank and frappe.session.user != "Administrator":
			from oan_a2c.a2c_marketplace.permissions import get_user_bank

			self.bank = get_user_bank(frappe.session.user)

		if not self.stage_id:
			self.stage_id = f"{frappe.scrub(self.label).replace('_', '-')}-{frappe.generate_hash(length=6)}"

	def validate(self):
		if not self.bank:
			frappe.throw(_("Bank is required for a Loan Status Stage"))

	def on_trash(self):
		# Prevent deletion if any Loan Application is currently using this stage
		count = frappe.db.count("A2C Loan Application", {"stage_id": self.stage_id, "bank": self.bank})
		if count > 0:
			frappe.throw(
				_("Cannot delete stage '{0}' because {1} applications are currently in this stage.").format(
					self.label, count
				)
			)
