import unittest

import frappe


class TestA2CLoanProductAuditEvent(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		cls.bank_code = "TEST_AUDIT_BANK"
		if not frappe.db.exists("A2C Participating Bank", cls.bank_code):
			frappe.get_doc(
				{
					"doctype": "A2C Participating Bank",
					"kyc_document": "/private/files/test_kyc.pdf",
					"gro_name": "Test GRO",
					"ops_name": "Test Ops",
					"bank_code": cls.bank_code,
					"bank_name": "Test Audit Bank",
					"entity_type": "Commercial Bank",
					"registered_street": "123 Test St",
					"registered_region": "Addis Ababa",
					"registered_country": "Ethiopia",
					"registered_postal_code": "100000",
					"registered_email": "audit_bank@test.com",
					"registered_phone": "+251900000000",
					"status": "Active",
				}
			).insert(ignore_permissions=True)

	def test_audit_event_logged_on_status_change(self):
		product = frappe.get_doc(
			{
				"doctype": "A2C Loan Product",
				"product_name": f"Audit Test Product {frappe.generate_hash(length=4)}",
				"bank": self.bank_code,
				"min_interest_rate": 5,
				"max_amount": 1000,
				"tenure_months": 12,
				"status": "Pending Approval",
			}
		).insert(ignore_permissions=True)

		from oan_a2c.api.v1.seller.loan_products import set_product_status

		# Reject product with reason
		frappe.set_user("Administrator")
		set_product_status(
			product_id=product.name, status="Rejected", reason="Interest rate policy violation"
		)

		# Check audit log created
		audit_logs = frappe.get_all(
			"A2C Loan Product Audit Event",
			filters={"loan_product": product.name},
			fields=["from_status", "to_status", "reason", "event_description"],
		)
		self.assertTrue(len(audit_logs) >= 1)
		latest_log = audit_logs[0]
		self.assertEqual(latest_log["from_status"], "Pending Approval")
		self.assertEqual(latest_log["to_status"], "Rejected")
		self.assertEqual(latest_log["reason"], "Interest rate policy violation")

	def test_audit_event_logged_on_resubmission_with_field_diffs(self):
		product = frappe.get_doc(
			{
				"doctype": "A2C Loan Product",
				"product_name": f"Resubmit Audit Product {frappe.generate_hash(length=4)}",
				"bank": self.bank_code,
				"min_interest_rate": 15,
				"max_amount": 500000,
				"tenure_months": 12,
				"status": "Rejected",
			}
		).insert(ignore_permissions=True)

		from oan_a2c.api.v1.seller.loan_products import update_product

		# Resubmit product with lower interest rate
		frappe.set_user("Administrator")
		update_product(product_id=product.name, min_interest_rate=10, reason="Adjusted interest rate to 10%")

		# Verify status changed back to Pending Approval
		product.reload()
		self.assertEqual(product.status, "Pending Approval")

		# Verify audit event captured field diffs
		audit_logs = frappe.get_all(
			"A2C Loan Product Audit Event",
			filters={"loan_product": product.name},
			fields=["from_status", "to_status", "reason", "event_description"],
		)
		self.assertTrue(len(audit_logs) >= 1)
		latest_log = audit_logs[0]
		self.assertEqual(latest_log["from_status"], "Rejected")
		self.assertEqual(latest_log["to_status"], "Pending Approval")
		self.assertIn("min_interest_rate", latest_log["event_description"])

	def test_rejection_and_approval_require_reason(self):
		product = frappe.get_doc(
			{
				"doctype": "A2C Loan Product",
				"product_name": f"Reason Req Test {frappe.generate_hash(length=4)}",
				"bank": self.bank_code,
				"min_interest_rate": 5,
				"max_amount": 1000,
				"tenure_months": 12,
				"status": "Pending Approval",
			}
		).insert(ignore_permissions=True)

		from oan_a2c.api.v1.seller.loan_products import set_product_status

		frappe.set_user("Administrator")

		# Missing reason for Rejection -> error
		res = set_product_status(product_id=product.name, status="Rejected")
		self.assertEqual(res.get("status"), "error")
		self.assertEqual(res.get("code"), "VALIDATION_ERROR")
		self.assertIn(
			"Please provide a reason", res.get("details", {}).get("reason", "") or res.get("message", "")
		)

		# Missing reason for Active -> error
		res_act = set_product_status(product_id=product.name, status="Active")
		self.assertEqual(res_act.get("status"), "error")
		self.assertEqual(res_act.get("code"), "VALIDATION_ERROR")
		self.assertIn(
			"Please provide a reason",
			res_act.get("details", {}).get("reason", "") or res_act.get("message", ""),
		)
