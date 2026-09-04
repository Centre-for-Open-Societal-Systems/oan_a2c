# Copyright (c) 2026, OpenAgriNet and contributors
# For license information, please see license.txt

from frappe.model.document import Document

from oan_a2c.a2c_marketplace.taxonomy import get_unique_term_id


class A2CTerm(Document):
	def before_naming(self):
		"""Derive `term_id` from `term_name` before Frappe names the document.

		The doctype autonames `field:term_id`, and `set_new_name()` runs *before*
		`before_save`, throwing "Term ID is required" on a blank field. This is the
		only hook early enough to fill it in.
		"""
		if not self.term_id and self.term_name:
			self.term_id = get_unique_term_id(self.term_name, current_term_id=self.name)

	def before_save(self):
		if not self.slug:
			self.slug = self.term_id
