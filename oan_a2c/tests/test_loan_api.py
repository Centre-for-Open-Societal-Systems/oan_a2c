import frappe
import unittest
from oan_a2c.api.v1.loan_applications import (
    get_loan_summary,
    get_all_loans,
    get_basic_profile,
    update_basic_profile,
    get_full_profile,
    get_supporting_documents,
    download_supporting_document,
    delete_supporting_document,
    update_loan_step,
    update_loan_status,
    create_loan_application
)

class TestLoansV1API(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        frappe.set_user("Administrator")
        frappe.db.sql("DELETE FROM `tabA2C Loan Application` WHERE lead_id='TEST_LEAD_999' OR first_name='API_TEST_FARMER'")
        frappe.db.sql("DELETE FROM `tabA2C Farmer Profile` WHERE lead_id='TEST_LEAD_999' OR phone_number='+251999888777'")
        frappe.db.sql("DELETE FROM `tabA2C Lead` WHERE name='TEST_LEAD_999'")
        frappe.db.sql("DELETE FROM `tabA2C Consent Request` WHERE lead='TEST_LEAD_999'")
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

        if not frappe.db.exists("A2C Consent Request", {"lead": "TEST_LEAD_999"}):
            consent = frappe.get_doc({
                "doctype": "A2C Consent Request",
                "farmer": "API_TEST_FARMER Test",
                "farmer_fayda_id": "123456789",
                "partner": "Test Partner",
                "lead": "TEST_LEAD_999",
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
                    {"field_name": "Location", "field_value": "Addis Ababa"}
                ]
            })
            consent.insert(ignore_permissions=True)
            frappe.db.set_value("A2C Farmer Profile", farmer_profile_name, "consent_id", consent.name)
            frappe.db.commit()
        
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
            # Loan is submittable; clear docstatus so a submitted (Approved/Rejected) test record
            # can be force-deleted without the cancel-first guard.
            frappe.db.sql("UPDATE `tabA2C Loan Application` SET docstatus=0 WHERE name=%s", self.app_id)
            frappe.delete_doc("A2C Loan Application", self.app_id, ignore_permissions=True, force=True)
        frappe.db.sql("DELETE FROM `tabA2C Consent Request` WHERE lead='TEST_LEAD_999'")
        
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

    def test_2_get_all_loans(self):
        res = get_all_loans(status="Draft", page_size=10)
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
        self.assertEqual(res_consent["data"]["websub_delivered_at"], "2026-06-11 13:00:00")
        self.assertEqual(res_consent["data"]["consent_type"], "Personal Data Sharing")
        self.assertEqual(res_consent["data"]["purpose"], "Loan Credit Risk Analysis")
        self.assertEqual(res_consent["data"]["validity_from"], "2026-06-11")
        self.assertEqual(res_consent["data"]["validity_to"], "2027-06-11")
        self.assertIn("requested_data_fields", res_consent["data"])
        self.assertEqual(len(res_consent["data"]["requested_data_fields"]), 2)
        fields_dict = {f["field_name"]: f["field_value"] for f in res_consent["data"]["requested_data_fields"]}
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
            lead = frappe.get_doc({
                "doctype": "A2C Lead",
                "phone_number": "+251999888111",
                "lead_source": "Agent Entry",
                "status": "Active"
            })
            lead.insert(ignore_permissions=True)
            frappe.db.sql("UPDATE `tabA2C Lead` SET name=%s WHERE name=%s", (lead_name, lead.name))
            frappe.db.commit()

        # Ensure no farmer profile is linked
        frappe.db.set_value("A2C Lead", lead_name, "farmer_profile", None)
        # Delete any existing consent requests for this test lead
        frappe.db.sql("DELETE FROM `tabA2C Consent Request` WHERE lead=%s", (lead_name,))
        frappe.db.commit()

        # 2. Call get_basic_profile - should return 400 ValidationError response
        res_error = get_basic_profile(lead_id=lead_name)
        self.assertEqual(frappe.local.response.get("http_status_code"), 400)
        self.assertEqual(res_error.get("status"), "error")
        self.assertEqual(res_error.get("code"), "VALIDATION_ERROR")
        self.assertIn("Farmer Profile not found", res_error.get("message"))
        frappe.local.response["http_status_code"] = 200

        # 3. Create a pending consent request linked to this lead
        consent = frappe.get_doc({
            "doctype": "A2C Consent Request",
            "farmer": "Pending Farmer",
            "farmer_fayda_id": "987654321",
            "partner": "Test Partner",
            "lead": lead_name,
            "status": "Pending OTP"
        })
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
        self.assertEqual(res["data"]["loan_amount"], 5000.0)
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
        self.assertEqual(res_del["message"], "File deleted successfully")

        # 3. Check if file is actually deleted
        self.assertFalse(frappe.db.exists("File", file_id))

        # 4. Get again, should be empty
        res_after = get_supporting_documents(application_id=self.app_id)
        self.assertEqual(res_after["status"], "success")
        self.assertEqual(len(res_after["data"]), 0)

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
        res = update_loan_status(application_id=self.app_id, status="Processing")
        self.assertEqual(res["status"], "success")
        res = update_loan_status(application_id=self.app_id, status="Rejected")
        self.assertEqual(res["status"], "success")

        doc = frappe.get_doc("A2C Loan Application", self.app_id)
        self.assertEqual(doc.status, "Rejected")
        self.assertEqual(doc.docstatus, 1)

        # A further transition (e.g. to Approved) is illegal from a terminal state and rejected.
        res = update_loan_status(application_id=self.app_id, status="Approved")
        self.assertEqual(res["status"], "error")

        # The submitted record is frozen: a direct edit + save is blocked by docstatus.
        doc.status = "Approved"
        self.assertRaises(frappe.ValidationError, doc.save)

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
        
        # 3. Create Credit Info
        credit_info = frappe.get_doc({
            "doctype": "A2C Credit Information",
            "lead": "TEST_LEAD_999",
            "loan_type": "Input loan (seeds, agrochemicals)",
            "loan_amount": 12000,
            "purpose_message": "Test loan purpose"
        })
        credit_info.insert(ignore_permissions=True)
        frappe.db.commit()
        
        # 4. Call create_loan_application API
        res = create_loan_application(lead_id="TEST_LEAD_999")
        self.assertEqual(res["status"], "success")
        app_id = res["data"]["application_id"]

        # Loan creation does NOT change the lead status (that is driven via the Lead Workflow
        # by the frontend through update_lead_status). The new loan starts in Draft.
        loan_status = frappe.db.get_value("A2C Loan Application", app_id, "status")
        self.assertEqual(loan_status, "Draft")
        
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
