import frappe
import unittest
from unittest.mock import patch, MagicMock
from oan_a2c.api.v1.consent.consent import request_otp, verify_otp, submit_consent
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

    def _setup_request_otp(self, mock_instance):
        mock_instance.get_farmer_by_fayda_id.return_value = {"id": 36, "name": "Test Farmer"}
        mock_instance.get_partner_id.return_value = "DB-PARTNER-001"
        mock_instance.get_partner_allowed_data_field_ids.return_value = [1, 2]
        
        mock_instance.session = MagicMock()
        mock_instance.session.cookies = MagicMock()
        mock_instance.session.cookies.get.return_value = "MOCK-SESSION-COOKIE"
        
        mock_instance.request_otp.return_value = {
            "transaction_id": "MOCK-TXN-999",
            "masked_mobile": "091****1111"
        }

        response = request_otp(
            lead_id="TEST-LEAD-CONSENT",
            fayda_id="FAYDA-123"
        )
        return response

    @patch("oan_a2c.api.v1.consent.consent.OpenG2PConsentClient")
    def test_request_otp(self, MockClient):
        # Mock the OpenG2P responses
        mock_instance = MockClient.return_value
        response = self._setup_request_otp(mock_instance)
        
        self.assertEqual(response.get("status"), "success")
        self.assertEqual(response.get("data", {}).get("transaction_id"), "MOCK-TXN-999")
        
        # Verify document was created using direct DB query
        consent_name = response.get("data", {}).get("consent_request")
        vals = self._get_consent_values(consent_name, "farmer_fayda_id", "status", "otp_transaction_id", "lead")
        self.assertEqual(vals.get("farmer_fayda_id"), "FAYDA-123")
        self.assertEqual(vals.get("status"), "Pending OTP")
        self.assertEqual(vals.get("otp_transaction_id"), "MOCK-TXN-999")
        self.assertEqual(vals.get("lead"), "TEST-LEAD-CONSENT")
        
        return consent_name

    @patch("oan_a2c.api.v1.consent.consent.enqueue_websub_delivery")
    @patch("oan_a2c.api.v1.consent.consent.OpenG2PConsentClient")
    def test_verify_and_submit_consent(self, MockClient, MockEnqueue):
        mock_instance = MockClient.return_value
        
        # Create doc and send OTP first
        response = self._setup_request_otp(mock_instance)
        consent_name = response.get("data", {}).get("consent_request")
        
        mock_instance.verify_otp.return_value = {
            "success": True
        }
        
        # 1. Test OTP verification step
        verify_response = verify_otp(
            lead_id="TEST-LEAD-CONSENT",
            consent_request=consent_name,
            otp_code="123456"
        )
        self.assertEqual(verify_response.get("status"), "success")
        self.assertEqual(verify_response.get("data", {}).get("status"), "OTP Verified")
        
        # Verify status in database
        vals = self._get_consent_values(consent_name, "status", "otp_verified_at")
        self.assertEqual(vals.get("status"), "Pending OTP")
        self.assertIsNotNone(vals.get("otp_verified_at"))
        
        # 2. Test Submit Consent step
        mock_instance.get_farmer_by_fayda_id.return_value = {
            "id": 36,
            "name": "Test Farmer",
            "mobile": "+251911123456"
        }
        mock_instance.get_consent_allowed_fields.return_value = {
            "success": True,
            "data": [
                {"id": 1, "name": "First Name"},
                {"id": 2, "name": "Last Name"}
            ]
        }
        mock_instance.submit_consent.return_value = {
            "success": True,
            "data": {
                "consent_id": "MOCK-G2P-CONS-001",
                "consent_creation_request_id": "MOCK-G2P-CONS-001"
            }
        }
        
        submit_response = submit_consent(
            lead_id="TEST-LEAD-CONSENT",
            consent_request=consent_name,
            consent_type="specific",
            consent_reason_id=1,
            consent_form_filename="signed_consent.txt",
            consent_form_base64="dGVzdCBjb250ZW50",
            allowed_data_field_ids=[1, 2],
            validity_months=12
        )
        
        self.assertEqual(submit_response.get("status"), "success")
        self.assertEqual(submit_response.get("data", {}).get("status"), "Approved")
        self.assertEqual(submit_response.get("data", {}).get("openg2p_consent_id"), "MOCK-G2P-CONS-001")
        self.assertIsNotNone(submit_response.get("data", {}).get("consent_receipt"))
        
        # Verify status and validity updated in DB
        vals_after_submit = self._get_consent_values(consent_name, "status", "validity_from", "validity_to")
        self.assertEqual(vals_after_submit.get("status"), "Approved")
        self.assertIsNotNone(vals_after_submit.get("validity_from"))
        self.assertIsNotNone(vals_after_submit.get("validity_to"))
        
        from frappe.utils import getdate, date_diff
        self.assertEqual(
            date_diff(getdate(vals_after_submit.get("validity_to")), getdate(vals_after_submit.get("validity_from"))),
            12 * 30
        )
        
        # Verify WebSub was queued
        MockEnqueue.assert_called_once()
