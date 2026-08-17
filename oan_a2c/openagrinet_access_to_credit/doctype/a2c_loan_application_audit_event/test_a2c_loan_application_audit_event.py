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
		# Create test banks. A2C Participating Bank autonames as PB-####, so the
		# document name differs from bank_code — capture the real names to use as
		# link values below.
		bank_names = {}
		for b_code in ["TEST_BANK_AUDIT_1", "TEST_BANK_AUDIT_2"]:
			existing = frappe.db.get_value("A2C Participating Bank", {"bank_code": b_code}, "name")
			if existing:
				bank_names[b_code] = existing
				continue
			bank = frappe.get_doc(
				{
					"doctype": "A2C Participating Bank",
					"registered_city": "Test City",
					"kyc_document": "/private/files/test_kyc.pdf",
					"gro_name": "Test GRO",
					"ops_name": "Test Ops",
					"bank_code": b_code,
					"bank_name": f"Test Bank {b_code}",
					"entity_type": "Commercial Bank",
					"registered_street": "123 Test St",
					"registered_region": "Region",
					"registered_country": "Country",
					"registered_postal_code": "1000",
					"registered_email": f"{b_code.lower()}@test.com",
					"registered_phone": "+251900000000",
					"status": "Active",
				}
			).insert(ignore_permissions=True)
			bank_names[b_code] = bank.name

		bank1_name = bank_names["TEST_BANK_AUDIT_1"]
		bank2_name = bank_names["TEST_BANK_AUDIT_2"]

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

		# loan_application is a required Link — create a real application per bank.
		def _make_loan_application():
			app = frappe.new_doc("A2C Loan Application")
			app.first_name = "Test"
			app.last_name = "Farmer"
			app.phone_number = "0912345678"
			app.requested_amount = 5000
			app.loan_type = "Input Loan"
			app.insert(ignore_mandatory=True, ignore_links=True, ignore_permissions=True)
			return app.name

		app1_name = _make_loan_application()
		app2_name = _make_loan_application()

		# Create audit event for Bank 1
		event1 = frappe.get_doc(
			{
				"doctype": "A2C Loan Application Audit Event",
				"loan_application": app1_name,
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
				"loan_application": app2_name,
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
