import frappe
import unittest
from oan_a2c.api.v1.loan_applications import (
    get_loan_summary,
    get_all_loans,
    get_basic_profile,
    update_basic_profile,
    get_full_profile,
    get_supporting_documents,
    delete_supporting_document,
    update_loan_step,
    update_loan_status
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
        self.assertIn("tab_counts", res["summary"])
        self.assertEqual(res["summary"]["tab_counts"]["all"], res["summary"]["total"])
        self.assertIn("my", res["summary"]["tab_counts"])
        self.assertIn("unassigned", res["summary"]["tab_counts"])

    def test_2_get_all_loans(self):
        res = get_all_loans(status="Draft", page_size=10)
        self.assertEqual(res["status"], "success")
        self.assertTrue(len(res["results"]) > 0)
        found = False
        for r in res["results"]:
            if r["application_id"] == self.app_id:
                found = True
                self.assertEqual(r["lead_id"], "TEST_LEAD_999")
                self.assertEqual(r["step"], 1)
        self.assertTrue(found)

        # Test filtering by lead_id
        res_lead = get_all_loans(lead_id="TEST_LEAD_999")
        self.assertEqual(res_lead["status"], "success")
        self.assertTrue(len(res_lead["results"]) > 0)
        for r in res_lead["results"]:
            self.assertEqual(r["lead_id"], "TEST_LEAD_999")

    def test_3_get_basic_profile(self):
        res = get_basic_profile(lead_id="TEST_LEAD_999")
        self.assertEqual(res["status"], "success")
        self.assertEqual(res["data"]["first_name"], "API_TEST_FARMER")
        self.assertEqual(res["data"]["phone_number"], "+251999888777")
        self.assertNotIn("loan_amount", res["data"])

    def test_3b_update_basic_profile(self):
        res = update_basic_profile(
            lead_id="TEST_LEAD_999",
            email="updated_farmer@example.com",
            location="Hawassa"
        )
        self.assertEqual(res["status"], "success")
        self.assertEqual(res["data"]["email"], "updated_farmer@example.com")
        self.assertEqual(res["data"]["location"], "Hawassa")

        # Verify database documents got updated
        lead_doc = frappe.get_doc("A2C Lead", "TEST_LEAD_999")
        farmer_doc = frappe.get_doc("A2C Farmer Profile", lead_doc.farmer_profile)
        self.assertEqual(farmer_doc.email, "updated_farmer@example.com")
        self.assertEqual(farmer_doc.location, "Hawassa")
        self.assertEqual(lead_doc.email, "updated_farmer@example.com")

    def test_4_get_full_profile(self):
        res = get_full_profile(application_id=self.app_id)
        self.assertEqual(res["status"], "success")
        self.assertEqual(res["data"]["first_name"], "API_TEST_FARMER")
        self.assertEqual(res["data"]["loan_amount"], 5000)
        self.assertEqual(res["data"]["status"], "Draft")

    def test_5_supporting_documents(self):
        # Create a File document programmatically
        file_doc = frappe.get_doc({
            "doctype": "File",
            "file_name": "test_doc.png",
            "content": b"dummy content",
            "attached_to_doctype": "A2C Loan Application",
            "attached_to_name": self.app_id,
            "is_private": 1
        })
        file_doc.insert(ignore_permissions=True)
        frappe.db.commit()
        file_id = file_doc.name

        # 1. Get supporting documents
        res = get_supporting_documents(self.app_id)
        self.assertEqual(res["status"], "success")
        self.assertEqual(len(res["files"]), 1)
        self.assertEqual(res["files"][0]["name"], file_id)

        # 2. Delete supporting document
        res_del = delete_supporting_document(self.app_id, file_id)
        self.assertEqual(res_del["status"], "success")
        self.assertEqual(res_del["message"], "File deleted successfully")

        # 3. Check if file is actually deleted
        self.assertFalse(frappe.db.exists("File", file_id))

        # 4. Get again, should be empty
        res_after = get_supporting_documents(self.app_id)
        self.assertEqual(res_after["status"], "success")
        self.assertEqual(len(res_after["files"]), 0)

    def test_6_update_loan_step(self):
        # 1. Update step
        res = update_loan_step(self.app_id, 3)
        self.assertEqual(res["status"], "success")
        self.assertEqual(res["message"], "Loan application step updated to 3")

        # 2. Check if updated
        doc = frappe.get_doc("A2C Loan Application", self.app_id)
        self.assertEqual(doc.current_step, 3)

    def test_7_rejected_loan_status_locked(self):
        # 1. Reject the loan application
        res = update_loan_status(self.app_id, "Rejected")
        self.assertEqual(res["status"], "success")

        # 2. Try to change it to Approved, should fail or throw ValidationError
        doc = frappe.get_doc("A2C Loan Application", self.app_id)
        doc.status = "Approved"
        self.assertRaises(frappe.ValidationError, doc.save)





