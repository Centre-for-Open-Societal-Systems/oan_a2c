# Copyright (c) 2026, OpenAgriNet and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document
from frappe.utils.data import cstr


class A2CTerm(Document):
	def before_save(self):
		if not self.slug and self.term_name:
			self.slug = frappe.scrub(self.term_name).replace("_", "-")
