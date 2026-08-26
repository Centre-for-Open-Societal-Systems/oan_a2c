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

	def before_save(self):
		if not self.is_new():
			db_status = self.get_db_value("status")
			if self.status == "In Transition" and db_status != "In Transition":
				self._enforce_submission_prerequisites()

	def _enforce_submission_prerequisites(self):
		"""
		A loan application may only transition to 'In Transition' (submitted to a bank)
		if an approved consent request exists and is linked.
		"""
		consent_name = self.consent_id
		if consent_name:
			status = frappe.db.get_value("A2C Consent Request", consent_name, "status")
			if status == "Approved":
				return
			frappe.throw(
				_("Loan Application cannot be submitted because the linked consent request is not approved."),
				frappe.ValidationError,
			)

		if self.farmer_profile:
			profile_consent = frappe.db.get_value("A2C Farmer Profile", self.farmer_profile, "consent_id")
			if profile_consent:
				status = frappe.db.get_value("A2C Consent Request", profile_consent, "status")
				if status == "Approved":
					self.consent_id = profile_consent
					return
				frappe.throw(
					_(
						"Loan Application cannot be submitted because the linked consent request is not approved."
					),
					frappe.ValidationError,
				)

			# If no consent is explicitly linked on the profile, check for an approved consent owned by the profile's user
			profile_user = frappe.db.get_value("A2C Farmer Profile", self.farmer_profile, "user")
			if profile_user:
				owned = frappe.db.get_value(
					"A2C Consent Request",
					{"owner": profile_user, "status": "Approved"},
					"name",
					order_by="creation desc",
				)
				if owned:
					self.consent_id = owned
					return

		if self.lead_id:
			lead_consent = frappe.db.get_value("A2C Lead", self.lead_id, "consent_id")
			if (
				lead_consent
				and frappe.db.get_value("A2C Consent Request", lead_consent, "status") == "Approved"
			):
				self.consent_id = lead_consent
				return
			approved_by_lead_ref = frappe.db.get_value(
				"A2C Consent Request",
				{"reference_doctype": "A2C Lead", "reference_name": self.lead_id, "status": "Approved"},
				"name",
			)
			if approved_by_lead_ref:
				self.consent_id = approved_by_lead_ref
				return

		frappe.throw(
			_("Loan Application cannot be submitted without an approved consent request."),
			frappe.ValidationError,
		)

	def after_insert(self):
		"""Notify the bank's team that a new application has landed."""
		if self.status == "Active":
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
		if self.is_new() or (
			not self.has_value_changed("status") and not self.has_value_changed("stage_label")
		):
			return

		actor = frappe.session.user
		old_status = self.get_value_before_save("status")

		if old_status == "Active" and self.status != "Active":
			label = self.lead_id or self.name
			notify_users(
				self._bank_recipients(),
				subject="New loan application submitted",
				message=f"New loan application submitted for {label} ({self.loan_product or 'no product'})",
				doctype="A2C Loan Application",
				docname=self.name,
			)
			return

		if self.status == "Active":
			return

		stage_name = self.stage_label or self.status
		notify_users(
			self._bank_recipients(),
			subject=f"Loan application {self.name} is now {stage_name}",
			message=(
				f"Loan Application {self.name} for {self.lead_id or self.name} "
				f"has been {stage_name} by {actor}"
			),
			doctype="A2C Loan Application",
			docname=self.name,
		)

	def validate(self):
		if self.is_new() and self.lead_id:
			lead_status, lead_source = frappe.db.get_value(
				"A2C Lead", self.lead_id, ["status", "lead_source"]
			)
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

		# Only on create or when the amount/product actually moved. Re-running this on
		# every save would let a bank narrowing its product range retroactively wedge
		# live applications: the workflow save inside apply_status_transition, the
		# Completed submit and any approved_amount edit would all start throwing, with
		# no way left to advance or cancel the application.
		if self.loan_product and (
			self.is_new()
			or self.has_value_changed("loan_product")
			or self.has_value_changed("requested_amount")
			or self.has_value_changed("loan_amount")
		):
			product_amounts = frappe.db.get_value(
				"A2C Loan Product", self.loan_product, ["min_amount", "max_amount"], as_dict=True
			)
			if product_amounts:
				amount = self.requested_amount or self.loan_amount
				if amount:
					from oan_a2c.api.utils import assert_amount_within_product_range

					assert_amount_within_product_range(
						amount, product_amounts.get("min_amount"), product_amounts.get("max_amount")
					)

		self._sync_bank_from_product()
		self._sync_loan_type_from_product()
		self._sync_term_snapshot_from_product()

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

	def _sync_loan_type_from_product(self):
		"""Keep loan_type in sync with term_snapshot or loan product's taxonomy category."""
		if self.loan_type:
			return

		# 1. Fallback to existing term_snapshot child table if present
		for row in self.get("term_snapshot") or []:
			if row.taxonomy == "Category" and row.term_name:
				self.loan_type = row.term_name
				return

		if not self.loan_product:
			return

		# 2. Derive from product's category taxonomy
		category = frappe.db.get_value(
			"A2C Term Relationship",
			{"loan_product": self.loan_product, "term_type": "Category"},
			"term_category",
		)
		if category:
			term = frappe.db.get_value("A2C Term Category", category, "term")
			loan_type = frappe.db.get_value("A2C Term", term, "term_name") if term else None
			self.loan_type = loan_type or category

	def _sync_term_snapshot_from_product(self):
		"""Snapshot category and tag terms into term_snapshot child table if not already populated."""
		if not self.loan_product or self.get("term_snapshot"):
			return

		rels = frappe.get_all(
			"A2C Term Relationship",
			filters={"loan_product": self.loan_product},
			fields=["term_type", "term_category", "term_tag"],
		)
		if not rels:
			return

		for r in rels:
			if r.term_type == "Category" and r.term_category:
				term = frappe.db.get_value("A2C Term Category", r.term_category, "term")
				term_doc = (
					frappe.db.get_value("A2C Term", term, ["term_name", "slug"], as_dict=True)
					if term
					else None
				)
				self.append(
					"term_snapshot",
					{
						"taxonomy": "Category",
						"term_name": term_doc.term_name if term_doc else r.term_category,
						"term_slug": term_doc.slug if term_doc else None,
						"source_ref": r.term_category,
					},
				)
			elif r.term_type == "Tag" and r.term_tag:
				term = frappe.db.get_value("A2C Term Tag", r.term_tag, "term")
				term_doc = (
					frappe.db.get_value("A2C Term", term, ["term_name", "slug"], as_dict=True)
					if term
					else None
				)
				self.append(
					"term_snapshot",
					{
						"taxonomy": "Tag",
						"term_name": term_doc.term_name if term_doc else r.term_tag,
						"term_slug": term_doc.slug if term_doc else None,
						"source_ref": r.term_tag,
					},
				)
