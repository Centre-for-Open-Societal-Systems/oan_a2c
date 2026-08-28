import json
import unittest
from unittest.mock import MagicMock, patch

import frappe

from oan_a2c.api.v1.consent.consent import request_otp, submit_consent, verify_otp


class TestConsentAPI(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		frappe.set_user("Administrator")
		frappe.db.sql(
			"DELETE FROM `tabA2C Consent Request` "
			"WHERE reference_doctype='A2C Lead' AND reference_name='TEST-LEAD-CONSENT'"
		)
		frappe.db.sql("DELETE FROM `tabA2C Lead` WHERE name='TEST-LEAD-CONSENT'")
		frappe.db.commit()

	def setUp(self):
		# The OpenG2P client is mocked in every test, and `fayda_id` flows through as a
		# plain string, so the consent code never reads a Farmer / Consent Partner Config
		# record. We deliberately do NOT create those DocTypes here: inserting a DocType
		# issues a CREATE TABLE (DDL), which Frappe forbids inside the per-test
		# transaction (ImplicitCommitError) — it only "worked" on databases where a
		# previous run had already left the tables behind.

		# Create Lead for testing consent
		if not frappe.db.exists("A2C Lead", "TEST-LEAD-CONSENT"):
			lead = frappe.get_doc(
				{"doctype": "A2C Lead", "phone_number": "+251911123456", "status": "Active"}
			)
			lead.insert(ignore_permissions=True)
			frappe.db.sql("UPDATE `tabA2C Lead` SET name='TEST-LEAD-CONSENT' WHERE name=%s", lead.name)
			frappe.db.commit()

		frappe.conf.secret_key = "test_secret_key"

	def tearDown(self):
		frappe.db.sql(
			"DELETE FROM `tabA2C Consent Request` "
			"WHERE reference_doctype='A2C Lead' AND reference_name='TEST-LEAD-CONSENT'"
		)
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
			"masked_mobile": "091****1111",
		}

		response = request_otp(lead_id="TEST-LEAD-CONSENT", fayda_id="FAYDA-123")
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
		vals = self._get_consent_values(
			consent_name,
			"farmer_fayda_id",
			"status",
			"otp_transaction_id",
			"reference_doctype",
			"reference_name",
		)
		self.assertEqual(vals.get("farmer_fayda_id"), "FAYDA-123")
		self.assertEqual(vals.get("status"), "Pending OTP")
		self.assertEqual(vals.get("otp_transaction_id"), "MOCK-TXN-999")
		self.assertEqual(vals.get("reference_doctype"), "A2C Lead")
		self.assertEqual(vals.get("reference_name"), "TEST-LEAD-CONSENT")

		# request_otp also refreshes the lead's "latest attempt" cache.
		self.assertEqual(frappe.db.get_value("A2C Lead", "TEST-LEAD-CONSENT", "consent_id"), consent_name)

		return consent_name

	@patch("oan_a2c.api.v1.consent.consent.frappe.get_roles")
	def test_request_otp_without_lead_fails_for_dev_agent(self, mock_get_roles):
		mock_get_roles.return_value = ["A2C Development Agent"]
		response = request_otp(fayda_id="FAYDA-123")
		self.assertEqual(response.get("status"), "error")
		self.assertEqual(response.get("code"), "VALIDATION_ERROR")
		self.assertIn("lead_id is required", response.get("message", "").lower())

	@patch("oan_a2c.api.v1.consent.consent._save_direct_consent_response_to_lead")
	@patch("oan_a2c.api.v1.consent.consent.OpenG2PConsentClient")
	def test_verify_and_submit_consent(self, MockClient, MockSaveDirect):
		mock_instance = MockClient.return_value

		# Create doc and send OTP first
		response = self._setup_request_otp(mock_instance)
		consent_name = response.get("data", {}).get("consent_request")

		mock_instance.verify_otp.return_value = {"success": True}

		# 1. Test OTP verification step
		verify_response = verify_otp(
			lead_id="TEST-LEAD-CONSENT", consent_request=consent_name, otp_code="123456"
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
			"mobile": "+251911123456",
		}
		mock_instance.get_consent_allowed_fields.return_value = {
			"success": True,
			"data": [{"id": 1, "name": "First Name"}, {"id": 2, "name": "Last Name"}],
		}
		mock_instance.get_consent_reasons.return_value = {
			"success": True,
			"data": [{"id": 1, "name": "Agri Loan Processing"}],
		}
		mock_instance.submit_consent.return_value = {
			"success": True,
			"data": {"consent_id": "MOCK-G2P-CONS-001", "consent_creation_request_id": "MOCK-G2P-CONS-001"},
		}

		frappe.clear_messages()
		submit_response = submit_consent(
			lead_id="TEST-LEAD-CONSENT",
			consent_request=consent_name,
			consent_type="specific",
			consent_reason_id=1,
			consent_form_filename="signed_consent.pdf",
			consent_form_base64="JVBERi0xLjQKJcOkw7zDtsOfCjIgMCBvYmoKPDwvTGVuZ3RoIDMgMCBSL0ZpbHRlci9GbGF0ZURlY29kZT4+CnN0cmVhbQp4nDPQM1Qo5ypUMFAwALJMLU31jBQsTAz1LBSKk_NTizjzS_NKYtPzKgDs8Qh4CmVuZHN0cmVhbQplbmRvYmoKCjMgMCBvYmoKNTEKZW5kb2JqCgo0IDAgb2JqCjw8L1R5cGUvUGFnZS9NZWRpYUJveFswIDAgNTk1LjI4IDg0MS44OV0vUGFyZW50IDUgMCBSL1Jlc291cmNlczw8L1Byb2NTZXRbL1BERiAvVGV4dCAvSW1hZ2VCIC9JbWFnZUMgL0ltYWdlSV0+Pi9Db250ZW50cyAyIDAgUj4+CmVuZG9iagoKNSAwIG9iago8PC9UeXBlL1BhZ2VzL0NvdW50IDEvS2lkc1s0IDAgUl0+PgplbmRvYmoKCjYgMCBvYmoKPDwvVHlwZS9DYXRhbG9nL1BhZ2VzIDUgMCBSPj4KZW5kb2JqCgoxIDAgb2JqCjw8L1Byb2R1Y2VyKGNhaXJvIDEuMTYuMCAoaHR0cHM6Ly9jYWlyb2dyYXBoaWNzLm9yZykpL0NyZWF0b3IoWEV2aW5jZSBEb2N1bWVudCBWaWV3ZXIgMy4zNi4wKS9DcmVhdGlvbkRhdGUoRDoyMDIwMTExNzExMTIwM1opPj4KZW5kb2JqCgp4cmVmCjAgNwowMDAwMDAwMDAwIDY1NTM1IGYgCjAwMDAwMDA0MDYgMDAwMDAgbiAKMDAwMDAwMDAxNSAwMDAwMCBuIAowMDAwMDAwMTM4IDAwMDAwIG4gCjAwMDAwMDAxNTkgMDAwMDAgbiAKMDAwMDAwMDI5NCAwMDAwMCBuIAowMDAwMDAwMzUyIDAwMDAwIG4gCnRyYWlsZXIKPDwvU2l6ZSA3L1Jvb3QgNiAwIFIvSW5mbyAxIDAgUj4+CnN0YXJ0eHJlZgo1NDIKJSVFT0YK",
			allowed_data_field_ids=[1, 2],
			validity_months=12,
		)

		try:
			self.assertEqual(submit_response.get("status"), "success")
		except AssertionError:
			error_logs = frappe.get_all(
				"Error Log", fields=["method", "error"], order_by="creation desc", limit=1
			)
			if error_logs:
				print("\n--- LATEST FRAPPE ERROR LOG ---")
				print(error_logs[0].error)
				print("-------------------------------\n")
			raise
		self.assertEqual(submit_response.get("data", {}).get("status"), "Approved")
		self.assertEqual(submit_response.get("data", {}).get("openg2p_consent_id"), "MOCK-G2P-CONS-001")
		self.assertIsNotNone(submit_response.get("data", {}).get("consent_receipt"))

		# Verify status, purpose and validity updated in DB
		vals_after_submit = self._get_consent_values(
			consent_name, "status", "purpose", "validity_from", "validity_to"
		)
		self.assertEqual(vals_after_submit.get("status"), "Approved")
		self.assertEqual(vals_after_submit.get("purpose"), "Agri Loan Processing")
		self.assertIsNotNone(vals_after_submit.get("validity_from"))
		self.assertIsNotNone(vals_after_submit.get("validity_to"))

		from frappe.utils import date_diff, getdate

		self.assertEqual(
			date_diff(
				getdate(vals_after_submit.get("validity_to")), getdate(vals_after_submit.get("validity_from"))
			),
			12 * 30,
		)

		# Verify the direct consent-response save (internal queued path) was triggered
		MockSaveDirect.assert_called_once()
