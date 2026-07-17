import unittest

import frappe

from oan_a2c.api.v1.leads import add_lead_credit_info, get_lead_credit_infos, get_lead_metadata


class TestLeadCreditInfo(unittest.TestCase):
	"""
	Tests for the A2C Credit Information DocType, controller validations, and REST APIs.
	"""

	TEST_PHONE = "+251977000001"

	@classmethod
	def setUpClass(cls):
		frappe.set_user("Administrator")
		cls._clear_records()

		# Create a test Lead to link to
		cls.lead = frappe.new_doc("A2C Lead")
		cls.lead.phone_number = cls.TEST_PHONE
		cls.lead.lead_source = "Agent Entry"
		cls.lead.status = "Active"
		cls.lead.insert(ignore_permissions=True)
		cls.lead_id = cls.lead.name
		frappe.db.commit()

	@classmethod
	def tearDownClass(cls):
		frappe.set_user("Administrator")
		cls._clear_records()
		frappe.db.commit()

	@classmethod
	def _clear_records(cls):
		# Delete all Credit Information records
		for name in frappe.get_all("A2C Credit Information", pluck="name"):
			frappe.delete_doc("A2C Credit Information", name, ignore_permissions=True, force=True)

		# Delete our specific testing lead
		for name in frappe.get_all("A2C Lead", filters={"phone_number": cls.TEST_PHONE}, pluck="name"):
			frappe.delete_doc("A2C Lead", name, ignore_permissions=True, force=True)

	def setUp(self):
		frappe.set_user("Administrator")
		# Delete credit information records before each test
		for name in frappe.get_all("A2C Credit Information", pluck="name"):
			frappe.delete_doc("A2C Credit Information", name, ignore_permissions=True, force=True)
		frappe.db.commit()

	def test_1_create_credit_info_success(self):
		"""Verifies that a valid credit info document is created and inserts timeline comment."""
		res = add_lead_credit_info(
			lead_id=self.lead_id,
			loan_type="Input loan (seeds, agrochemicals)",
			loan_amount=90000,
			purpose_message="Seeds and fertilizer for next planting season.",
		)

		self.assertEqual(res["status"], "success")
		self.assertTrue(res["data"]["credit_info_id"].startswith("LCR-"))

		# Verify DB values
		doc = frappe.get_doc("A2C Credit Information", res["data"]["credit_info_id"])
		self.assertEqual(doc.lead, self.lead_id)
		self.assertEqual(doc.loan_type, "Input loan (seeds, agrochemicals)")
		self.assertEqual(float(doc.loan_amount), 90000.00)
		self.assertEqual(doc.purpose_message, "Seeds and fertilizer for next planting season.")
		self.assertEqual(doc.created_by, "Administrator")

		# Verify lead timeline audit events
		events = frappe.get_all(
			"A2C Lead Audit Event", filters={"lead": self.lead_id}, fields=["event_description"]
		)
		self.assertTrue(any("Credit Information added" in e["event_description"] for e in events))

	def test_2_create_credit_info_invalid_amount_throws(self):
		"""Verifies that negative or zero amounts are rejected by the controller validation."""
		# Zero amount
		with self.assertRaises(frappe.ValidationError):
			doc = frappe.new_doc("A2C Credit Information")
			doc.lead = self.lead_id
			doc.loan_type = "Input loan (seeds, agrochemicals)"
			doc.loan_amount = 0
			doc.purpose_message = "Valid message"
			doc.insert()

		# Negative amount
		with self.assertRaises(frappe.ValidationError):
			doc = frappe.new_doc("A2C Credit Information")
			doc.lead = self.lead_id
			doc.loan_type = "Input loan (seeds, agrochemicals)"
			doc.loan_amount = -500.50
			doc.purpose_message = "Valid message"
			doc.insert()

	def test_3_create_credit_info_nonexistent_lead_throws(self):
		"""Verifies controller rejects linking to nonexistent lead."""
		with self.assertRaises(frappe.LinkValidationError):
			doc = frappe.new_doc("A2C Credit Information")
			doc.lead = "LEAD-NONEXISTENT-99999"
			doc.loan_type = "Input loan (seeds, agrochemicals)"
			doc.loan_amount = 1000
			doc.purpose_message = "Valid message"
			doc.insert()

	def test_4_get_lead_credit_infos_filtering(self):
		"""Verifies API correctly filters credit info listings to the target lead."""
		add_lead_credit_info(
			lead_id=self.lead_id,
			loan_type="Agricultural term loan",
			loan_amount=50000,
			purpose_message="Message 1",
		)
		add_lead_credit_info(
			lead_id=self.lead_id, loan_type="Land loan", loan_amount=150000, purpose_message="Message 2"
		)

		res = get_lead_credit_infos(lead_id=self.lead_id)
		self.assertEqual(res["status"], "success")
		self.assertEqual(len(res["data"]), 2)

		first = res["data"][0]
		self.assertEqual(first["loan_type"], "Land loan")
		self.assertEqual(float(first["loan_amount"]), 150000.00)

	def test_5_get_lead_metadata_includes_loan_types(self):
		"""Verifies get_lead_metadata endpoint exposes the options configured for loan_type Select field."""
		res = get_lead_metadata()
		self.assertEqual(res["status"], "success")
		self.assertTrue("loan_types" in res["data"])
		self.assertIn("Input loan (seeds, agrochemicals)", res["data"]["loan_types"])
		self.assertIn("Agricultural term loan", res["data"]["loan_types"])
		self.assertIn("Smallholder farmer direct loan", res["data"]["loan_types"])
