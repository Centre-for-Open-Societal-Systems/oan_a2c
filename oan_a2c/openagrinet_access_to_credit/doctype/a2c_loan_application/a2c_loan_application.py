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

		if not self.is_new():
			db_status = self.get_db_value("status")
			if db_status == "Rejected" and self.status != "Rejected":
				frappe.throw("Status is locked because the loan application is Rejected", frappe.ValidationError)


