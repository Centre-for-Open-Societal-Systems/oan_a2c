# Copyright (c) 2026, OpenAgriNet and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document

class A2CLoanApplication(Document):
	def validate(self):
		if self.loan_amount and self.loan_amount < 0:
			frappe.throw("Loan Amount cannot be negative")
		if self.phone_number and not self.phone_number.isdigit() and not self.phone_number.startswith('+'):
			frappe.throw("Phone Number must contain only digits or start with +")

		# Enforce status allowlist validation
		if self.status:
			allowed_statuses = ("Draft", "Processing", "Approved", "Rejected")
			if self.status not in allowed_statuses:
				frappe.throw(f"Invalid status: {self.status}", frappe.ValidationError)

		if not self.is_new():
			db_status = self.get_db_value("status")
			if db_status in ["Rejected", "Processed"] and self.status != db_status:
				frappe.throw(f"Status is locked because the loan application is {db_status}", frappe.ValidationError)

			db_step = self.get_db_value("current_step") or 1
			if self.current_step and self.current_step != db_step:
				if self.current_step > db_step + 1:
					frappe.throw("Invalid step transition. You cannot skip steps.", frappe.ValidationError)


