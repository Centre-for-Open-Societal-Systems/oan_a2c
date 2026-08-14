# Copyright (c) 2026, OpenAgriNet and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document

from oan_a2c.a2c_marketplace.lookups import refresh_product_lookups
from oan_a2c.a2c_marketplace.permissions import get_bank_members
from oan_a2c.a2c_marketplace.roles import BANK_ADMIN_ROLE, BANK_ROLES
from oan_a2c.api.v1.notifications import notify_users


class A2CLoanProduct(Document):
	def after_insert(self):
		"""Notify Bank Admins that a new (Draft) product needs activation."""
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
	# these on an already-approved product forces it back to Draft for re-review.
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
		# Revert to Draft when the product's content is edited after approval, so
		# an Active/Archived product must be re-activated. A pure status change
		# (e.g. set_product_status activating the product) touches no content
		# field, so it is left untouched.
		before = self.get_doc_before_save()
		if not self.is_new() and before and self.status != "Draft":
			for f in self.CONTENT_FIELDS:
				if self.has_value_changed(f):
					# Ignore empty→empty normalization (None vs "" vs 0); a real
					# edit like 5→0 still reverts because the old value is truthy.
					if not before.get(f) and not self.get(f):
						continue
					self.status = "Draft"
					break

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

		# Notify the bank team only on an actual status change (on_update fires on
		# every save, including after insert where _doc_before_save is None).
		if self.get_doc_before_save() and self.has_value_changed("status"):
			notify_users(
				get_bank_members(self.bank, roles=BANK_ROLES),
				subject=f"Loan product {self.product_name} is now {self.status}",
				message=f"Loan product {self.product_name} ({self.bank}) is now {self.status}",
				doctype="A2C Loan Product",
				docname=self.name,
			)
