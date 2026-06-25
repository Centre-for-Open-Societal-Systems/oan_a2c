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

		# Status ordering, terminal-state locking, and per-role gating are enforced by the
		# A2C Loan Application Workflow (see development/workflow_design_lead_loan.md) and by
		# submit (docstatus). The previous imperative status-lock here was buggy (it locked the
		# non-existent status "Processed", leaving "Approved" unlocked) and is now removed.

		if not self.is_new():
			db_step = self.get_db_value("current_step") or 1
			if self.current_step and self.current_step != db_step:
				if self.current_step > db_step + 1:
					frappe.throw("Invalid step transition. You cannot skip steps.", frappe.ValidationError)


