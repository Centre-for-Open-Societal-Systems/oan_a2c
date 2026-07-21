# Copyright (c) 2026, OpenAgriNet and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document
from oan_a2c.a2c_marketplace.lookups import refresh_product_lookups

class A2CLoanProduct(Document):
	def before_save(self):
		if not self.slug and self.product_name:
			# Ensure unique slug per bank
			base_slug = frappe.scrub(self.product_name).replace('_', '-')
			self.slug = f"{self.bank}-{base_slug}"

	def on_update(self):
		refresh_product_lookups(self.name)

