# Copyright (c) 2026, OpenAgriNet and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document


class A2CSavedProduct(Document):
	def validate(self):
		"""Doctype-layer integrity for bookmarks.

		The API layer is not the only writer -- Desk, data import and other apps
		can reach this doctype -- so the two invariants live here:

		1. A row always belongs to the user creating it. DocPerm on this doctype is
		   role "All", so without this a user could POST `user` pointing at someone
		   else and write into another person's saved list. Platform admins are
		   exempt so support can curate on a user's behalf.
		2. A product can only be bookmarked once per user. A unique index on
		   (user, loan_product) is the real guarantee under concurrency; this check
		   only exists to turn that index violation into a readable message.
		"""
		from oan_a2c.a2c_marketplace.permissions import is_platform_admin

		if not is_platform_admin():
			self.user = frappe.session.user
		elif not self.user:
			self.user = frappe.session.user

		if not self.is_new():
			return

		if frappe.db.exists(
			"A2C Saved Product",
			{"user": self.user, "loan_product": self.loan_product, "name": ["!=", self.name]},
		):
			frappe.throw(_("This product is already saved."), frappe.DuplicateEntryError)
