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

		# before_insert runs ahead of validate() and ahead of Frappe's mandatory-field
		# check, so `label` cannot be assumed present here: frappe.scrub(None) raises
		# AttributeError and the caller sees a stack trace instead of "Label is
		# required". Check it explicitly and let the same message serve both paths.
		if not self.label:
			frappe.throw(_("Label is required for a Loan Status Stage"))

		if not self.stage_id:
			self.stage_id = f"{frappe.scrub(self.label).replace('_', '-')}-{frappe.generate_hash(length=6)}"

	def validate(self):
		if not self.label:
			frappe.throw(_("Label is required for a Loan Status Stage"))

		if not self.bank:
			frappe.throw(_("Bank is required for a Loan Status Stage"))

	def on_update(self):
		from oan_a2c.a2c_marketplace.stages import invalidate_stage_map_cache

		invalidate_stage_map_cache(self.bank)

	def on_trash(self):
		# Prevent deletion if any Loan Application is currently using this stage
		count = frappe.db.count("A2C Loan Application", {"stage_id": self.stage_id, "bank": self.bank})
		if count > 0:
			frappe.throw(
				_("Cannot delete stage '{0}' because {1} applications are currently in this stage.").format(
					self.label, count
				)
			)

		from oan_a2c.a2c_marketplace.stages import invalidate_stage_map_cache

		invalidate_stage_map_cache(self.bank)
