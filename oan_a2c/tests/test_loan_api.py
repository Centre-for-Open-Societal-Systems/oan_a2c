import frappe
import unittest
from oan_a2c.api.v1.loan_applications import (
    get_loan_summary,
    get_all_loans,
    get_basic_profile,
    get_full_profile
)

class TestLoansV1API(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        frappe.set_user("Administrator")
        frappe.db.sql("DELETE FROM `tabA2C Loan Application` WHERE lead_id='TEST_LEAD_999' OR first_name='API_TEST_FARMER'")
        frappe.db.sql("DELETE FROM `tabA2C Farmer Profile` WHERE lead_id='TEST_LEAD_999' OR phone_number='+251999888777'")
        frappe.db.sql("DELETE FROM `tabA2C Lead` WHERE name='TEST_LEAD_999'")
        frappe.db.commit()

    def setUp(self):
        frappe.set_user("Administrator")
        
        # Create Lead
        if not frappe.db.exists("A2C Lead", "TEST_LEAD_999"):
            lead = frappe.get_doc({
                "doctype": "A2C Lead",
                "phone_number": "+251999888777",
                "lead_source": "Agent Entry",
                "status": "Active"
            })
            lead.insert(ignore_permissions=True)
            frappe.db.sql("UPDATE `tabA2C Lead` SET name='TEST_LEAD_999' WHERE name=%s", lead.name)
            frappe.db.commit()

        # Create Farmer Profile and link to Lead
        if not frappe.db.get_value("A2C Lead", "TEST_LEAD_999", "farmer_profile"):
            farmer = frappe.get_doc({
                "doctype": "A2C Farmer Profile",
                "first_name": "API_TEST_FARMER",
                "last_name": "Test",
                "phone_number": "+251999888777",
                "location": "Addis Ababa",
                "lead_id": "TEST_LEAD_999"
            })
            farmer.insert(ignore_permissions=True)
            frappe.db.set_value("A2C Lead", "TEST_LEAD_999", "farmer_profile", farmer.name)
            frappe.db.commit()

        farmer_profile_name = frappe.db.get_value("A2C Lead", "TEST_LEAD_999", "farmer_profile")
        
        doc = frappe.get_doc({
            "doctype": "A2C Loan Application",
            "first_name": "API_TEST_FARMER",
            "last_name": "Test",
            "phone_number": "+251999888777",
            "loan_amount": 5000,
            "loan_type": "Input Loan",
            "status": "Draft",
            "location": "Addis Ababa",
            "lead_id": "TEST_LEAD_999",
            "farmer_profile": farmer_profile_name
        })
        doc.insert(ignore_permissions=True)
        self.app_id = doc.name
        frappe.db.commit()

    def tearDown(self):
        if hasattr(self, "app_id") and frappe.db.exists("A2C Loan Application", self.app_id):
            frappe.delete_doc("A2C Loan Application", self.app_id, ignore_permissions=True, force=True)
        frappe.db.commit()

    def test_1_get_loan_summary(self):
        res = get_loan_summary()
        self.assertEqual(res["status"], "success")
        self.assertIn("summary", res)
        self.assertIn("total", res["summary"])

    def test_2_get_all_loans(self):
        res = get_all_loans(status="Draft", page_size=10)
        self.assertEqual(res["status"], "success")
        self.assertTrue(len(res["results"]) > 0)
        found = False
        for r in res["results"]:
            if r["application_id"] == self.app_id:
                found = True
        self.assertTrue(found)

    def test_3_get_basic_profile(self):
        res = get_basic_profile(lead_id="TEST_LEAD_999")
        self.assertEqual(res["status"], "success")
        self.assertEqual(res["data"]["first_name"], "API_TEST_FARMER")
        self.assertEqual(res["data"]["phone_number"], "+251999888777")
        self.assertNotIn("loan_amount", res["data"])

    def test_4_get_full_profile(self):
        res = get_full_profile(application_id=self.app_id)
        self.assertEqual(res["status"], "success")
        self.assertEqual(res["data"]["first_name"], "API_TEST_FARMER")
        self.assertEqual(res["data"]["loan_amount"], 5000)
        self.assertEqual(res["data"]["status"], "Draft")




