import unittest

import frappe


class TestA2CLoanProductAuditEvent(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		cls.bank_code = "TEST_AUDIT_BANK"
		bank_name = frappe.db.exists("A2C Participating Bank", {"bank_code": cls.bank_code})
		if not bank_name:
			bank_doc = frappe.get_doc(
				{
					"doctype": "A2C Participating Bank",
					"registered_city": "Test City",
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
			bank_name = bank_doc.name
		cls.bank_name = bank_name

		cls.agent_email = "audit_agent@test.com"
		if not frappe.db.exists("User", cls.agent_email):
			frappe.get_doc({
				"doctype": "User",
				"email": cls.agent_email,
				"first_name": "Audit Agent",
				"roles": [{"role": "A2C Bank Agent"}]
			}).insert(ignore_permissions=True, ignore_mandatory=True)

		if not frappe.db.exists("User Permission", {"user": cls.agent_email, "allow": "A2C Participating Bank", "for_value": cls.bank_name}):
			frappe.get_doc({
				"doctype": "User Permission",
				"user": cls.agent_email,
				"allow": "A2C Participating Bank",
				"for_value": cls.bank_name
			}).insert(ignore_permissions=True)

	def test_audit_event_logged_on_status_change(self):
		frappe.set_user(self.agent_email)
		product = frappe.get_doc(
			{
				"doctype": "A2C Loan Product",
				"product_name": f"Audit Test Product {frappe.generate_hash(length=4)}",
				"bank": self.bank_name,
				"min_interest_rate": 5,
				"max_interest_rate": 15,
				"min_amount": 100,
				"max_amount": 1000,
				"tenure_months": 12,
				"status": "Pending Approval",
			}
		).insert(ignore_permissions=True)

		from oan_a2c.api.v1.seller.loan_products import set_product_status

		# Reject product with reason
		frappe.set_user("Administrator")
		res = set_product_status(
			product_id=product.name, status="Rejected", reason="Interest rate policy violation"
		)
		self.assertEqual(res.get("status"), "success", str(res))

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
		frappe.set_user(self.agent_email)
		product = frappe.get_doc(
			{
				"doctype": "A2C Loan Product",
				"product_name": f"Resubmission Test {frappe.generate_hash(length=4)}",
				"bank": self.bank_name,
				"min_interest_rate": 15,
				"max_interest_rate": 20,
				"min_amount": 1000,
				"max_amount": 500000,
				"tenure_months": 12,
				"status": "Rejected",
			}
		).insert(ignore_permissions=True)

		from oan_a2c.api.v1.seller.loan_products import update_product

		# Resubmit product with lower interest rate
		frappe.set_user(self.agent_email)
		res = update_product(
			product_id=product.name, min_interest_rate=10, reason="Adjusted interest rate to 10%"
		)
		self.assertEqual(res.get("status"), "success", str(res))

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
		frappe.set_user(self.agent_email)
		product = frappe.get_doc(
			{
				"doctype": "A2C Loan Product",
				"product_name": f"Reason Req Test {frappe.generate_hash(length=4)}",
				"bank": self.bank_name,
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
		self.assertEqual(res.get("status"), "error")
		self.assertIn("Please provide a reason", str(res))

		# Missing reason for Active -> error
		res_act = set_product_status(product_id=product.name, status="Active")
		self.assertEqual(res_act.get("status"), "error")
		self.assertEqual(res_act.get("code"), "VALIDATION_ERROR")
		self.assertIn(
			"Please provide a reason",
			str(res_act),
		)

	def test_archive_is_reversible_and_any_product_can_be_archived(self):
		frappe.set_user(self.agent_email)
		product = frappe.get_doc(
			{
				"doctype": "A2C Loan Product",
				"product_name": f"Archive Test {frappe.generate_hash(length=4)}",
				"bank": self.bank_name,
				"min_interest_rate": 5,
				"max_interest_rate": 15,
				"min_amount": 100,
				"max_amount": 1000,
				"tenure_months": 12,
				"status": "Pending Approval",
			}
		).insert(ignore_permissions=True)

		from oan_a2c.api.v1.seller.loan_products import set_product_status

		frappe.set_user("Administrator")

		# Archiving requires a reason.
		res_noreason = set_product_status(product_id=product.name, status="Archived")
		self.assertEqual(res_noreason.get("status"), "error")
		self.assertIn("Please provide a reason", str(res_noreason))

		# Any product (e.g. Pending Approval) can be archived with a reason.
		res_arch_pending = set_product_status(
			product_id=product.name, status="Archived", reason="Archived from Pending Approval"
		)
		self.assertEqual(res_arch_pending.get("status"), "success", str(res_arch_pending))
		self.assertEqual(res_arch_pending.get("data", {}).get("status"), "Archived")

		# Archived -> Active (restore), then Active -> Archived (retire).
		res_activate = set_product_status(product_id=product.name, status="Active", reason="Approved")
		self.assertEqual(res_activate.get("status"), "success", str(res_activate))

		res_arch = set_product_status(
			product_id=product.name, status="Archived", reason="Product discontinued"
		)
		self.assertEqual(res_arch.get("status"), "success", str(res_arch))
		self.assertEqual(res_arch.get("data", {}).get("status"), "Archived")

		res_restore = set_product_status(product_id=product.name, status="Active", reason="Relaunched")
		self.assertEqual(res_restore.get("status"), "success", str(res_restore))
		self.assertEqual(res_restore.get("data", {}).get("status"), "Active")

		# The transitions to Archived are captured in the audit trail.
		audit_logs = frappe.get_all(
			"A2C Loan Product Audit Event",
			filters={"loan_product": product.name, "to_status": "Archived"},
			fields=["from_status", "to_status", "reason"],
			order_by="creation asc",
		)
		self.assertTrue(len(audit_logs) >= 2)
		self.assertEqual(audit_logs[0]["from_status"], "Pending Approval")
		self.assertEqual(audit_logs[0]["reason"], "Archived from Pending Approval")
		self.assertEqual(audit_logs[1]["from_status"], "Active")
		self.assertEqual(audit_logs[1]["reason"], "Product discontinued")
