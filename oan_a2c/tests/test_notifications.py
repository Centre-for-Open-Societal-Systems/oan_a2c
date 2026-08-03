import unittest

import frappe

from oan_a2c.a2c_marketplace.roles import BANK_ADMIN_ROLE, BANK_AGENT_ROLE
from oan_a2c.api.v1 import notifications as notif_api

BANK = "TEST_NOTIF_BANK"
ADMIN_USER = "notif_admin@example.com"
AGENT_USER = "notif_agent@example.com"


def _ensure_user(email, role):
	if not frappe.db.exists("User", email):
		frappe.get_doc(
			{
				"doctype": "User",
				"email": email,
				"first_name": email.split("@")[0],
				"send_welcome_email": 0,
			}
		).insert(ignore_permissions=True)
	user = frappe.get_doc("User", email)
	if role not in [r.role for r in user.roles]:
		user.add_roles(role)
	if not frappe.db.exists(
		"User Permission", {"user": email, "allow": "A2C Participating Bank", "for_value": BANK}
	):
		frappe.get_doc(
			{
				"doctype": "User Permission",
				"user": email,
				"allow": "A2C Participating Bank",
				"for_value": BANK,
			}
		).insert(ignore_permissions=True)


class TestNotifications(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		frappe.set_user("Administrator")
		if not frappe.db.exists("A2C Participating Bank", BANK):
			bank = frappe.get_doc(
				{
					"doctype": "A2C Participating Bank",
					"bank_name": "Test Notif Bank",
					"bank_code": BANK,
					"status": "In Review",
					"entity_type": "Commercial Bank",
					"registered_email": "notif@test.com",
					"registered_phone": "+251911000000",
					"registered_city": "Addis Ababa",
					"registered_country": "Ethiopia",
				}
			).insert(ignore_permissions=True)
			frappe.db.sql("UPDATE `tabA2C Participating Bank` SET name=%s WHERE name=%s", (BANK, bank.name))
		_ensure_user(ADMIN_USER, BANK_ADMIN_ROLE)
		_ensure_user(AGENT_USER, BANK_AGENT_ROLE)
		frappe.db.commit()

	def setUp(self):
		frappe.set_user("Administrator")
		frappe.db.delete("Notification Log", {"for_user": ["in", [ADMIN_USER, AGENT_USER]]})
		frappe.db.delete("A2C Loan Product", {"bank": BANK})
		frappe.db.commit()

	def _logs_for(self, user, doctype=None):
		filters = {"for_user": user}
		if doctype:
			filters["document_type"] = doctype
		return frappe.get_all("Notification Log", filters=filters, fields=["subject", "document_name"])

	def _make_product(self, status="Draft"):
		return frappe.get_doc(
			{
				"doctype": "A2C Loan Product",
				"product_name": f"Notif Product {frappe.generate_hash(length=6)}",
				"bank": BANK,
				"min_interest_rate": 5,
				"max_amount": 1000,
				"tenure_months": 12,
				"status": status,
			}
		).insert(ignore_permissions=True)

	# --- Loan product ------------------------------------------------------

	def test_product_insert_notifies_bank_admin(self):
		"""after_insert on a Draft product notifies Bank Admins only."""
		frappe.set_user("Administrator")
		self._make_product()
		self.assertTrue(self._logs_for(ADMIN_USER, "A2C Loan Product"))
		# Agents are not notified about a new Draft product.
		self.assertFalse(self._logs_for(AGENT_USER, "A2C Loan Product"))

	def test_product_status_change_notifies_team(self):
		"""on_update notifies admins + agents when status changes (Draft -> Active)."""
		frappe.set_user("Administrator")
		product = self._make_product(status="Draft")
		frappe.db.delete("Notification Log", {"for_user": ["in", [ADMIN_USER, AGENT_USER]]})
		frappe.db.commit()

		product.status = "Active"
		product.save(ignore_permissions=True)

		self.assertTrue(self._logs_for(ADMIN_USER, "A2C Loan Product"))
		self.assertTrue(self._logs_for(AGENT_USER, "A2C Loan Product"))

	def test_product_save_without_status_change_is_silent(self):
		"""A save that does not change status must not spam notifications."""
		frappe.set_user("Administrator")
		product = self._make_product(status="Active")
		frappe.db.delete("Notification Log", {"for_user": ["in", [ADMIN_USER, AGENT_USER]]})
		frappe.db.commit()

		product.max_amount = 2000
		product.save(ignore_permissions=True)

		self.assertFalse(self._logs_for(ADMIN_USER, "A2C Loan Product"))

	# --- Actor exclusion ---------------------------------------------------

	def test_actor_excluded_from_own_notification(self):
		"""The user who acts is never notified about their own action."""
		frappe.set_user(ADMIN_USER)
		try:
			self._make_product()
		finally:
			frappe.set_user("Administrator")
		self.assertFalse(self._logs_for(ADMIN_USER, "A2C Loan Product"))
		# Agent (a different bank member) is unaffected by exclusion of the actor —
		# but agents are not on the Draft-insert list anyway, so nobody is notified.

	# --- API endpoints -----------------------------------------------------

	def test_get_mark_read_and_clear(self):
		frappe.set_user("Administrator")
		notif_api.notify_users([AGENT_USER], subject="Hello agent", doctype="A2C Loan Product", docname="X")
		frappe.db.commit()

		frappe.set_user(AGENT_USER)
		# Endpoints return the handle_api_errors envelope: payload lives under "data".
		data = notif_api.get_notifications(read_status="all", limit=20, start=0)["data"]
		self.assertEqual(data["unread_count"], 1)
		self.assertEqual(len(data["notifications"]), 1)
		nid = data["notifications"][0]["id"]

		notif_api.mark_read(notification_ids=[nid], mark_all=False)
		data = notif_api.get_notifications(read_status="unread", limit=20, start=0)["data"]
		self.assertEqual(data["unread_count"], 0)

		notif_api.clear(notification_ids=[nid], clear_all=False)
		data = notif_api.get_notifications(read_status="all", limit=20, start=0)["data"]
		self.assertEqual(len(data["notifications"]), 0)
		frappe.set_user("Administrator")
