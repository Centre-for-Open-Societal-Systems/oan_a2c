# Copyright (c) 2026, OpenAgriNet and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document


class A2CFarmerProfile(Document):
	def validate(self):
		if self.phone_number and not self.phone_number.isdigit() and not self.phone_number.startswith("+"):
			frappe.throw(_("Phone Number must contain only digits or start with +"))
