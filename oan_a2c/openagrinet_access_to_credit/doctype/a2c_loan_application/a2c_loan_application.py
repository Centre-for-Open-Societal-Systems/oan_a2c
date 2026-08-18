# Copyright (c) 2026, OpenAgriNet and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document

from oan_a2c.a2c_marketplace.permissions import get_bank_members
from oan_a2c.a2c_marketplace.roles import BANK_ROLES
from oan_a2c.api.v1.notifications import notify_users


class A2CLoanApplication(Document):
	def _bank_recipients(self):
		"""Bank Admins + Agents of this application's bank (actor excluded downstream)."""
		return get_bank_members(self.bank, roles=BANK_ROLES)

	def after_insert(self):
		"""Notify the bank's team that a new application has landed."""
		if self.status == "Draft":
			return
		label = self.lead_id or self.name
		notify_users(
			self._bank_recipients(),
			subject="New loan application submitted",
			message=f"New loan application submitted for {label} ({self.loan_product or 'no product'})",
			doctype="A2C Loan Application",
			docname=self.name,
		)

	def on_update(self):
		"""Notify the bank's team when the application status changes."""
		if self.is_new() or not self.has_value_changed("status"):
			return
		
		actor = frappe.session.user
		old_status = self.get_value_before_save("status")
		
		if old_status == "Draft" and self.status == "Processing":
			label = self.lead_id or self.name
			notify_users(
				self._bank_recipients(),
				subject="New loan application submitted",
				message=f"New loan application submitted for {label} ({self.loan_product or 'no product'})",
				doctype="A2C Loan Application",
				docname=self.name,
			)
			return

		notify_users(
			self._bank_recipients(),
			subject=f"Loan application {self.name} is now {self.status}",
			message=(
				f"Loan Application {self.name} for {self.lead_id or self.name} "
				f"has been {self.status} by {actor}"
			),
			doctype="A2C Loan Application",
			docname=self.name,
		)

	def validate(self):
		if self.is_new() and self.lead_id:
			lead_status, lead_source = frappe.db.get_value("A2C Lead", self.lead_id, ["status", "lead_source"])
			if lead_status not in ["Verified", "Processed"] and lead_source != "Self Service":
				frappe.throw(_("A Loan Application can only be created for a Verified or Processed Lead."))

		if self.requested_amount and self.requested_amount < 0:
			frappe.throw(_("Requested Amount cannot be negative"))
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

		if self.loan_product and not self.loan_product_name:
			self.loan_product_name = frappe.db.get_value(
				"A2C Loan Product", self.loan_product, "product_name"
			)

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
