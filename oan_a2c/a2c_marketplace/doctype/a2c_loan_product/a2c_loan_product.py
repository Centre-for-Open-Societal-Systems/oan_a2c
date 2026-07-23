# Copyright (c) 2026, OpenAgriNet and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document

from oan_a2c.a2c_marketplace.lookups import refresh_product_lookups


class A2CLoanProduct(Document):
	def before_save(self):
		if self.product_name:
			base_slug = frappe.scrub(self.product_name).replace("_", "-")
			candidate_slug = f"{self.bank}-{base_slug}"
			if frappe.db.exists(
				"A2C Loan Product", {"slug": candidate_slug, "name": ["!=", self.name or ""]}
			):
				frappe.throw(
					frappe._("A loan product named '{0}' already exists for your bank.").format(
						self.product_name
					),
					frappe.ValidationError,
				)
			self.slug = candidate_slug

	def on_update(self):
		refresh_product_lookups(self)
