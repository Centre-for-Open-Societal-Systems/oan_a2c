# Copyright (c) 2026, OpenAgriNet and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document

from oan_a2c.a2c_marketplace.lookups import refresh_product_lookups


class A2CTermRelationship(Document):
	def before_save(self):
		if self.loan_product and not self.bank:
			self.bank = frappe.db.get_value("A2C Loan Product", self.loan_product, "bank")

	def on_update(self):
		if self.loan_product:
			refresh_product_lookups(self.loan_product)

	def on_trash(self):
		if self.loan_product:
			# Needs to be done after the document is deleted, so using frappe.enqueue or running it after trash is better
			# But for simplicity, we can do it during on_trash which happens before deletion.
			# Actually on_trash happens before deletion, so the lookup refresh might still see the term relationship
			# We can use after_delete.
			pass

	def after_delete(self):
		if self.loan_product:
			refresh_product_lookups(self.loan_product)
