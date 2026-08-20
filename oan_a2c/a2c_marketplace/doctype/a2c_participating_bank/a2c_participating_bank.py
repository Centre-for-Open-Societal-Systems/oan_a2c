# Copyright (c) 2026, OpenAgriNet and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document


class A2CParticipatingBank(Document):
	def after_insert(self):
		# Seed default stages for the newly created bank
		default_stages = [
			{"label": "Submitted", "archetype_state": "In Transition", "sequence": 1},
			{"label": "Processed", "archetype_state": "In Transition", "sequence": 2},
			{"label": "Verified", "archetype_state": "In Transition", "sequence": 3},
			{"label": "Approved", "archetype_state": "In Transition", "sequence": 4},
			{"label": "Disbursed", "archetype_state": "Completed", "sequence": 5},
			{"label": "Rejected", "archetype_state": "Completed", "sequence": 6},
		]
		for stage in default_stages:
			frappe.get_doc(
				{
					"doctype": "A2C Loan Status Stage",
					"bank": self.name,
					"label": stage["label"],
					"archetype_state": stage["archetype_state"],
					"sequence": stage["sequence"],
				}
			).insert(ignore_permissions=True)

	def on_update(self):
		# Only enforce activation requirements (and run status side effects) when the
		# status actually transitions. on_update fires on every save, so gating on
		# has_value_changed keeps unrelated edits (contacts, profile) from re-triggering
		# the KYC/contacts guard once the bank is already Active.
		if not self.has_value_changed("status"):
			return

		if self.status == "Active":
			if not self.kyc_document:
				frappe.throw(_("KYC Document is required to activate the bank."))
			if not self.gro_name or not self.ops_name:
				frappe.throw(_("GRO Name and Ops Name are required to activate the bank."))

		# Handle Suspended / Active side effects on User records.
		# If suspended, disable users. If active, enable users.
		if self.status == "Suspended":
			self.set_users_enabled(0)
		elif self.status == "Active":
			self.set_users_enabled(1)

	def set_users_enabled(self, enabled):
		users = frappe.get_all(
			"User Permission",
			filters={"allow": "A2C Participating Bank", "for_value": self.name},
			fields=["user"],
		)
		for u in users:
			frappe.db.set_value("User", u.user, "enabled", enabled)
