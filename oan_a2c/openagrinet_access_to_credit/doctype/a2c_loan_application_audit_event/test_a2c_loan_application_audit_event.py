import frappe
from frappe.tests import IntegrationTestCase

from oan_a2c.a2c_marketplace.roles import BANK_ADMIN_ROLE, BANK_AGENT_ROLE


class IntegrationTestA2CLoanApplicationAuditEvent(IntegrationTestCase):
	"""
	Integration tests for A2CLoanApplicationAuditEvent.
	Verifies bank scoping and permissions for Bank Admin and Bank Agent.
	"""

	def setUp(self):
		frappe.set_user("Administrator")

	def test_audit_event_bank_scoping_and_permissions(self):
		# Create test banks
		bank1_name = "TEST_BANK_AUDIT_1"
		bank2_name = "TEST_BANK_AUDIT_2"

		for b_name in [bank1_name, bank2_name]:
			if not frappe.db.exists("A2C Participating Bank", b_name):
				frappe.get_doc(
					{
						"doctype": "A2C Participating Bank",
						"bank_code": b_name,
						"bank_name": f"Test Bank {b_name}",
						"entity_type": "Commercial Bank",
						"registered_street": "123 Test St",
						"registered_region": "Region",
						"registered_country": "Country",
						"registered_postal_code": "1000",
						"registered_email": f"{b_name.lower()}@test.com",
						"registered_phone": "+251900000000",
						"status": "Active",
					}
				).insert(ignore_permissions=True)

		# Create test user for Bank 1 (Bank Admin)
		user_email = "test_bank_admin_audit@example.com"
		if not frappe.db.exists("User", user_email):
			user_doc = frappe.get_doc(
				{
					"doctype": "User",
					"email": user_email,
					"first_name": "Test Bank",
					"last_name": "Admin Audit",
					"send_welcome_email": 0,
					"roles": [{"role": BANK_ADMIN_ROLE}],
				}
			)
			user_doc.insert(ignore_permissions=True)

			frappe.get_doc(
				{
					"doctype": "User Permission",
					"user": user_email,
					"allow": "A2C Participating Bank",
					"for_value": bank1_name,
				}
			).insert(ignore_permissions=True)

		# Create audit event for Bank 1
		event1 = frappe.get_doc(
			{
				"doctype": "A2C Loan Application Audit Event",
				"loan_application": "APP-TEST-001",
				"bank": bank1_name,
				"event_type": "Status Changed",
				"event_title": "Status Updated",
				"event_description": "Loan status updated for Bank 1",
			}
		).insert(ignore_permissions=True)

		# Create audit event for Bank 2
		event2 = frappe.get_doc(
			{
				"doctype": "A2C Loan Application Audit Event",
				"loan_application": "APP-TEST-002",
				"bank": bank2_name,
				"event_type": "Status Changed",
				"event_title": "Status Updated",
				"event_description": "Loan status updated for Bank 2",
			}
		).insert(ignore_permissions=True)

		# Switch to Bank Admin of Bank 1
		frappe.set_user(user_email)

		# Check list query (bank_scope_query enforcement)
		list_events = frappe.get_list("A2C Loan Application Audit Event", fields=["name", "bank"])
		event_names = [e["name"] for e in list_events]
		self.assertIn(event1.name, event_names)
		self.assertNotIn(event2.name, event_names)

		# Check single doc permission (bank_scope_doc enforcement)
		self.assertTrue(frappe.has_permission("A2C Loan Application Audit Event", doc=event1, ptype="read"))
		self.assertFalse(frappe.has_permission("A2C Loan Application Audit Event", doc=event2, ptype="read"))

		# Cleanup
		frappe.set_user("Administrator")
		frappe.delete_doc("A2C Loan Application Audit Event", event1.name, ignore_permissions=True)
		frappe.delete_doc("A2C Loan Application Audit Event", event2.name, ignore_permissions=True)
