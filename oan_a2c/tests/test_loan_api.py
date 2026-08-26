import unittest

import frappe

from oan_a2c.api.utils import get_workflow_initial_state
from oan_a2c.api.v1.loan_applications import (
	create_loan_application,
	delete_supporting_document,
	download_supporting_document,
	get_all_loans,
	get_basic_profile,
	get_full_profile,
	get_loan_summary,
	get_supporting_documents,
	update_basic_profile,
	update_loan_status,
	update_loan_step,
)


class TestLoansV1API(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		frappe.set_user("Administrator")
		frappe.db.sql(
			"DELETE FROM `tabA2C Loan Application` WHERE lead_id='TEST_LEAD_999' OR first_name='API_TEST_FARMER'"
		)
		frappe.db.sql("DELETE FROM `tabA2C Farmer Profile` WHERE phone_number='+251999888777'")
		frappe.db.sql("DELETE FROM `tabA2C Lead` WHERE name='TEST_LEAD_999'")
		frappe.db.sql(
			"DELETE FROM `tabA2C Consent Request` "
			"WHERE reference_doctype='A2C Lead' AND reference_name='TEST_LEAD_999'"
		)
		frappe.db.sql(
			"DELETE FROM `tabA2C Loan Application Audit Event` WHERE loan_application IN "
			"(SELECT name FROM `tabA2C Loan Application` WHERE lead_id='TEST_LEAD_999')"
		)

		if not frappe.db.exists("A2C Participating Bank", "Test Bank"):
			bank_doc = frappe.get_doc(
				{
					"doctype": "A2C Participating Bank",
					"registered_city": "Test City",
					"bank_name": "Test Bank Name",
					"bank_code": "TEST_BANK_999",
					"status": "In Review",
					"entity_type": "Commercial Bank",
					"registered_email": "testbank@test.com",
					"registered_phone": "+251911000000",
					"registered_region": "Addis Ababa",
					"registered_country": "Ethiopia",
					"kyc_document": "/private/files/test_kyc.pdf",
					"gro_name": "Test GRO",
					"ops_name": "Test Ops",
				}
			)
			bank_doc.insert(ignore_permissions=True)
			frappe.db.sql(
				"UPDATE `tabA2C Participating Bank` SET name='Test Bank' WHERE name=%s", bank_doc.name
			)
			frappe.db.sql(
				"UPDATE `tabA2C Loan Status Stage` SET bank='Test Bank' WHERE bank=%s", bank_doc.name
			)

		from oan_a2c.patches.create_lead_loan_workflows import _seed_default_stages

		_seed_default_stages()
		frappe.db.commit()

	def setUp(self):
		frappe.set_user("Administrator")

		# Create Lead
		if not frappe.db.exists("A2C Lead", "TEST_LEAD_999"):
			lead = frappe.get_doc(
				{
					"doctype": "A2C Lead",
					"phone_number": "+251999888777",
					"lead_source": "Agent Entry",
					"status": "Verified",
				}
			)
			lead.insert(ignore_permissions=True)
			frappe.db.sql("UPDATE `tabA2C Lead` SET name='TEST_LEAD_999' WHERE name=%s", lead.name)
			frappe.db.commit()
		else:
			frappe.db.set_value("A2C Lead", "TEST_LEAD_999", "status", "Verified")
			frappe.db.commit()

		# Create Farmer Profile and link to Lead
		if not frappe.db.get_value("A2C Lead", "TEST_LEAD_999", "farmer_profile"):
			farmer = frappe.get_doc(
				{
					"doctype": "A2C Farmer Profile",
					"first_name": "API_TEST_FARMER",
					"last_name": "Test",
					"phone_number": "+251999888777",
					"region": "Addis Ababa",
					"woreda": "Gulele",
					"lead_id": "TEST_LEAD_999",
				}
			)
			farmer.insert(ignore_permissions=True)
			frappe.db.set_value("A2C Lead", "TEST_LEAD_999", "farmer_profile", farmer.name)
			frappe.db.commit()

		farmer_profile_name = frappe.db.get_value("A2C Lead", "TEST_LEAD_999", "farmer_profile")

		if not frappe.db.exists(
			"A2C Consent Request",
			{"reference_doctype": "A2C Lead", "reference_name": "TEST_LEAD_999"},
		):
			consent = frappe.get_doc(
				{
					"doctype": "A2C Consent Request",
					"farmer": "API_TEST_FARMER Test",
					"farmer_fayda_id": "123456789",
					"partner": "Test Partner",
					"reference_doctype": "A2C Lead",
					"reference_name": "TEST_LEAD_999",
					"status": "Approved",
					"otp_verified_at": "2026-06-11 12:00:00",
					"consent_receipt": "{'signed': true}",
					"websub_delivered_at": "2026-06-11 13:00:00",
					"consent_type": "Personal Data Sharing",
					"purpose": "Loan Credit Risk Analysis",
					"validity_from": "2026-06-11",
					"validity_to": "2027-06-11",
					"requested_data_fields": [
						{"field_name": "Phone Number", "field_value": "+251999888777"},
						{"field_name": "Location", "field_value": "Addis Ababa"},
					],
				}
			)
			consent.insert(ignore_permissions=True)
			frappe.db.set_value("A2C Farmer Profile", farmer_profile_name, "consent_id", consent.name)
			frappe.db.commit()

		doc = frappe.get_doc(
			{
				"doctype": "A2C Loan Application",
				"first_name": "API_TEST_FARMER",
				"last_name": "Test",
				"phone_number": "+251999888777",
				"loan_amount": 5000,
				"requested_amount": 5000,
				"bank": "Test Bank",
				"loan_type": "Input Loan",
				"status": "Active",
				"region": "Addis Ababa",
				"woreda": "Gulele",
				"lead_id": "TEST_LEAD_999",
				"farmer_profile": farmer_profile_name,
			}
		)
		doc.insert(ignore_permissions=True)
		self.app_id = doc.name
		frappe.db.commit()

	def tearDown(self):
		if hasattr(self, "app_id") and frappe.db.exists("A2C Loan Application", self.app_id):
			# Loan is submittable; clear docstatus so a submitted (Approved/Rejected) test record
			# can be force-deleted without the cancel-first guard.
			frappe.db.sql("UPDATE `tabA2C Loan Application` SET docstatus=0 WHERE name=%s", self.app_id)
			frappe.delete_doc("A2C Loan Application", self.app_id, ignore_permissions=True, force=True)
		frappe.db.sql(
			"DELETE FROM `tabA2C Consent Request` "
			"WHERE reference_doctype='A2C Lead' AND reference_name='TEST_LEAD_999'"
		)

		# Reset response state to avoid test pollution
		if getattr(frappe.local, "response", None):
			frappe.local.response.type = None
			frappe.local.response.filename = None
			frappe.local.response.filecontent = None
			frappe.local.response.display_content_as = None

		frappe.db.commit()

	def test_1_get_loan_summary(self):
		res = get_loan_summary()
		self.assertEqual(res["status"], "success")
		self.assertIn("data", res)
		self.assertIn("total", res["data"])
		self.assertIn("tab_counts", res["data"])
		self.assertEqual(res["data"]["tab_counts"]["all"], res["data"]["total"])
		self.assertIn("my", res["data"]["tab_counts"])
		self.assertIn("unassigned", res["data"]["tab_counts"])

	def test_1a_loan_summary_zero_fills_every_visible_stage(self):
		"""`stages` is the contract the bank and agent KPI cards read.

		They key on stage names directly, so stages are zero-filled from the
		caller's visible stage set to ensure empty stages still render as 0.
		"""
		data = get_loan_summary()["data"]

		self.assertNotIn("by_status", data)
		self.assertIn("stages", data)
		self.assertIn("Active", data["stages"])
		for stage, count in data["stages"].items():
			self.assertIsInstance(count, int, f"{stage} is not a count")

		self.assertEqual(sum(data["stages"].values()), data["total"])

	def test_1b_workflow_helpers(self):
		self.assertEqual(get_workflow_initial_state("A2C Loan Application"), "Active")

	def test_2_get_all_loans(self):
		res = get_all_loans(status="Active", page_size=10)
		self.assertEqual(res["status"], "success")
		self.assertTrue(len(res["data"]) > 0)
		self.assertIn("pagination", res)
		self.assertEqual(res["pagination"]["limit"], 10)
		self.assertEqual(res["pagination"]["page"], 1)
		found = False
		for r in res["data"]:
			if r["application_id"] == self.app_id:
				found = True
				self.assertEqual(r["lead_id"], "TEST_LEAD_999")
				self.assertEqual(r["step"], 1)
		self.assertTrue(found)

		# Test filtering by lead_id
		res_lead = get_all_loans(lead_id="TEST_LEAD_999")
		self.assertEqual(res_lead["status"], "success")
		self.assertTrue(len(res_lead["data"]) > 0)
		for r in res_lead["data"]:
			self.assertEqual(r["lead_id"], "TEST_LEAD_999")

		# Test filtering by search_query (phone number)
		res_phone = get_all_loans(search_query="+251999888777")
		self.assertEqual(res_phone["status"], "success")
		self.assertTrue(any(r["application_id"] == self.app_id for r in res_phone["data"]))

		# Test filtering by search_query (first name)
		res_name = get_all_loans(search_query="API_TEST_FARMER")
		self.assertEqual(res_name["status"], "success")
		self.assertTrue(any(r["application_id"] == self.app_id for r in res_name["data"]))

		# Test filtering by loan_officer (assignee)
		frappe.db.set_value("A2C Loan Application", self.app_id, "loan_officer", "Administrator")
		frappe.db.commit()
		res_officer = get_all_loans(loan_officer="Administrator")
		self.assertEqual(res_officer["status"], "success")
		self.assertTrue(any(r["application_id"] == self.app_id for r in res_officer["data"]))

		# The same loan must NOT appear when filtering for unassigned loans
		res_unassigned = get_all_loans(loan_officer="unassigned")
		self.assertEqual(res_unassigned["status"], "success")
		self.assertFalse(any(r["application_id"] == self.app_id for r in res_unassigned["data"]))

	def test_2a_get_all_loans_returns_applicant_and_location(self):
		"""The list has to carry the columns every dashboard renders.

		first_name/last_name were searchable but not selected, and `location` was
		selected but is not a field on the doctype -- Frappe drops an unknown field
		from the SELECT silently, so both the applicant and the location column had
		nothing behind them and rendered a dash on every row.
		"""
		res = get_all_loans(lead_id="TEST_LEAD_999")
		self.assertEqual(res["status"], "success")

		row = next(r for r in res["data"] if r["application_id"] == self.app_id)
		self.assertEqual(row["first_name"], "API_TEST_FARMER")
		self.assertEqual(row["last_name"], "Test")
		self.assertEqual(row["region"], "Addis Ababa")
		self.assertEqual(row["woreda"], "Gulele")
		self.assertIn("stage_label", row)
		self.assertNotIn("location", row)

	def test_2b_get_all_loans_location_filter(self):
		"""Location filters on the real hierarchy fields, prefix-matched and ANDed."""
		res = get_all_loans(region="Addis")
		self.assertEqual(res["status"], "success")
		self.assertTrue(any(r["application_id"] == self.app_id for r in res["data"]))

		res_woreda = get_all_loans(region="Addis", woreda="Gulele")
		self.assertEqual(res_woreda["status"], "success")
		self.assertTrue(any(r["application_id"] == self.app_id for r in res_woreda["data"]))

		# Both levels are ANDed, so a mismatched woreda excludes the row rather than
		# widening the result.
		res_miss = get_all_loans(region="Addis", woreda="Nowhere")
		self.assertEqual(res_miss["status"], "success")
		self.assertFalse(any(r["application_id"] == self.app_id for r in res_miss["data"]))

	def test_2e_get_all_loans_status_filters_the_bank_stage(self):
		"""`status` filters by stage label, stage_id, external_code or 'Active'."""
		res_active = get_all_loans(status="Active")
		self.assertEqual(res_active["status"], "success")
		self.assertTrue(any(r["application_id"] == self.app_id for r in res_active["data"]))

		# Unknown status is a 400 validation error
		frappe.local.response = frappe._dict({"http_status_code": 200})
		res_unknown = get_all_loans(status="NO-SUCH-STAGE")
		self.assertEqual(res_unknown["status"], "error")
		self.assertEqual(res_unknown["code"], "VALIDATION_ERROR")
		frappe.local.response["http_status_code"] = 200

	def test_3_get_basic_profile(self):
		res = get_basic_profile(lead_id="TEST_LEAD_999")
		self.assertEqual(res["status"], "success")
		self.assertTrue(res["data"]["farmer_profile_created"])
		self.assertEqual(res["data"]["first_name"], "API_TEST_FARMER")
		self.assertEqual(res["data"]["phone_number"], "+251999888777")
		self.assertNotIn("loan_amount", res["data"])

		# Verify consent fields are NOT returned by default
		self.assertNotIn("websub_delivered_at", res["data"])
		self.assertNotIn("consent_type", res["data"])
		self.assertNotIn("purpose", res["data"])
		self.assertNotIn("validity_from", res["data"])
		self.assertNotIn("validity_to", res["data"])
		self.assertNotIn("requested_data_fields", res["data"])

		# Request with include_consent_data=1
		res_consent = get_basic_profile(lead_id="TEST_LEAD_999", include_consent_data=1)
		self.assertEqual(res_consent["status"], "success")
		self.assertTrue(res_consent["data"]["websub_delivered_at"].startswith("2026-06-11T13:00:00"))
		self.assertEqual(res_consent["data"]["consent_type"], "Personal Data Sharing")
		self.assertEqual(res_consent["data"]["purpose"], "Loan Credit Risk Analysis")
		self.assertEqual(res_consent["data"]["validity_from"], "2026-06-11")
		self.assertEqual(res_consent["data"]["validity_to"], "2027-06-11")
		self.assertIn("requested_data_fields", res_consent["data"])
		self.assertEqual(len(res_consent["data"]["requested_data_fields"]), 2)
		fields_dict = {
			f["field_name"]: f["field_value"] for f in res_consent["data"]["requested_data_fields"]
		}
		self.assertEqual(fields_dict["Phone Number"], "+251999888777")
		self.assertEqual(fields_dict["Location"], "Addis Ababa")

	def test_3_get_basic_profile_errors(self):
		# Missing lead_id
		res = get_basic_profile(lead_id=None)
		self.assertEqual(frappe.local.response.get("http_status_code"), 400)
		self.assertEqual(res.get("status"), "error")
		self.assertEqual(res.get("code"), "VALIDATION_ERROR")

		# Reset response status code for next assertions
		frappe.local.response["http_status_code"] = 200

		# Nonexistent lead_id
		res_nonexistent = get_basic_profile(lead_id="LEAD-2026-00000")
		self.assertEqual(frappe.local.response.get("http_status_code"), 404)
		self.assertEqual(res_nonexistent.get("status"), "error")
		self.assertEqual(res_nonexistent.get("code"), "NOT_FOUND")
		self.assertEqual(res_nonexistent.get("message"), "A2C Lead LEAD-2026-00000 not found")
		frappe.local.response["http_status_code"] = 200

	def test_3c_get_basic_profile_pending_consent(self):
		# 1. Create a lead with no farmer profile linked
		lead_name = "TEST_LEAD_PENDING"
		if not frappe.db.exists("A2C Lead", lead_name):
			lead = frappe.get_doc(
				{
					"doctype": "A2C Lead",
					"phone_number": "+251999888111",
					"lead_source": "Agent Entry",
					"status": "Active",
				}
			)
			lead.insert(ignore_permissions=True)
			frappe.db.sql("UPDATE `tabA2C Lead` SET name=%s WHERE name=%s", (lead_name, lead.name))
			frappe.db.commit()

		# Ensure no farmer profile is linked
		frappe.db.set_value("A2C Lead", lead_name, "farmer_profile", None)
		# Delete any existing consent requests for this test lead
		frappe.db.sql(
			"DELETE FROM `tabA2C Consent Request` WHERE reference_doctype='A2C Lead' AND reference_name=%s",
			(lead_name,),
		)
		frappe.db.commit()

		# 2. Call get_basic_profile - should return 400 ValidationError response
		res_error = get_basic_profile(lead_id=lead_name)
		self.assertEqual(frappe.local.response.get("http_status_code"), 400)
		self.assertEqual(res_error.get("status"), "error")
		self.assertEqual(res_error.get("code"), "VALIDATION_ERROR")
		self.assertIn("Farmer Profile not found", res_error.get("message"))
		frappe.local.response["http_status_code"] = 200

		# 3. Create a pending consent request linked to this lead
		consent = frappe.get_doc(
			{
				"doctype": "A2C Consent Request",
				"farmer": "Pending Farmer",
				"farmer_fayda_id": "987654321",
				"partner": "Test Partner",
				"reference_doctype": "A2C Lead",
				"reference_name": lead_name,
				"status": "Pending OTP",
			}
		)
		consent.insert(ignore_permissions=True)
		frappe.db.commit()

		# 4. Call get_basic_profile again - should return 200 with farmer_profile_created: False
		res = get_basic_profile(lead_id=lead_name)
		self.assertEqual(res["status"], "success")
		self.assertFalse(res["data"]["farmer_profile_created"])
		self.assertEqual(res["data"]["consent_request"]["name"], consent.name)
		self.assertEqual(res["data"]["consent_request"]["status"], "Pending OTP")

		# Clean up
		frappe.delete_doc("A2C Consent Request", consent.name, ignore_permissions=True, force=True)
		frappe.delete_doc("A2C Lead", lead_name, ignore_permissions=True, force=True)
		frappe.db.commit()

	def test_3b_update_basic_profile(self):
		res = update_basic_profile(
			lead_id="TEST_LEAD_999",
			email="updated_farmer@example.com",
			region="Sidama",
			woreda="Hawassa",
			kebele="01",
		)
		self.assertEqual(res["status"], "success")
		self.assertEqual(res["data"]["email"], "updated_farmer@example.com")
		self.assertEqual(res["data"]["region"], "Sidama")
		self.assertEqual(res["data"]["woreda"], "Hawassa")
		self.assertEqual(res["data"]["kebele"], "01")

		# Verify database documents got updated
		lead_doc = frappe.get_doc("A2C Lead", "TEST_LEAD_999")
		farmer_doc = frappe.get_doc("A2C Farmer Profile", lead_doc.farmer_profile)
		self.assertEqual(farmer_doc.email, "updated_farmer@example.com")
		self.assertEqual(farmer_doc.region, "Sidama")
		self.assertEqual(farmer_doc.woreda, "Hawassa")
		self.assertEqual(farmer_doc.kebele, "01")
		self.assertEqual(lead_doc.email, "updated_farmer@example.com")

	def test_4_get_full_profile(self):
		res = get_full_profile(application_id=self.app_id)
		self.assertEqual(res["status"], "success")
		self.assertEqual(res["data"]["first_name"], "API_TEST_FARMER")
		self.assertEqual(res["data"]["loan_amount"], 5000.0)
		self.assertEqual(res["data"]["status"], "Active")

	def test_5_supporting_documents(self):
		# Create a File document programmatically
		file_doc = frappe.get_doc(
			{
				"doctype": "File",
				"file_name": "test_doc.png",
				"content": b"dummy content",
				"attached_to_doctype": "A2C Loan Application",
				"attached_to_name": self.app_id,
				"is_private": 1,
			}
		)
		file_doc.insert(ignore_permissions=True)
		frappe.db.commit()
		file_id = file_doc.name

		# 1. Get supporting documents
		res = get_supporting_documents(application_id=self.app_id)
		self.assertEqual(res["status"], "success")
		self.assertEqual(len(res["data"]), 1)
		self.assertEqual(res["data"][0]["name"], file_id)

		# 1.5 Download supporting document
		download_supporting_document(file_id=file_id)
		self.assertEqual(frappe.local.response.filename, "test_doc.png")

		file_content = frappe.local.response.filecontent
		if isinstance(file_content, bytes):
			file_content = file_content.decode("utf-8")
		self.assertEqual(file_content, "dummy content")

		self.assertEqual(frappe.local.response.type, "download")
		self.assertIsNone(frappe.local.response.get("display_content_as"))

		# Test downloading with view=1 (inline)
		download_supporting_document(file_id=file_id, view=1)
		self.assertEqual(frappe.local.response.display_content_as, "inline")

		# Reset response state to avoid test pollution
		if getattr(frappe.local, "response", None):
			frappe.local.response.type = None
			frappe.local.response.filename = None
			frappe.local.response.filecontent = None
			frappe.local.response.display_content_as = None

		# 2. Delete supporting document
		res_del = delete_supporting_document(application_id=self.app_id, file_id=file_id)
		self.assertEqual(res_del["status"], "success")
		self.assertEqual(res_del["message"], "Document deleted successfully.")

		# 3. Check if file is actually deleted
		self.assertFalse(frappe.db.exists("File", file_id))

		# 4. Get again, should be empty
		res_after = get_supporting_documents(application_id=self.app_id)
		self.assertEqual(res_after["status"], "success")
		self.assertEqual(len(res_after["data"]), 0)

		# 5. Check audit event
		audit_events = frappe.get_all(
			"A2C Loan Application Audit Event",
			filters={"loan_application": self.app_id, "event_type": "Document Deleted"},
			fields=["event_type", "event_title", "event_description"],
		)
		self.assertEqual(len(audit_events), 1)
		self.assertEqual(audit_events[0]["event_type"], "Document Deleted")
		self.assertIn("Deleted document: test_doc.png", audit_events[0]["event_description"])

	def test_6_update_loan_step(self):
		# Ensure it starts at 1
		frappe.db.set_value("A2C Loan Application", self.app_id, "current_step", 1)
		frappe.db.commit()

		# 1. Invalid jump: 1 to 3 should raise ValidationError
		res = update_loan_step(application_id=self.app_id, step=3)
		self.assertEqual(res.get("status"), "error")
		self.assertEqual(res.get("code"), "VALIDATION_ERROR")

		# 2. Valid sequential step: 1 to 2
		res = update_loan_step(application_id=self.app_id, step=2)
		self.assertEqual(res["status"], "success")
		self.assertEqual(res["message"], "Loan application step updated to 2")

		# 3. Invalid jump: 2 to 4 should raise ValidationError
		res = update_loan_step(application_id=self.app_id, step=4)
		self.assertEqual(res.get("status"), "error")
		self.assertEqual(res.get("code"), "VALIDATION_ERROR")

		# 4. Valid sequential step: 2 to 3
		res = update_loan_step(application_id=self.app_id, step=3)
		self.assertEqual(res["status"], "success")

		# 5. Backward step: 3 to 1 should be allowed
		res = update_loan_step(application_id=self.app_id, step=1)
		self.assertEqual(res["status"], "success")

		# 6. Step out of bounds: 0 or 5 should raise ValidationError
		res = update_loan_step(application_id=self.app_id, step=0)
		self.assertEqual(res.get("status"), "error")
		self.assertEqual(res.get("code"), "VALIDATION_ERROR")

		res = update_loan_step(application_id=self.app_id, step=5)
		self.assertEqual(res.get("status"), "error")
		self.assertEqual(res.get("code"), "VALIDATION_ERROR")

	def test_7_rejected_loan_status_locked(self):
		# Reject follows the legal workflow path Draft -> Processing -> Rejected. Rejection is a
		# submit action, so the record ends at docstatus 1 (frozen).

		# Clean up any pre-existing audit events for this application
		frappe.db.sql(
			"DELETE FROM `tabA2C Loan Application Audit Event` WHERE loan_application=%s",
			self.app_id,
		)
		frappe.db.commit()

		res = update_loan_status(application_id=self.app_id, status="In Transition")
		self.assertEqual(res["status"], "success")
		res = update_loan_status(
			application_id=self.app_id,
			status="Rejected",
			reason="Insufficient collateral provided.",
		)
		self.assertEqual(res["status"], "success")

		doc = frappe.get_doc("A2C Loan Application", self.app_id)
		self.assertEqual(doc.status, "Rejected")
		self.assertEqual(doc.stage_label, "Rejected")
		self.assertEqual(doc.docstatus, 1)

		# Verify audit events were created for both transitions
		audit_events = frappe.get_all(
			"A2C Loan Application Audit Event",
			filters={"loan_application": self.app_id},
			fields=["event_type", "event_title", "event_description"],
			order_by="creation asc",
		)
		self.assertEqual(len(audit_events), 2)

		# First event: Active -> In Transition
		self.assertEqual(audit_events[0]["event_type"], "Status Changed")
		self.assertEqual(audit_events[0]["event_title"], "Status Updated")
		self.assertIn("In Transition", audit_events[0]["event_description"])
		self.assertIn("Administrator", audit_events[0]["event_description"])

		# Second event: In Transition -> Completed/Rejected (with reason)
		self.assertIn("Rejected", audit_events[1]["event_description"])
		self.assertIn("Insufficient collateral provided.", audit_events[1]["event_description"])
		self.assertIn("Administrator", audit_events[1]["event_description"])

		# A further transition is illegal from a terminal state and rejected.
		res = update_loan_status(application_id=self.app_id, status="Active")
		self.assertEqual(res["status"], "error")

		# No additional audit event should be created for the failed transition
		audit_count = frappe.db.count("A2C Loan Application Audit Event", {"loan_application": self.app_id})
		self.assertEqual(audit_count, 2)

		# The submitted record is frozen: a direct edit + save is blocked by docstatus.
		doc.status = "In Transition"
		self.assertRaises(frappe.ValidationError, doc.save)

	def test_7b_invalid_status_rejected_by_validator(self):
		res = update_loan_status(application_id=self.app_id, status="NotARealState")
		self.assertEqual(res["status"], "error")
		self.assertEqual(res["code"], "VALIDATION_ERROR")
		self.assertEqual(frappe.local.response.get("http_status_code"), 400)
		frappe.local.response["http_status_code"] = 200

	def test_8_create_loan_application_copies_profile_details(self):
		# 1. Clean up any existing loan application for TEST_LEAD_999 first (since setUp creates one)
		app_name = frappe.db.exists("A2C Loan Application", {"lead_id": "TEST_LEAD_999"})
		if app_name:
			frappe.db.sql("UPDATE `tabA2C Loan Application` SET docstatus=0 WHERE name=%s", app_name)
			frappe.delete_doc("A2C Loan Application", app_name, ignore_permissions=True, force=True)

		# Clean up existing credit info
		frappe.db.sql("DELETE FROM `tabA2C Credit Information` WHERE lead='TEST_LEAD_999'")

		# 2. Setup Farmer Profile details
		farmer_profile_name = frappe.db.get_value("A2C Lead", "TEST_LEAD_999", "farmer_profile")
		farmer = frappe.get_doc("A2C Farmer Profile", farmer_profile_name)
		farmer.gender = "Male"
		farmer.marital_status = "Married"
		farmer.education_level = "Degree and above"
		farmer.total_farmland_size_as_landowner = 15.5
		farmer.save(ignore_permissions=True)

		# 3. Ensure a test bank + loan product exist so create_loan_application can resolve the bank
		test_bank = frappe.db.get_value("A2C Participating Bank", {"bank_code": "TEST_LOAN_API_BANK"}, "name")
		if not test_bank:
			bank_doc = frappe.get_doc(
				{
					"doctype": "A2C Participating Bank",
					"registered_city": "Test City",
					"kyc_document": "/private/files/test_kyc.pdf",
					"gro_name": "Test GRO",
					"ops_name": "Test Ops",
					"bank_name": "Test Loan API Bank",
					"bank_code": "TEST_LOAN_API_BANK",
					"status": "In Review",
					"entity_type": "Commercial Bank",
					"registered_email": "loanapi@test.com",
					"registered_phone": "+251911000000",
					"registered_region": "Addis Ababa",
					"registered_country": "Ethiopia",
				}
			).insert(ignore_permissions=True)
			test_bank = bank_doc.name

		test_product = frappe.db.get_value(
			"A2C Loan Product", {"bank": test_bank, "status": "Active"}, "name"
		)
		if not test_product:
			prod_doc = frappe.get_doc(
				{
					"doctype": "A2C Loan Product",
					"product_name": "Test Loan API Product",
					"bank": test_bank,
					"min_interest_rate": 5,
					"max_amount": 100000,
					"tenure_months": 12,
					"status": "Active",
				}
			).insert(ignore_permissions=True)
			test_product = prod_doc.name
		frappe.db.commit()

		# 3. Create Credit Info with loan_product so bank can be resolved
		credit_info = frappe.get_doc(
			{
				"doctype": "A2C Credit Information",
				"lead": "TEST_LEAD_999",
				"loan_type": "Input loan (seeds, agrochemicals)",
				"loan_amount": 12000,
				"purpose_message": "Test loan purpose",
				"loan_product": test_product,
			}
		)
		credit_info.insert(ignore_permissions=True)
		frappe.db.commit()

		# 4. Call create_loan_application API
		res = create_loan_application(lead_id="TEST_LEAD_999")
		self.assertEqual(res["status"], "success")
		app_id = res["data"]["application_id"]

		# Loan creation does NOT change the lead status (that is driven via the Lead Workflow
		# by the frontend through update_lead_status). The new loan starts in Active.
		loan_status = frappe.db.get_value("A2C Loan Application", app_id, "status")
		self.assertEqual(loan_status, "Active")

		# 5. Fetch the newly created loan application and assert fields were copied
		loan_app = frappe.get_doc("A2C Loan Application", app_id)
		self.assertEqual(loan_app.gender, "Male")
		self.assertEqual(loan_app.marital_status, "Married")
		self.assertEqual(loan_app.education_level, "Degree and above")
		self.assertEqual(float(loan_app.total_farmland_size_as_landowner), 15.5)
		self.assertEqual(float(loan_app.loan_amount), 12000.0)
		self.assertEqual(loan_app.loan_reason, "Test loan purpose")

		# 6. Call get_full_profile API and verify response includes these fields
		profile_res = get_full_profile(application_id=app_id)
		self.assertEqual(profile_res["status"], "success")
		self.assertEqual(profile_res["data"]["gender"], "Male")
		self.assertEqual(profile_res["data"]["marital_status"], "Married")
		self.assertEqual(profile_res["data"]["education_level"], "Degree and above")
		self.assertEqual(float(profile_res["data"]["total_farmland_size_as_landowner"]), 15.5)

		# Clean up
		frappe.delete_doc("A2C Loan Application", app_id, ignore_permissions=True, force=True)
		frappe.delete_doc("A2C Credit Information", credit_info.name, ignore_permissions=True, force=True)
		frappe.db.commit()


class TestLoanStatusReadPath(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		frappe.set_user("Administrator")
		cls.h = frappe.generate_hash(length=6)

		# Create 2 test banks
		cls.bank_1 = frappe.get_doc(
			{
				"doctype": "A2C Participating Bank",
				"bank_name": f"ReadPath Bank 1 {cls.h}",
				"bank_code": f"RP_BANK_1_{cls.h}",
				"registered_phone": f"+251911{cls.h[:6]}",
				"kyc_document": "/private/files/test_kyc.pdf",
				"gro_name": "Test GRO 1",
				"ops_name": "Test Ops 1",
				"status": "Active",
			}
		).insert(ignore_permissions=True, ignore_mandatory=True)
		frappe.db.set_value("A2C Participating Bank", cls.bank_1.name, "status", "Active")

		cls.bank_2 = frappe.get_doc(
			{
				"doctype": "A2C Participating Bank",
				"bank_name": f"ReadPath Bank 2 {cls.h}",
				"bank_code": f"RP_BANK_2_{cls.h}",
				"registered_phone": f"+251912{cls.h[:6]}",
				"kyc_document": "/private/files/test_kyc.pdf",
				"gro_name": "Test GRO 2",
				"ops_name": "Test Ops 2",
				"status": "Active",
			}
		).insert(ignore_permissions=True, ignore_mandatory=True)
		frappe.db.set_value("A2C Participating Bank", cls.bank_2.name, "status", "Active")

		# Load auto-seeded stages for both banks
		cls.stages_bank_1 = {
			s.label: s
			for s in frappe.get_all(
				"A2C Loan Status Stage",
				filters={"bank": cls.bank_1.name},
				fields=["name", "stage_id", "label", "sequence", "archetype_state"],
			)
		}
		cls.stages_bank_2 = {
			s.label: s
			for s in frappe.get_all(
				"A2C Loan Status Stage",
				filters={"bank": cls.bank_2.name},
				fields=["name", "stage_id", "label", "sequence", "archetype_state"],
			)
		}

		# Create products
		cls.prod_1 = frappe.get_doc(
			{
				"doctype": "A2C Loan Product",
				"product_name": f"RP Prod 1 {cls.h}",
				"bank": cls.bank_1.name,
				"min_interest_rate": 5,
				"max_amount": 50000,
				"tenure_months": 12,
				"status": "Active",
			}
		).insert(ignore_permissions=True, ignore_mandatory=True)
		frappe.db.set_value("A2C Loan Product", cls.prod_1.name, "status", "Active")

		cls.prod_2 = frappe.get_doc(
			{
				"doctype": "A2C Loan Product",
				"product_name": f"RP Prod 2 {cls.h}",
				"bank": cls.bank_2.name,
				"min_interest_rate": 5,
				"max_amount": 50000,
				"tenure_months": 12,
				"status": "Active",
			}
		).insert(ignore_permissions=True, ignore_mandatory=True)
		frappe.db.set_value("A2C Loan Product", cls.prod_2.name, "status", "Active")

		# Create users
		cls.farmer_user = f"farmer_rp_{cls.h}@test.com"
		if not frappe.db.exists("User", cls.farmer_user):
			frappe.get_doc(
				{
					"doctype": "User",
					"email": cls.farmer_user,
					"first_name": "RPFarmer",
					"roles": [{"role": "A2C Farmer"}],
				}
			).insert(ignore_permissions=True, ignore_mandatory=True)

		cls.dev_agent_user = f"dev_rp_{cls.h}@test.com"
		if not frappe.db.exists("User", cls.dev_agent_user):
			frappe.get_doc(
				{
					"doctype": "User",
					"email": cls.dev_agent_user,
					"first_name": "RPDevAgent",
					"roles": [{"role": "A2C Development Agent"}],
				}
			).insert(ignore_permissions=True, ignore_mandatory=True)

		cls.bank_admin_user = f"bank_admin_rp_{cls.h}@test.com"
		if not frappe.db.exists("User", cls.bank_admin_user):
			frappe.get_doc(
				{
					"doctype": "User",
					"email": cls.bank_admin_user,
					"first_name": "RPBankAdmin",
					"roles": [{"role": "A2C Bank Admin"}],
				}
			).insert(ignore_permissions=True, ignore_mandatory=True)
			frappe.get_doc(
				{
					"doctype": "User Permission",
					"user": cls.bank_admin_user,
					"allow": "A2C Participating Bank",
					"for_value": cls.bank_1.name,
				}
			).insert(ignore_permissions=True)

		# Create Farmer Profile & Consent
		cls.consent = frappe.get_doc(
			{
				"doctype": "A2C Consent Request",
				"farmer": f"openg2p-rp-{cls.h}",
				"farmer_fayda_id": f"fayda-rp-{cls.h}",
				"status": "Approved",
				"owner": cls.farmer_user,
			}
		).insert(ignore_permissions=True, ignore_mandatory=True)

		cls.profile = frappe.get_doc(
			{
				"doctype": "A2C Farmer Profile",
				"user": cls.farmer_user,
				"consent_id": cls.consent.name,
				"first_name": "RPFarmer",
				"last_name": "Test",
				"phone_number": "+251911888111",
				"region": "Addis Ababa",
				"woreda": "Gulele",
				"kebele": "01",
			}
		).insert(ignore_permissions=True, ignore_mandatory=True)

		frappe.db.commit()

	@classmethod
	def tearDownClass(cls):
		frappe.set_user("Administrator")
		frappe.db.delete("A2C Loan Application", {"bank": ["in", [cls.bank_1.name, cls.bank_2.name]]})
		frappe.db.delete("A2C Loan Status Stage", {"bank": ["in", [cls.bank_1.name, cls.bank_2.name]]})
		frappe.db.delete("A2C Loan Product", {"bank": ["in", [cls.bank_1.name, cls.bank_2.name]]})
		frappe.delete_doc("A2C Participating Bank", cls.bank_1.name, force=True)
		frappe.delete_doc("A2C Participating Bank", cls.bank_2.name, force=True)
		frappe.delete_doc("A2C Farmer Profile", cls.profile.name, force=True)
		frappe.delete_doc("A2C Consent Request", cls.consent.name, force=True)
		frappe.db.commit()

	def test_farmer_list_and_detail_status_progression(self):
		"""1. Farmer list + detail return Submitted (not In Transition) for a submitted application, and Active before submission."""
		from oan_a2c.api.v1.farmer.applications import get_application, list_applications, submit_application

		# Create Active application
		app = frappe.get_doc(
			{
				"doctype": "A2C Loan Application",
				"application_source": "Self Service",
				"farmer_profile": self.profile.name,
				"bank": self.bank_1.name,
				"loan_product": self.prod_1.name,
				"requested_amount": 1000,
				"loan_amount": 1000,
				"consent_id": self.consent.name,
				"status": "Active",
				"current_step": 1,
				"first_name": "RPFarmer",
				"last_name": "Test",
				"phone_number": "+251911888111",
				"owner": self.farmer_user,
			}
		).insert(ignore_permissions=True)
		frappe.db.commit()

		frappe.set_user(self.farmer_user)

		# Check Active state in list and detail
		detail_res = get_application(application_id=app.name)
		self.assertEqual(detail_res["data"]["status"], "Active")
		self.assertIsNone(detail_res["data"]["stage_id"])
		self.assertIsNone(detail_res["data"]["sequence"])
		self.assertFalse(detail_res["data"]["is_terminal"])
		self.assertFalse(detail_res["data"]["is_successful"])

		list_res = list_applications()
		app_row = next(r for r in list_res["data"] if r["application_id"] == app.name)
		self.assertEqual(app_row["status"], "Active")

		# Submit application
		submit_res = submit_application(application_id=app.name)
		self.assertEqual(submit_res["status"], "success")

		# Check Submitted state in list and detail
		detail_submitted = get_application(application_id=app.name)
		self.assertEqual(detail_submitted["data"]["status"], "Submitted")
		self.assertEqual(detail_submitted["data"]["stage_id"], self.stages_bank_1["Submitted"].stage_id)
		self.assertEqual(detail_submitted["data"]["sequence"], 1)
		self.assertFalse(detail_submitted["data"]["is_terminal"])
		self.assertFalse(detail_submitted["data"]["is_successful"])

		list_submitted = list_applications()
		app_row_sub = next(r for r in list_submitted["data"] if r["application_id"] == app.name)
		self.assertEqual(app_row_sub["status"], "Submitted")
		self.assertEqual(app_row_sub["stage_id"], self.stages_bank_1["Submitted"].stage_id)
		self.assertEqual(app_row_sub["sequence"], 1)

		# Clean up
		frappe.set_user("Administrator")
		frappe.delete_doc("A2C Loan Application", app.name, force=True)
		frappe.db.commit()

	def test_get_all_loans_status_filter_resolves_stage_labels(self):
		"""2. get_all_loans(status="Verified") returns rows instead of 400."""
		from oan_a2c.api.v1.loan_applications import get_all_loans

		app = frappe.get_doc(
			{
				"doctype": "A2C Loan Application",
				"application_source": "Agent",
				"farmer_profile": self.profile.name,
				"bank": self.bank_1.name,
				"loan_product": self.prod_1.name,
				"requested_amount": 1000,
				"loan_amount": 1000,
				"consent_id": self.consent.name,
				"status": "In Transition",
				"stage_id": self.stages_bank_1["Verified"].stage_id,
				"stage_label": "Verified",
				"current_step": 2,
				"first_name": "RPFarmer",
				"last_name": "Test",
				"phone_number": "+251911888111",
			}
		).insert(ignore_permissions=True)
		frappe.db.commit()

		frappe.set_user(self.dev_agent_user)
		res = get_all_loans(status="Verified")
		self.assertEqual(res["status"], "success")
		self.assertTrue(any(r["application_id"] == app.name for r in res["data"]))

		# Clean up
		frappe.set_user("Administrator")
		frappe.delete_doc("A2C Loan Application", app.name, force=True)
		frappe.db.commit()

	def test_farmer_list_applications_status_filter_resolves_stage_labels(self):
		"""3. Farmer list_applications(status="Verified") returns rows instead of silently empty."""
		from oan_a2c.api.v1.farmer.applications import list_applications

		app = frappe.get_doc(
			{
				"doctype": "A2C Loan Application",
				"application_source": "Self Service",
				"farmer_profile": self.profile.name,
				"bank": self.bank_1.name,
				"loan_product": self.prod_1.name,
				"requested_amount": 1000,
				"loan_amount": 1000,
				"consent_id": self.consent.name,
				"status": "In Transition",
				"stage_id": self.stages_bank_1["Verified"].stage_id,
				"stage_label": "Verified",
				"current_step": 2,
				"first_name": "RPFarmer",
				"last_name": "Test",
				"phone_number": "+251911888111",
				"owner": self.farmer_user,
			}
		).insert(ignore_permissions=True)
		frappe.db.commit()

		frappe.set_user(self.farmer_user)
		res = list_applications(status="Verified")
		self.assertEqual(res["status"], "success")
		self.assertTrue(any(r["application_id"] == app.name for r in res["data"]))

		# Clean up
		frappe.set_user("Administrator")
		frappe.delete_doc("A2C Loan Application", app.name, force=True)
		frappe.db.commit()

	def test_dev_agent_filters_across_banks(self):
		"""4. A Development Agent filtering status="Verified" across two banks whose Verified stages have different stage_ids gets both banks' rows."""
		from oan_a2c.api.v1.loan_applications import get_all_loans

		app_1 = frappe.get_doc(
			{
				"doctype": "A2C Loan Application",
				"application_source": "Agent",
				"farmer_profile": self.profile.name,
				"bank": self.bank_1.name,
				"loan_product": self.prod_1.name,
				"requested_amount": 1000,
				"loan_amount": 1000,
				"consent_id": self.consent.name,
				"status": "In Transition",
				"stage_id": self.stages_bank_1["Verified"].stage_id,
				"stage_label": "Verified",
				"current_step": 2,
				"first_name": "RPFarmer",
				"last_name": "Bank1",
				"phone_number": "+251911888111",
			}
		).insert(ignore_permissions=True)

		app_2 = frappe.get_doc(
			{
				"doctype": "A2C Loan Application",
				"application_source": "Agent",
				"farmer_profile": self.profile.name,
				"bank": self.bank_2.name,
				"loan_product": self.prod_2.name,
				"requested_amount": 2000,
				"loan_amount": 2000,
				"consent_id": self.consent.name,
				"status": "In Transition",
				"stage_id": self.stages_bank_2["Verified"].stage_id,
				"stage_label": "Verified",
				"current_step": 2,
				"first_name": "RPFarmer",
				"last_name": "Bank2",
				"phone_number": "+251911888222",
			}
		).insert(ignore_permissions=True)
		frappe.db.commit()

		frappe.set_user(self.dev_agent_user)
		res = get_all_loans(status="Verified")
		self.assertEqual(res["status"], "success")

		matched_ids = [r["application_id"] for r in res["data"]]
		self.assertIn(app_1.name, matched_ids)
		self.assertIn(app_2.name, matched_ids)

		# Clean up
		frappe.set_user("Administrator")
		frappe.delete_doc("A2C Loan Application", app_1.name, force=True)
		frappe.delete_doc("A2C Loan Application", app_2.name, force=True)
		frappe.db.commit()

	def test_sync_stages_rename_propagates_to_read_path(self):
		"""5. Renaming a stage through sync_stages changes what the farmer sees on an existing application."""
		from oan_a2c.api.v1.farmer.applications import get_application
		from oan_a2c.api.v1.loan_applications import get_loan_metadata, get_loan_summary
		from oan_a2c.api.v1.seller.loan_stages import sync_stages

		app = frappe.get_doc(
			{
				"doctype": "A2C Loan Application",
				"application_source": "Self Service",
				"farmer_profile": self.profile.name,
				"bank": self.bank_1.name,
				"loan_product": self.prod_1.name,
				"requested_amount": 1000,
				"loan_amount": 1000,
				"consent_id": self.consent.name,
				"status": "In Transition",
				"stage_id": self.stages_bank_1["Verified"].stage_id,
				"stage_label": "Verified",
				"current_step": 2,
				"first_name": "RPFarmer",
				"last_name": "Test",
				"phone_number": "+251911888111",
				"owner": self.farmer_user,
			}
		).insert(ignore_permissions=True)
		frappe.db.commit()

		# Rename Verified -> Field Check using sync_stages
		new_stages_payload = [
			{
				"stage_id": self.stages_bank_1["Submitted"].stage_id,
				"label": "Submitted",
				"archetype_state": "In Transition",
				"sequence": 1,
			},
			{
				"stage_id": self.stages_bank_1["Processed"].stage_id,
				"label": "Processed",
				"archetype_state": "In Transition",
				"sequence": 2,
			},
			{
				"stage_id": self.stages_bank_1["Verified"].stage_id,
				"label": "Field Check",
				"archetype_state": "In Transition",
				"sequence": 3,
			},
			{
				"stage_id": self.stages_bank_1["Approved"].stage_id,
				"label": "Approved",
				"archetype_state": "In Transition",
				"sequence": 4,
			},
			{
				"stage_id": self.stages_bank_1["Disbursed"].stage_id,
				"label": "Disbursed",
				"archetype_state": "Completed",
				"sequence": 5,
			},
			{
				"stage_id": self.stages_bank_1["Rejected"].stage_id,
				"label": "Rejected",
				"archetype_state": "Rejected",
				"sequence": 6,
			},
		]
		frappe.set_user(self.bank_admin_user)
		sync_stages(stages=new_stages_payload)
		frappe.db.commit()

		# Read application as farmer
		frappe.set_user(self.farmer_user)
		detail = get_application(application_id=app.name)
		self.assertEqual(detail["data"]["status"], "Field Check")

		# Check metadata
		meta_res = get_loan_metadata()
		status_names = [s["status"] for s in meta_res["data"]["statuses"]]
		self.assertIn("Field Check", status_names)

		# Restore original stage name for clean state
		new_stages_payload[2]["label"] = "Verified"
		frappe.set_user(self.bank_admin_user)
		sync_stages(stages=new_stages_payload)
		frappe.set_user("Administrator")
		frappe.delete_doc("A2C Loan Application", app.name, force=True)
		frappe.db.commit()

	def test_no_consumer_response_contains_in_transition(self):
		"""6. No consumer response contains the string 'In Transition' — assert over full JSON of each read endpoint."""
		import json

		from oan_a2c.api.v1.farmer.applications import get_application, list_applications
		from oan_a2c.api.v1.farmer.dashboard import get_dashboard_summary
		from oan_a2c.api.v1.loan_applications import (
			get_all_loans,
			get_full_profile,
			get_loan_metadata,
			get_loan_summary,
		)

		app_agent = frappe.get_doc(
			{
				"doctype": "A2C Loan Application",
				"application_source": "Agent",
				"farmer_profile": self.profile.name,
				"bank": self.bank_1.name,
				"loan_product": self.prod_1.name,
				"requested_amount": 1000,
				"loan_amount": 1000,
				"consent_id": self.consent.name,
				"status": "In Transition",
				"stage_id": self.stages_bank_1["Submitted"].stage_id,
				"stage_label": "Submitted",
				"current_step": 1,
				"first_name": "RPFarmer",
				"last_name": "Test",
				"phone_number": "+251911888111",
			}
		).insert(ignore_permissions=True)

		app_farmer = frappe.get_doc(
			{
				"doctype": "A2C Loan Application",
				"application_source": "Self Service",
				"farmer_profile": self.profile.name,
				"bank": self.bank_1.name,
				"loan_product": self.prod_1.name,
				"requested_amount": 1000,
				"loan_amount": 1000,
				"consent_id": self.consent.name,
				"status": "In Transition",
				"stage_id": self.stages_bank_1["Submitted"].stage_id,
				"stage_label": "Submitted",
				"current_step": 1,
				"first_name": "RPFarmer",
				"last_name": "Test",
				"phone_number": "+251911888111",
				"owner": self.farmer_user,
			}
		).insert(ignore_permissions=True)
		frappe.db.commit()

		# 1. get_all_loans
		frappe.set_user(self.dev_agent_user)
		res_all = get_all_loans()
		self.assertEqual(res_all["status"], "success")
		self.assertNotIn("In Transition", json.dumps(res_all))

		# 2. get_full_profile
		res_profile = get_full_profile(application_id=app_agent.name)
		self.assertEqual(res_profile["status"], "success")
		self.assertNotIn("In Transition", json.dumps(res_profile))

		# 3. get_loan_metadata
		res_meta = get_loan_metadata()
		self.assertEqual(res_meta["status"], "success")
		self.assertNotIn("In Transition", json.dumps(res_meta))

		# 4. get_loan_summary
		res_summary = get_loan_summary()
		self.assertEqual(res_summary["status"], "success")
		self.assertNotIn("In Transition", json.dumps(res_summary))

		# 5. Farmer list_applications
		frappe.set_user(self.farmer_user)
		res_farmer_list = list_applications()
		self.assertEqual(res_farmer_list["status"], "success")
		self.assertNotIn("In Transition", json.dumps(res_farmer_list))

		# 6. Farmer get_application
		res_farmer_app = get_application(application_id=app_farmer.name)
		self.assertEqual(res_farmer_app["status"], "success")
		self.assertNotIn("In Transition", json.dumps(res_farmer_app))

		# 7. Farmer get_dashboard_summary
		res_dash = get_dashboard_summary()
		self.assertEqual(res_dash["status"], "success")
		self.assertNotIn("In Transition", json.dumps(res_dash))

		# Clean up
		frappe.set_user("Administrator")
		frappe.delete_doc("A2C Loan Application", app_agent.name, force=True)
		frappe.delete_doc("A2C Loan Application", app_farmer.name, force=True)
		frappe.db.commit()
