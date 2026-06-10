import frappe
import unittest
from unittest.mock import patch, MagicMock
from oan_a2c.api.v1.consent.consent import request_otp, verify_otp_for_lead
import json

class TestConsentAPI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        frappe.set_user("Administrator")
        frappe.db.sql("DELETE FROM `tabA2C Consent Request` WHERE lead='TEST-LEAD-CONSENT'")
        frappe.db.sql("DELETE FROM `tabA2C Lead` WHERE name='TEST-LEAD-CONSENT'")
        frappe.db.commit()

    def setUp(self):
        # Create missing Custom DocTypes if they don't exist in the database
        if not frappe.db.exists("DocType", "Farmer"):
            frappe.get_doc({
                "doctype": "DocType",
                "name": "Farmer",
                "module": "OpenAgriNet Access to Credit",
                "custom": 1,
                "fields": [
                    {"fieldname": "farmer_name", "fieldtype": "Data", "label": "Farmer Name"},
                    {"fieldname": "full_name", "fieldtype": "Data", "label": "Full Name"},
                    {"fieldname": "mobile_no", "fieldtype": "Data", "label": "Mobile No"},
                    {"fieldname": "fayda_id", "fieldtype": "Data", "label": "Fayda ID"}
                ],
                "permissions": [{"role": "System Manager", "read": 1, "write": 1, "create": 1}]
            }).insert(ignore_permissions=True)

        if not frappe.db.exists("DocType", "Consent Partner Config"):
            frappe.get_doc({
                "doctype": "DocType",
                "name": "Consent Partner Config",
                "module": "OpenAgriNet Access to Credit",
                "custom": 1,
                "fields": [
                    {"fieldname": "partner_name", "fieldtype": "Data", "label": "Partner Name"}
                ],
                "permissions": [{"role": "System Manager", "read": 1, "write": 1, "create": 1}]
            }).insert(ignore_permissions=True)

        # Create necessary placeholder records
        if not frappe.db.exists("Farmer", "FAYDA-123"):
            frappe.get_doc({
                "doctype": "Farmer",
                "farmer_name": "Test Farmer",
                "full_name": "Test Farmer",
                "mobile_no": "+251911123456",
                "fayda_id": "FAYDA-123"
            }).insert(ignore_permissions=True)
            
        if not frappe.db.exists("Consent Partner Config", "Test Partner"):
            frappe.get_doc({
                "doctype": "Consent Partner Config",
                "partner_name": "Test Partner"
            }).insert(ignore_permissions=True)

        # Create Lead for testing consent
        if not frappe.db.exists("A2C Lead", "TEST-LEAD-CONSENT"):
            lead = frappe.get_doc({
                "doctype": "A2C Lead",
                "phone_number": "+251911123456",
                "status": "Active"
            })
            lead.insert(ignore_permissions=True)
            frappe.db.sql("UPDATE `tabA2C Lead` SET name='TEST-LEAD-CONSENT' WHERE name=%s", lead.name)
            frappe.db.commit()

        frappe.conf.secret_key = "test_secret_key"

    def tearDown(self):
        frappe.db.sql("DELETE FROM `tabA2C Consent Request` WHERE lead='TEST-LEAD-CONSENT'")
        frappe.db.commit()

    def _get_consent_values(self, name, *fields):
        """Helper: fetch consent request fields directly from DB to avoid child-table load."""
        result = frappe.db.get_value("A2C Consent Request", name, list(fields), as_dict=True)
        return result or {}

    @patch("oan_a2c.api.v1.consent.consent.OpenG2PConsentClient")
    def test_request_otp(self, MockClient):
        # Mock the OpenG2P responses
        mock_instance = MockClient.return_value
        
        mock_instance.get_farmer_by_fayda_id.return_value = "DB-FARMER-001"
        mock_instance.get_partner_id.return_value = "DB-PARTNER-001"
        mock_instance.get_partner_allowed_data_field_ids.return_value = [1, 2]
        
        mock_instance.session = MagicMock()
        mock_instance.session.cookies = MagicMock()
        mock_instance.session.cookies.get.return_value = "MOCK-SESSION-COOKIE"
        
        mock_instance.request_otp.return_value = {
            "data": {
                "transaction_id": "MOCK-TXN-999",
                "masked_mobile": "091****1111"
            },
            "success": True
        }

        response = request_otp(
            lead_id="TEST-LEAD-CONSENT",
            fayda_id="FAYDA-123",
            partner="Test Partner",
            purpose="Testing Consent API",
            consent_form_base64="dGVzdCBjb250ZW50",
            consent_form_filename="signed_consent.png"
        )
        
        self.assertEqual(response.get("status"), "success")
        self.assertEqual(response.get("transaction_id"), "MOCK-TXN-999")
        
        # Verify document was created using direct DB query
        consent_name = response.get("consent_request")
        vals = self._get_consent_values(consent_name, "farmer_fayda_id", "status", "otp_transaction_id", "lead")
        self.assertEqual(vals.get("farmer_fayda_id"), "FAYDA-123")
        self.assertEqual(vals.get("status"), "Pending OTP")
        self.assertEqual(vals.get("otp_transaction_id"), "MOCK-TXN-999")
        self.assertEqual(vals.get("lead"), "TEST-LEAD-CONSENT")
        
        return consent_name

    @patch("oan_a2c.api.v1.consent.consent._fetch_and_save_farmer_data")
    @patch("oan_a2c.api.v1.consent.consent.enqueue_websub_delivery")
    @patch("oan_a2c.api.v1.consent.consent.OpenG2PConsentClient")
    def test_verify_otp_for_lead(self, MockClient, MockEnqueue, MockFetchFarmer):
        # Create doc and send OTP first
        consent_name = self.test_request_otp()
        
        # Mock fetch and save farmer data
        MockFetchFarmer.return_value = ({
            "given_name": "Test",
            "family_name": "Farmer",
            "phone_no": "+251911123456"
        }, None)
        
        mock_instance = MockClient.return_value
        mock_instance.get_farmer_by_fayda_id.return_value = "DB-FARMER-001"
        mock_instance.get_partner_id.return_value = "DB-PARTNER-001"
        mock_instance.get_partner_allowed_data_field_ids.return_value = [1, 2]
        mock_instance.verify_otp.return_value = {
            "status": "success"
        }
        mock_instance.upload_consent_attachment.return_value = ["MOCK-ATT-001"]
        mock_instance.create_consent_request.return_value = {
            "id": "MOCK-G2P-CONS-001",
            "data": {
                "consent_creation_request_id": "MOCK-G2P-CONS-001"
            }
        }
        mock_instance.approve_consent_request.return_value = {
            "status": "success"
        }
        
        response = verify_otp_for_lead(lead_id="TEST-LEAD-CONSENT", otp_code="123456")
        
        self.assertEqual(response.get("status"), "success")
        self.assertIn("consent_receipt", response)
        
        # Verify via direct DB query to avoid child-table loading
        vals = self._get_consent_values(consent_name, "status", "otp_verified_at")
        self.assertEqual(vals.get("status"), "Approved")
        self.assertIsNotNone(vals.get("otp_verified_at"))
        
        # Verify WebSub was queued
        MockEnqueue.assert_called_once()
