# Copyright (c) 2026, OpenAgriNet and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document

from oan_a2c.a2c_marketplace.lookups import refresh_product_lookups
from oan_a2c.a2c_marketplace.permissions import get_bank_members
from oan_a2c.a2c_marketplace.roles import ADMIN_ROLE, BANK_ADMIN_ROLE, BANK_ROLES
from oan_a2c.api.v1.notifications import notify_users


class A2CLoanProduct(Document):
	def after_insert(self):
		"""Notify Bank Admins that a new (Pending Approval) product needs activation."""
		notify_users(
			get_bank_members(self.bank, roles=[BANK_ADMIN_ROLE]),
			subject="New loan product created",
			message=f"New loan product created: {self.product_name} ({self.bank})",
			doctype="A2C Loan Product",
			docname=self.name,
		)

	def validate(self):
		from pydantic import ValidationError

		from oan_a2c.a2c_marketplace.doctype_schemas import SingleProductSchema

		data = {
			"product_name": self.product_name,
			"min_interest_rate": self.min_interest_rate,
			"max_interest_rate": self.max_interest_rate,
			"min_amount": self.min_amount,
			"max_amount": self.max_amount,
			"tenure_months": self.tenure_months,
			"description": self.description,
			"image": self.image,
		}

		try:
			SingleProductSchema.model_validate(data)
		except ValidationError as exc:
			messages = []
			for err in exc.errors():
				msg = err.get("msg", "")
				if msg.startswith("Value error, "):
					msg = msg[len("Value error, ") :]
				loc = ".".join([str(l) for l in err.get("loc", [])])
				messages.append(f"{loc}: {msg}" if loc else msg)
			frappe.throw("; ".join(messages), frappe.ValidationError)

	# Content fields whose edit invalidates a prior approval — changing any of
	# these on an already-approved product forces it back to Pending Approval for re-review.
	CONTENT_FIELDS = (
		"product_name",
		"min_interest_rate",
		"max_interest_rate",
		"min_amount",
		"max_amount",
		"tenure_months",
		"description",
		"image",
	)

	def before_save(self):
		user_roles = frappe.get_roles()
		is_bank_admin = (
			BANK_ADMIN_ROLE in user_roles or ADMIN_ROLE in user_roles or "System Manager" in user_roles
		)

		if self.is_new():
			if is_bank_admin:
				self.status = "Active"
		else:
			before = self.get_doc_before_save()
			if before:
				has_content_changes = any(
					self.has_value_changed(f) and (before.get(f) or self.get(f)) for f in self.CONTENT_FIELDS
				)

				if has_content_changes and is_bank_admin:
					self.status = "Active"

				# 1. Editing content fields on an approved (Active) product is forbidden for non-admins.
				if before.status == "Active" and not is_bank_admin:
					if has_content_changes:
						frappe.throw(
							frappe._("Cannot edit a loan product once it is approved (Active)."),
							frappe.PermissionError,
						)

				# 2. Editing a Rejected product resubmits it for approval (Pending Approval) for non-admins.
				if before.status == "Rejected" and not is_bank_admin:
					if has_content_changes:
						self.status = "Pending Approval"

		if self.product_name:
			base_slug = frappe.scrub(self.product_name).replace("_", "-")
			candidate_slug = f"{self.bank}-{base_slug}"
			if frappe.db.exists(
				"A2C Loan Product", {"slug": candidate_slug, "name": ["!=", self.name or ""]}
			):
				frappe.throw(
					frappe._("A loan product named '{0}' already exists for your bank.").format(
						self.product_name
					),
					frappe.ValidationError,
				)
			self.slug = candidate_slug

	def on_update(self):
		refresh_product_lookups(self)

		before = self.get_doc_before_save()
		if before:
			status_changed = before.status != self.status
			changed_fields = {}
			for f in self.CONTENT_FIELDS:
				if self.has_value_changed(f):
					old_val = before.get(f)
					new_val = self.get(f)
					if str(old_val or "") != str(new_val or ""):
						changed_fields[f] = (old_val, new_val)

			if status_changed or changed_fields:
				from oan_a2c.a2c_marketplace.doctype.a2c_loan_product_audit_event.a2c_loan_product_audit_event import (
					log_product_audit_event,
				)

				log_product_audit_event(
					product_doc=self,
					event_type="Status Changed" if status_changed else "Product Updated",
					from_status=before.status if status_changed else None,
					to_status=self.status if status_changed else None,
					reason=getattr(self, "_status_reason", None),
					changed_fields=changed_fields,
				)

			# Notify the bank team on an actual status change
			if status_changed:
				msg = f"Loan product {self.product_name} ({self.bank}) is now {self.status}"
				reason = getattr(self, "_status_reason", None)
				if reason:
					msg += f". Reason: {reason}"

				notify_users(
					get_bank_members(self.bank, roles=BANK_ROLES),
					subject=f"Loan product {self.product_name} is now {self.status}",
					message=msg,
					doctype="A2C Loan Product",
					docname=self.name,
				)
