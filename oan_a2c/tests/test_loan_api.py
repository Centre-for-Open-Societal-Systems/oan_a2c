import frappe
import unittest
from oan_a2c.api.loan_app_api import (
    loan_details,
    bank_details,
    farmer_details,
    application_manager
)

class TestLoanAPI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        frappe.set_user("Administrator")

    @classmethod
    def tearDownClass(cls):
        frappe.set_user("Administrator")
        # Clean up created loan applications
        apps = frappe.get_all("Loan Application", filters={"purpose_of_loan": "API_TEST"}, pluck="name")
        for name in apps:
            frappe.delete_doc("Loan Application", name, ignore_permissions=True, force=True)
        frappe.db.commit()

    def setUp(self):
        frappe.set_user("Administrator")

    def test_1_create_loan_details(self):
        """Test creating a new application via loan_details API"""
        response = loan_details(
            action="save",
            loan_type="Agricultural Loan",
            purpose_of_loan="API_TEST",
            requested_loan_amount=50000,
            primary_crop="Wheat"
        )
        self.assertEqual(response["status"], "success")
        self.assertIn("application_id", response["data"])
        self.assertEqual(response["data"]["current_step"], 1)

        # Verify in DB
        app_id = response["data"]["application_id"]
        doc = frappe.get_doc("Loan Application", app_id)
        self.assertEqual(doc.requested_amount, 50000)
        self.assertEqual(doc.primary_crop, "Wheat")
        self.assertEqual(doc.current_step, 1)

        # Store app_id for next tests
        TestLoanAPI.app_id = app_id

    def test_2_update_bank_details(self):
        """Test saving bank details for the created application"""
        # Ensure we have an app_id from previous test or create one
        app_id = getattr(self, "app_id", None)
        if not app_id:
            res = loan_details(action="save", purpose_of_loan="API_TEST")
            app_id = res["data"]["application_id"]

        response = bank_details(
            action="save",
            application_id=app_id,
            bank_account_name="John Doe",
            bank_account_number="1234567890",
            total_amount_borrowing=50000
        )
        self.assertEqual(response["status"], "success")
        self.assertEqual(response["data"]["current_step"], 2)

        doc = frappe.get_doc("Loan Application", app_id)
        self.assertEqual(doc.bank_account_no, "1234567890")

    def test_3_save_farmer_details(self):
        """Test saving farmer details"""
        app_id = getattr(self, "app_id", None)
        if not app_id:
            res = loan_details(action="save", purpose_of_loan="API_TEST")
            app_id = res["data"]["application_id"]

        response = farmer_details(
            action="save",
            application_id=app_id,
            full_name="John",
            last_name="Doe",
            mobile_phone="0911223344"
        )
        self.assertEqual(response["status"], "success")
        self.assertEqual(response["data"]["current_step"], 5)

        doc = frappe.get_doc("Loan Application", app_id)
        self.assertEqual(doc.full_name, "John")

    def test_4_application_manager_review(self):
        """Test reviewing the application status"""
        app_id = getattr(self, "app_id", None)
        if not app_id:
            res = loan_details(action="save", purpose_of_loan="API_TEST")
            app_id = res["data"]["application_id"]

        response = application_manager(
            action="review",
            application_id=app_id
        )
        self.assertEqual(response["status"], "success")
        sections = response["data"]["sections"]
        self.assertIn("Loan Requirements", sections)
        self.assertIn("Bank Details", sections)

    def test_5_draft_save(self):
        """Test draft saving functionality with protected fields"""
        app_id = getattr(self, "app_id", None)
        if not app_id:
            res = loan_details(action="save", purpose_of_loan="API_TEST")
            app_id = res["data"]["application_id"]

        response = application_manager(
            action="draft",
            application_id=app_id,
            step=1,
            data={"address": "Test Address", "status": "Approved"} # status should be protected
        )
        self.assertEqual(response["status"], "success")

        doc = frappe.get_doc("Loan Application", app_id)
        self.assertEqual(doc.address, "Test Address")
        # status should not be changed by generic draft endpoint
        self.assertEqual(doc.status, "Draft")

    def test_6_submit_missing_consent(self):
        """Test that submitting an application without consent fails"""
        app_id = getattr(self, "app_id", None)
        if not app_id:
            res = loan_details(action="save", purpose_of_loan="API_TEST")
            app_id = res["data"]["application_id"]

        response = application_manager(
            action="submit",
            application_id=app_id
        )
        # It should fail because consent_status != "Approved" and no documents
        self.assertEqual(response["status"], "error")
        self.assertEqual(response["error_code"], "CONSENT_NOT_APPROVED")

    def test_7_cancel_application(self):
        """Test cancelling the application"""
        res = loan_details(action="save", purpose_of_loan="API_TEST")
        app_id = res["data"]["application_id"]

        response = application_manager(
            action="cancel",
            application_id=app_id,
            reason="User requested cancellation"
        )
        self.assertEqual(response["status"], "success")

        doc = frappe.get_doc("Loan Application", app_id)
        self.assertEqual(doc.status, "Cancelled")
        self.assertEqual(doc.cancellation_reason, "User requested cancellation")

    def test_8_submit_success(self):
        """Test submitting an application when consent is Approved and documents are uploaded"""
        res = loan_details(action="save", purpose_of_loan="API_TEST")
        app_id = res["data"]["application_id"]

        # 1. Update consent status to Approved
        frappe.db.set_value("Loan Application", app_id, "consent_status", "Approved")
        frappe.db.commit()

        # 2. Attach a dummy document to satisfy doc count check
        frappe.db.sql("""
            INSERT INTO `tabFile` (name, file_name, attached_to_doctype, attached_to_name, file_url, is_private)
            VALUES (%s, %s, %s, %s, %s, %s)
        """, (frappe.generate_hash(), "test.pdf", "Loan Application", app_id, "/private/files/test.pdf", 1))
        frappe.db.commit()

        # 3. Call submit
        response = application_manager(
            action="submit",
            application_id=app_id
        )
        
        self.assertEqual(response["status"], "success")
        self.assertEqual(response["data"]["status"], "Submitted")
        
        # Verify in DB
        doc = frappe.get_doc("Loan Application", app_id)
        self.assertEqual(doc.status, "Submitted")
        self.assertEqual(doc.current_step, 6)
