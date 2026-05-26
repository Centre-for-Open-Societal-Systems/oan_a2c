import frappe
import unittest
from unittest.mock import patch, MagicMock
from oan_a2c.consent.consent import create_consent_request, send_otp, verify_otp
import json

class TestConsentAPI(unittest.TestCase):
    def setUp(self):
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

    def _get_consent_values(self, name, *fields):
        """Helper: fetch consent request fields directly from DB to avoid child-table load."""
        result = frappe.db.get_value("Consent Request", name, list(fields), as_dict=True)
        return result or {}

    @patch("oan_a2c.consent.consent.OpenG2PConsentClient")
    def test_create_consent_request(self, MockClient):
        # Mock the OpenG2P response
        mock_instance = MockClient.return_value
        mock_instance.create_consent_request.return_value = {
            "consent_id": "MOCK-G2P-CONS-001"
        }

        response = create_consent_request(
            farmer="FAYDA-123",
            partner="Test Partner",
            consent_type="specific",
            purpose="Testing Consent API",
            validity_from="2026-06-01 00:00:00",
            validity_to="2027-06-01 00:00:00",
            requested_data_fields=json.dumps([])
        )
        
        self.assertEqual(response.get("status"), "success")
        self.assertEqual(response.get("openg2p_consent_id"), "MOCK-G2P-CONS-001")
        
        # Verify document was created using direct DB query (no child-table load)
        consent_name = response.get("consent_request")
        vals = self._get_consent_values(consent_name, "farmer", "status", "openg2p_consent_id")
        self.assertEqual(vals.get("farmer"), "FAYDA-123")
        self.assertEqual(vals.get("status"), "Draft")
        self.assertEqual(vals.get("openg2p_consent_id"), "MOCK-G2P-CONS-001")
        
        return consent_name

    @patch("oan_a2c.consent.consent.OpenG2PConsentClient")
    def test_send_otp(self, MockClient):
        # First create a doc
        consent_name = self.test_create_consent_request()
        
        mock_instance = MockClient.return_value
        mock_instance.send_otp.return_value = {
            "transaction_id": "MOCK-TXN-999",
            "masked_phone": "091****1111"
        }
        
        response = send_otp(consent_request=consent_name)
        
        self.assertEqual(response.get("status"), "success")
        self.assertEqual(response.get("transaction_id"), "MOCK-TXN-999")
        
        # Verify via direct DB query to avoid child-table loading
        vals = self._get_consent_values(consent_name, "status", "otp_transaction_id")
        self.assertEqual(vals.get("status"), "Pending OTP")
        self.assertEqual(vals.get("otp_transaction_id"), "MOCK-TXN-999")
        
        return consent_name

    @patch("oan_a2c.consent.consent.enqueue_websub_delivery")
    @patch("oan_a2c.consent.consent.OpenG2PConsentClient")
    def test_verify_otp(self, MockClient, MockEnqueue):
        # Create doc and send OTP first
        consent_name = self.test_send_otp()
        
        mock_instance = MockClient.return_value
        mock_instance.verify_otp.return_value = {
            "status": "success"
        }
        
        response = verify_otp(consent_request=consent_name, otp_code="123456")
        
        self.assertEqual(response.get("status"), "success")
        self.assertIn("consent_receipt", response)
        
        # Verify via direct DB query to avoid child-table loading
        vals = self._get_consent_values(consent_name, "status", "otp_verified_at")
        self.assertEqual(vals.get("status"), "Approved")
        self.assertIsNotNone(vals.get("otp_verified_at"))
        
        # Verify WebSub was queued
        MockEnqueue.assert_called_once()
