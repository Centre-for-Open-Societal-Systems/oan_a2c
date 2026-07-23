# Copyright (c) 2026, OpenAgriNet and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document


class A2CLoanApplication(Document):
	def validate(self):
		if self.loan_amount and self.loan_amount < 0:
			frappe.throw(_("Loan Amount cannot be negative"))
		if self.phone_number and not self.phone_number.isdigit() and not self.phone_number.startswith("+"):
			frappe.throw(_("Phone Number must contain only digits or start with +"))

		# Status ordering, terminal-state locking, and per-role gating are enforced by the
		# A2C Loan Application Workflow (see development/workflow_design_lead_loan.md) and by
		# submit (docstatus). The previous imperative status-lock here was buggy (it locked the
		# non-existent status "Processed", leaving "Approved" unlocked) and is now removed.

		if not self.is_new():
			db_step = self.get_db_value("current_step") or 1
			if self.current_step and self.current_step != db_step:
				if self.current_step > db_step + 1:
					frappe.throw(_("Invalid step transition. You cannot skip steps."), frappe.ValidationError)

		self._sync_bank_from_product()

	def _sync_bank_from_product(self):
		"""Keep the denormalized `bank` snapshot authoritative to the loan product.

		`bank` and `loan_product` are Data snapshots (no FK), so nothing at the DB
		layer stops a typo'd or drifted `bank` -- and a wrong `bank` silently
		mis-scopes bank tenant isolation (bank_scope_query / bank_filters compare
		this value). The participating bank is always the loan product's bank, so
		stamp it from the source on write.

		Non-throwing by design: an unresolvable loan_product is logged, not fatal,
		so existing write paths and historical rows are never broken.
		"""
		if not self.loan_product:
			return

		product_bank = frappe.db.get_value("A2C Loan Product", self.loan_product, "bank")
		if not product_bank:
			frappe.logger("bank_scope").warning(
				f"A2C Loan Application {self.name or '(new)'}: loan_product "
				f"'{self.loan_product}' did not resolve to a product with a bank; "
				f"bank snapshot left as '{self.bank}'."
			)
			return

		if self.bank != product_bank:
			self.bank = product_bank
