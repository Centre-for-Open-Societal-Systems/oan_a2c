import unittest

import frappe


class TestA2CFarmerProfile(unittest.TestCase):
	def test_phone_number_validation(self):
		doc = frappe.new_doc("A2C Farmer Profile")
		doc.first_name = "Test"
		doc.last_name = "Farmer"
		doc.phone_number = "invalid_phone"

		with self.assertRaises(frappe.ValidationError):
			doc.insert()

	def test_valid_submission(self):
		# Clean up any existing test profile with this phone
		frappe.db.delete("A2C Farmer Profile", {"phone_number": "+251912345678"})

		doc = frappe.new_doc("A2C Farmer Profile")
		doc.first_name = "Test"
		doc.last_name = "Farmer"
		doc.phone_number = "+251912345678"

		doc.insert()
		self.assertTrue(doc.name)
