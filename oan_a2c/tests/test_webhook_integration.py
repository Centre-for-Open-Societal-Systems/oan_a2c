import copy

import frappe
import requests

WEBHOOK_PAYLOAD = {
	"source": "g2p_ati_consent_mgt",
	"event_type": "WEBSUB_INDIVIDUAL_UPDATED",
	"published_at": "2026-06-06 10:42:03",
	"consent": {
		"id": 96,
		"consent_creation_request_id": "8e3ce997-dce5-42cc-a6a4-ae3db05b6542",
		"consent_type": "specific",
		"status": "approved",
		"approved_at": "2026-06-06 10:42:03",
		"validity_from": "2026-05-06 00:00:00",
		"validity_to": "2027-05-06 00:00:00",
		"requested_field_codes": ["farmer_basic"],
		"published_field_codes": ["farmer_basic"],
		"data_field_mode": "dynamic",
	},
	"consent_partner": {
		"id": 16,
		"name": "a2capp@test.com",
		"ref": False,
		"websub_config_id": 2,
		"websub_config_name": "Local Test WebSub (Mock)1",
	},
	"farmer": {"id": 30, "farmer_id": False, "name": "ABEBE BEKELE TESFAYE BEKELE TEFAYA"},
	"selected_data": {
		"farmer": {
			"First Name(English)": False,
			"Father Name": False,
			"Email": False,
			"Region": {"id": 1, "name": "Addis Ababa", "code": "ET14"},
			"Zone": {"id": 1, "name": "Gulele Subcity", "code": "ET1401"},
			"Woreda": {"id": 1, "name": "Wereda 01", "code": "140101"},
		}
	},
}


def run():
	FRAPPE_URL = "http://127.0.0.1:8000"

	payload = copy.deepcopy(WEBHOOK_PAYLOAD)
	consent_request_id = payload["consent"]["consent_creation_request_id"]

	print(f"Cleaning up existing records for UUID: {consent_request_id}...")
	# Cleanup previous runs
	existing_consents = frappe.get_all(
		"A2C Consent Request", filters={"openg2p_consent_id": consent_request_id}, pluck="name"
	)
	for name in existing_consents:
		doc = frappe.get_doc("A2C Consent Request", name)
		if doc.loan_application:
			frappe.delete_doc(
				"A2C Loan Application", doc.loan_application, ignore_permissions=True, force=True
			)
		frappe.delete_doc("A2C Consent Request", name, ignore_permissions=True, force=True)
	frappe.db.commit()

	print("Creating fake Loan Application and Consent Request...")
	# Create fake Loan Application first
	loan_app = frappe.get_doc(
		{
			"doctype": "A2C Loan Application",
			"first_name": "Pending",
			"last_name": "Pending",
			"phone_number": "+251999999999",
			"loan_amount": 1000,
			"loan_type": "Input Loan",
			"status": "Draft",
		}
	)
	loan_app.insert(ignore_permissions=True)
	frappe.db.commit()

	# Create fake Consent Request linked to Loan Application
	consent_req = frappe.get_doc(
		{
			"doctype": "A2C Consent Request",
			"openg2p_consent_id": consent_request_id,
			"loan_application": loan_app.name,
			"farmer_fayda_id": "1234567890",
			"partner": "Test Partner",
			"status": "Draft",
		}
	)
	consent_req.insert(ignore_permissions=True)
	frappe.db.commit()

	print("\nSending Webhook Payload to API...")
	# Call Webhook API via HTTP POST
	response = requests.post(
		f"{FRAPPE_URL}/api/method/oan_a2c.api.v1.webhook_consent_data.receive_consent_data",
		json=payload,
		headers={"Content-Type": "application/json"},
		timeout=10,
	)

	print(f"Status Code: {response.status_code}")
	print(f"Response: {response.text}")

	if response.status_code not in (200, 202):
		raise Exception(f"Webhook failed: {response.text}")

	print("Waiting 3 seconds for background worker to process the payload...")
	import time

	time.sleep(3)

	# Verify Database Changes
	frappe.db.commit()  # Ensure we read fresh data
	updated_loan = frappe.get_doc("A2C Loan Application", loan_app.name)

	print("\n--- Verification ---")
	print(f"First Name: {updated_loan.first_name} (Expected: ABEBE)")
	print(f"Last Name: {updated_loan.last_name} (Expected: BEKELE TESFAYE BEKELE TEFAYA)")
	print(f"Location: {updated_loan.location} (Expected: Addis Ababa, Gulele Subcity, Wereda 01)")
	print(f"Consent ID: {updated_loan.consent_id} (Expected: {consent_req.name})")
	print(f"Farmer ID: {updated_loan.farmer_id} (Expected: 30)")

	# Verify Consent Request
	updated_consent = frappe.get_doc("A2C Consent Request", consent_req.name)
	print(f"Consent Status: {updated_consent.status} (Expected: Approved)")

	print("\nDone! To run this test, execute: bench execute oan_a2c.tests.test_webhook_integration.run")


if __name__ == "__main__":
	print("Please run via: bench execute oan_a2c.tests.test_webhook_integration.run")
