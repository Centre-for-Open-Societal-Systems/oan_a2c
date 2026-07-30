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

	def before_save(self):
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
