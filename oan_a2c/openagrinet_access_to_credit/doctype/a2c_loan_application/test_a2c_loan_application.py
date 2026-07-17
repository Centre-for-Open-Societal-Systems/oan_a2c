import unittest

import frappe


class TestA2CLoanApplication(unittest.TestCase):
	def test_positive_loan_amount_validation(self):
		doc = frappe.new_doc("A2C Loan Application")
		doc.first_name = "Test"
		doc.last_name = "Farmer"
		doc.phone_number = "1234567890"
		doc.loan_amount = -500
		doc.loan_type = "Input Loan"

		with self.assertRaises(frappe.ValidationError):
			doc.insert()

	def test_phone_number_validation(self):
		doc = frappe.new_doc("A2C Loan Application")
		doc.first_name = "Test"
		doc.last_name = "Farmer"
		doc.phone_number = "invalid_phone"
		doc.loan_amount = 500
		doc.loan_type = "Input Loan"

		with self.assertRaises(frappe.ValidationError):
			doc.insert()

	def test_valid_submission(self):
		doc = frappe.new_doc("A2C Loan Application")
		doc.first_name = "Test"
		doc.last_name = "Farmer"
		doc.phone_number = "0912345678"
		doc.loan_amount = 5000
		doc.loan_type = "Input Loan"

		doc.insert()
		self.assertTrue(doc.name)
