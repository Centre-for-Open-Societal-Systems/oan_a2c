import frappe
import unittest
from oan_a2c.api.v1.webhooks import lead_inbound


class TestA2CLead(unittest.TestCase):
	"""
	Tests for the A2C Lead DocType controller and the lead_inbound webhook.
	"""

	TEST_PHONE = "+251911000001"
	TEST_PHONE_2 = "+251911000002"

	@classmethod
	def setUpClass(cls):
		frappe.set_user("Administrator")

	@classmethod
	def tearDownClass(cls):
		frappe.set_user("Administrator")
		for name in frappe.get_all(
			"A2C Lead",
			filters={"phone_number": ("in", [cls.TEST_PHONE, cls.TEST_PHONE_2])},
			pluck="name",
		):
			frappe.delete_doc("A2C Lead", name, ignore_permissions=True, force=True)
		frappe.db.commit()

	def setUp(self):
		frappe.set_user("Administrator")
		for name in frappe.get_all(
			"A2C Lead",
			filters={"phone_number": ("in", [self.TEST_PHONE, self.TEST_PHONE_2])},
			pluck="name",
		):
			frappe.delete_doc("A2C Lead", name, ignore_permissions=True, force=True)
		frappe.db.commit()

	# ------------------------------------------------------------------
	# DocType controller tests
	# ------------------------------------------------------------------

	def test_1_create_lead_success(self):
		"""A valid lead should be created with an auto-generated LEAD-YYYY-##### name."""
		lead = frappe.new_doc("A2C Lead")
		lead.phone_number = self.TEST_PHONE
		lead.lead_source = "Missed Call"
		lead.status = "Active"
		lead.insert()

		self.assertTrue(lead.name.startswith("LEAD-"))
		self.assertEqual(lead.status, "Active")

	def test_2_duplicate_active_lead_blocked(self):
		"""Two active leads for the same phone number must be rejected."""
		lead = frappe.new_doc("A2C Lead")
		lead.phone_number = self.TEST_PHONE
		lead.lead_source = "Missed Call"
		lead.status = "Active"
		lead.insert()

		duplicate = frappe.new_doc("A2C Lead")
		duplicate.phone_number = self.TEST_PHONE
		duplicate.lead_source = "IVR"
		duplicate.status = "Active"

		with self.assertRaises(frappe.DuplicateEntryError):
			duplicate.insert()

	def test_3_processed_lead_allows_new_lead_same_phone(self):
		"""A new lead for the same phone is allowed when the prior lead is Processed."""
		lead = frappe.new_doc("A2C Lead")
		lead.phone_number = self.TEST_PHONE
		lead.lead_source = "Missed Call"
		lead.status = "Processed"
		lead.insert()

		new_lead = frappe.new_doc("A2C Lead")
		new_lead.phone_number = self.TEST_PHONE
		new_lead.lead_source = "Agent Entry"
		new_lead.status = "Active"
		new_lead.insert()

		self.assertTrue(new_lead.name.startswith("LEAD-"))

		# Verify that updating/saving the Processed lead raises ValidationError
		lead.email = "updated_processed_email@example.com"
		with self.assertRaises(frappe.ValidationError):
			lead.save()


	# ------------------------------------------------------------------
	# Webhook tests
	# ------------------------------------------------------------------

	def test_4_webhook_creates_new_lead(self):
		"""A first-time inbound call should create a new Open lead."""
		response = lead_inbound(
			phone_number=self.TEST_PHONE,
			lead_source="Missed Call",
			external_ref_id="TELCO-001",
			timestamp="2026-05-19T10:00:00Z",
		)

		self.assertEqual(response["status"], "success")
		self.assertTrue(response["data"]["lead_id"].startswith("LEAD-"))

		lead = frappe.get_doc("A2C Lead", response["data"]["lead_id"])
		self.assertEqual(lead.phone_number, self.TEST_PHONE)
		self.assertEqual(lead.status, "Active")
		self.assertIn("TELCO-001", lead.call_notes)

	def test_5_webhook_idempotent_on_duplicate_call(self):
		"""A second call for the same active phone must update, not duplicate."""
		r1 = lead_inbound(
			phone_number=self.TEST_PHONE,
			lead_source="Missed Call",
			external_ref_id="TELCO-001",
			timestamp="2026-05-19T10:00:00Z",
		)
		lead_id = r1["data"]["lead_id"]

		r2 = lead_inbound(
			phone_number=self.TEST_PHONE,
			lead_source="IVR",
			external_ref_id="TELCO-002",
			timestamp="2026-05-19T10:05:00Z",
		)

		self.assertEqual(r2["data"]["lead_id"], lead_id)
		self.assertIn("Existing active lead updated", r2["message"])

		notes = frappe.db.get_value("A2C Lead", lead_id, "call_notes")
		self.assertIn("TELCO-001", notes)
		self.assertIn("TELCO-002", notes)

		count = frappe.db.count("A2C Lead", {"phone_number": self.TEST_PHONE})
		self.assertEqual(count, 1)

	def test_6_webhook_missing_phone_raises(self):
		"""Webhook must reject requests with no phone_number."""
		res = lead_inbound(phone_number="")
		self.assertEqual(res["status"], "error")
		self.assertEqual(res["code"], "VALIDATION_ERROR")

	def test_7_external_id_uniqueness_enforced(self):
		"""Programmatic external_id uniqueness check is enforced when populated."""
		lead1 = frappe.new_doc("A2C Lead")
		lead1.phone_number = self.TEST_PHONE
		lead1.external_id = "EXT-UNIQUE-123"
		lead1.insert()

		lead2 = frappe.new_doc("A2C Lead")
		lead2.phone_number = self.TEST_PHONE_2
		lead2.external_id = "EXT-UNIQUE-123"

		with self.assertRaises(frappe.DuplicateEntryError):
			lead2.insert()

	def test_8_blank_external_ids_allowed(self):
		"""Multiple leads with empty/blank external_id are allowed."""
		lead1 = frappe.new_doc("A2C Lead")
		lead1.phone_number = self.TEST_PHONE
		lead1.external_id = ""
		lead1.insert()

		lead2 = frappe.new_doc("A2C Lead")
		lead2.phone_number = self.TEST_PHONE_2
		lead2.external_id = ""
		lead2.insert()  # Should not raise any DuplicateEntryError

		self.assertTrue(lead1.name != lead2.name)


class TestLeadListAPI(unittest.TestCase):
	"""Tests for the paginated search and filter leads list API endpoint."""

	@classmethod
	def setUpClass(cls):
		frappe.set_user("Administrator")
		# Clear existing testing records to ensure a deterministic state
		cls._clear_records()

		# Insert 5 test leads with varying parameters to run queries against
		cls.leads = []
		lead_data = [
			{
				"phone_number": "+251922000001",
				"external_id": "TEL-LIST-001",
				"lead_source": "Missed Call",
				"status": "Active",
			},
			{
				"phone_number": "+251922000002",
				"external_id": "TEL-LIST-002",
				"lead_source": "IVR",
				"status": "Verified",
			},
			{
				"phone_number": "+251922000003",
				"external_id": "TEL-LIST-003",
				"lead_source": "SMS",
				"status": "Granted",
			},
			{
				"phone_number": "+251922000004",
				"external_id": "",
				"lead_source": "Agent Entry",
				"status": "Processed",
			},
			{
				"phone_number": "+251922000005",
				"external_id": "TEL-LIST-005",
				"lead_source": "Missed Call",
				"status": "Processed",
			},
		]
		for data in lead_data:
			doc = frappe.new_doc("A2C Lead")
			doc.phone_number = data["phone_number"]
			doc.external_id = data["external_id"]
			doc.lead_source = data["lead_source"]
			doc.status = data["status"]
			doc.insert(ignore_permissions=True)
			cls.leads.append(doc.name)
		frappe.db.commit()

	@classmethod
	def tearDownClass(cls):
		frappe.set_user("Administrator")
		cls._clear_records()
		frappe.db.commit()

	@classmethod
	def _clear_records(cls):
		for name in frappe.get_all(
			"A2C Lead",
			pluck="name",
		):
			frappe.delete_doc("A2C Lead", name, ignore_permissions=True, force=True)

	def test_get_leads_pagination(self):
		"""Verifies list pagination slice parameters start and page_length work."""
		from oan_a2c.api.v1.leads import get_leads
		res = get_leads(start=0, page_length=2, search_query="+251922000")
		self.assertEqual(res["status"], "success")
		self.assertEqual(res["pagination"]["limit"], 2)
		self.assertEqual(res["pagination"]["total"], 5)
		self.assertEqual(len(res["data"]), 2)

		# Fetch next page
		res_page_2 = get_leads(start=2, page_length=2, search_query="+251922000")
		self.assertEqual(res_page_2["pagination"]["page"], 2)
		self.assertEqual(len(res_page_2["data"]), 2)

	def test_get_leads_search(self):
		"""Verifies searching by phone number, Lead ID, and external ID works."""
		from oan_a2c.api.v1.leads import get_leads
		
		# Search by Phone Number
		res_phone = get_leads(search_query="+251922000003")
		self.assertEqual(res_phone["pagination"]["total"], 1)
		self.assertEqual(res_phone["data"][0]["phone_number"], "+251922000003")

		# Search by External ID
		res_ext = get_leads(search_query="TEL-LIST-005")
		self.assertEqual(res_ext["pagination"]["total"], 1)
		self.assertEqual(res_ext["data"][0]["external_id"], "TEL-LIST-005")

		# Search by Lead ID (name)
		lead_name = self.leads[0]
		res_name = get_leads(search_query=lead_name)
		self.assertEqual(res_name["pagination"]["total"], 1)
		self.assertEqual(res_name["data"][0]["name"], lead_name)

	def test_get_leads_filters(self):
		"""Verifies applying status and lead_source filters works."""
		from oan_a2c.api.v1.leads import get_leads
		
		# Filter by Granted status (new status)
		res_status = get_leads(status="Granted", search_query="+251922000")
		self.assertEqual(res_status["pagination"]["total"], 1)
		self.assertEqual(res_status["data"][0]["status"], "Granted")

		# Filter by Lead Source
		res_source = get_leads(lead_source="Missed Call", search_query="+251922000")
		self.assertEqual(res_source["pagination"]["total"], 2)
		for lead in res_source["data"]:
			self.assertEqual(lead["lead_source"], "Missed Call")

	def test_get_leads_assigned_to_filter(self):
		"""Verifies filtering by assignee, multi-agent, and the 'unassigned' sentinel."""
		from oan_a2c.api.v1.leads import get_leads

		# Assign two of the five list leads to Administrator, leave the rest unassigned.
		frappe.db.set_value("A2C Lead", self.leads[0], "assigned_to", "Administrator")
		frappe.db.set_value("A2C Lead", self.leads[1], "assigned_to", "Administrator")
		frappe.db.commit()

		# Filter by a single agent
		res = get_leads(assigned_to="Administrator", search_query="+251922000")
		self.assertEqual(res["pagination"]["total"], 2)
		for lead in res["data"]:
			self.assertEqual(lead["assigned_to"], "Administrator")

		# Filter for unassigned leads (the other three)
		res_unassigned = get_leads(assigned_to="unassigned", search_query="+251922000")
		self.assertEqual(res_unassigned["pagination"]["total"], 3)
		for lead in res_unassigned["data"]:
			self.assertFalse(lead["assigned_to"])

	def test_get_leads_invalid_filters_throw(self):
		"""Verifies that passing invalid status or lead_source values returns a validation error."""
		from oan_a2c.api.v1.leads import get_leads
		
		# Test invalid status
		frappe.local.response = frappe._dict({"http_status_code": 200})
		res = get_leads(status="InvalidStatus")
		self.assertEqual(res.get("status"), "error")
		self.assertEqual(frappe.local.response.get("http_status_code"), 400)
		self.assertIn("Invalid value 'InvalidStatus'", res.get("message"))
			
		# Test invalid lead_source
		frappe.local.response = frappe._dict({"http_status_code": 200})
		res2 = get_leads(lead_source="InvalidSource")
		self.assertEqual(res2.get("status"), "error")
		self.assertEqual(frappe.local.response.get("http_status_code"), 400)
		self.assertIn("Invalid value 'InvalidSource'", res2.get("message"))
		
		# Reset response code
		frappe.local.response["http_status_code"] = 200

	def test_get_lead_summary(self):
		"""Verifies that get_lead_summary correctly returns counts of all leads by status."""
		from oan_a2c.api.v1.leads import get_lead_summary
		res = get_lead_summary()
		
		self.assertEqual(res["status"], "success")
		self.assertEqual(res["data"]["total"], 5)
		
		by_status = res["data"]["by_status"]
		self.assertEqual(by_status["Active"], 1)
		self.assertEqual(by_status["Verified"], 1)
		self.assertEqual(by_status["Granted"], 1)
		self.assertEqual(by_status["Rejected"], 0)
		self.assertEqual(by_status["Processed"], 2)

	def test_get_lead_metadata(self):
		"""Verifies get_lead_metadata dynamically parses Select field options."""
		from oan_a2c.api.v1.leads import get_lead_metadata
		res = get_lead_metadata()
		self.assertEqual(res["status"], "success")
		self.assertIn("Active", res["data"]["statuses"])
		self.assertIn("Verified", res["data"]["statuses"])
		self.assertIn("Missed Call", res["data"]["sources"])
		self.assertIn("Agent Entry", res["data"]["sources"])

	def test_comments_and_timeline_workflow(self):
		"""Verifies that adding a comment works and is correctly reflected on the lead's timeline."""
		from oan_a2c.api.v1.leads import add_lead_comment, get_lead_timeline
		
		target_lead = self.leads[0]
		
		# 1. Add comment
		res_add = add_lead_comment(lead_id=target_lead, content="Test comment from field officer.")
		self.assertEqual(res_add["status"], "success")
		self.assertTrue(res_add["data"]["comment_id"])

		# 2. Verify in timeline
		res_timeline = get_lead_timeline(lead_id=target_lead)
		self.assertEqual(res_timeline["status"], "success")
		self.assertEqual(res_timeline["data"]["lead_id"], target_lead)
		self.assertTrue(len(res_timeline["data"]["timeline"]) >= 1)
		
		found_comment = next((c for c in res_timeline["data"]["timeline"] if c["name"] == res_add["data"]["comment_id"]), None)
		self.assertIsNotNone(found_comment)
		self.assertEqual(found_comment["event_description"], "Test comment from field officer.")

	def test_get_lead_call_logs(self):
		"""Verifies that call notes are correctly retrieved and parsed into structured call logs."""
		from oan_a2c.api.v1.leads import get_lead_call_logs
		
		target_lead = self.leads[0]
		
		# Set raw call notes to simulate multiple calls recorded by webhooks
		frappe.db.set_value(
			"A2C Lead", 
			target_lead, 
			"call_notes", 
			"Source: IVR | Ref ID: REF-IVR-101 | Timestamp: 2026-05-27T10:00:00Z\n\nSource: Missed Call | Ref ID: REF-MC-202 | Timestamp: 2026-05-27T10:05:00Z"
		)
		frappe.db.commit()

		res = get_lead_call_logs(lead_id=target_lead)
		self.assertEqual(res["status"], "success")
		self.assertEqual(res["data"]["lead_id"], target_lead)
		
		call_logs = res["data"]["call_logs"]
		self.assertEqual(len(call_logs), 2)
		
		first_call = call_logs[0]
		self.assertEqual(first_call["source"], "IVR")
		self.assertEqual(first_call["ref_id"], "REF-IVR-101")
		self.assertEqual(first_call["timestamp"], "2026-05-27T10:00:00Z")
		
		second_call = call_logs[1]
		self.assertEqual(second_call["source"], "Missed Call")
		self.assertEqual(second_call["ref_id"], "REF-MC-202")
		self.assertEqual(second_call["timestamp"], "2026-05-27T10:05:00Z")





class TestLeadCreationAPI(unittest.TestCase):
	"""Tests for the native lead creation API endpoint."""

	TEST_PHONE = "+251933000001"
	TEST_PHONE_2 = "+251933000002"

	@classmethod
	def setUpClass(cls):
		frappe.set_user("Administrator")
		cls._clear_records()

	@classmethod
	def tearDownClass(cls):
		frappe.set_user("Administrator")
		cls._clear_records()
		frappe.db.commit()

	@classmethod
	def _clear_records(cls):
		for name in frappe.get_all(
			"A2C Lead",
			filters={"phone_number": ("in", [cls.TEST_PHONE, cls.TEST_PHONE_2])},
			pluck="name",
		):
			frappe.delete_doc("A2C Lead", name, ignore_permissions=True, force=True)

	def setUp(self):
		self._clear_records()
		frappe.db.commit()

	def test_create_lead_api_success(self):
		"""Verifies that create_lead API saves first_name, last_name, and email successfully."""
		from oan_a2c.api.v1.leads import create_lead
		res = create_lead(
			phone_number=self.TEST_PHONE,
			first_name="Abebe",
			last_name="Bikila",
			email="abebe@coopbank.com",
			lead_source="Agent Entry",
			external_id="EXT-API-999"
		)
		self.assertEqual(res["status"], "success")
		self.assertTrue(res["data"]["lead_id"].startswith("LEAD-"))

		lead = frappe.get_doc("A2C Lead", res["data"]["lead_id"])
		self.assertEqual(lead.phone_number, self.TEST_PHONE)
		self.assertEqual(lead.first_name, "Abebe")
		self.assertEqual(lead.last_name, "Bikila")
		self.assertEqual(lead.email, "abebe@coopbank.com")
		self.assertEqual(lead.lead_source, "Agent Entry")
		self.assertEqual(lead.external_id, "EXT-API-999")

	def test_create_lead_api_invalid_email(self):
		"""Verifies that an invalid email address raises a ValidationError."""
		from oan_a2c.api.v1.leads import create_lead
		res = create_lead(
			phone_number=self.TEST_PHONE,
			first_name="Abebe",
			last_name="Bikila",
			email="invalid-email-format"
		)
		self.assertEqual(res["status"], "error")
		self.assertEqual(res["code"], "VALIDATION_ERROR")

	def test_create_lead_api_duplicate(self):
		"""Verifies that duplicate lead validation is triggered via the API creation flow."""
		from oan_a2c.api.v1.leads import create_lead
		r1 = create_lead(
			phone_number=self.TEST_PHONE,
			first_name="First",
			last_name="Last"
		)
		self.assertEqual(r1["status"], "success")
		
		# Attempting to create a duplicate active lead
		r2 = create_lead(
			phone_number=self.TEST_PHONE,
			first_name="Second",
			last_name="Last"
		)
		self.assertEqual(r2["status"], "error")
		self.assertEqual(r2["code"], "VALIDATION_ERROR")


class TestVisitScheduleAPI(unittest.TestCase):
	"""Tests for A2C Lead Visit Scheduling REST API endpoints."""

	@classmethod
	def setUpClass(cls):
		frappe.set_user("Administrator")
		cls._clear_records()

		# Insert a test lead to schedule visits for
		cls.lead = frappe.new_doc("A2C Lead")
		cls.lead.phone_number = "+251955000001"
		cls.lead.lead_source = "Agent Entry"
		cls.lead.status = "Active"
		cls.lead.insert(ignore_permissions=True)
		cls.lead_id = cls.lead.name
		frappe.db.commit()

	@classmethod
	def tearDownClass(cls):
		frappe.set_user("Administrator")
		cls._clear_records()
		frappe.db.commit()

	@classmethod
	def _clear_records(cls):
		# Delete all visit schedules
		for name in frappe.get_all("A2C Visit Schedule", pluck="name"):
			frappe.delete_doc("A2C Visit Schedule", name, ignore_permissions=True, force=True)
		# Delete our specific testing lead
		for name in frappe.get_all("A2C Lead", filters={"phone_number": "+251955000001"}, pluck="name"):
			frappe.delete_doc("A2C Lead", name, ignore_permissions=True, force=True)

	def setUp(self):
		frappe.set_user("Administrator")
		# Reset lead status to Active
		frappe.db.set_value("A2C Lead", self.lead_id, "status", "Active")
		# Delete all schedules before each test
		for name in frappe.get_all("A2C Visit Schedule", pluck="name"):
			frappe.delete_doc("A2C Visit Schedule", name, ignore_permissions=True, force=True)
		frappe.db.commit()

	def test_schedule_visit_success(self):
		"""Verifies that schedule_visit successfully creates a visit schedule, promotes lead status, and logs timeline."""
		from oan_a2c.api.v1.leads import schedule_visit
		
		res = schedule_visit(
			lead_id=self.lead_id,
			visit_date="2026-06-10",
			visit_time="14:30:00",
			region="Oromia",
			zone="East Shewa",
			woreda="Ada'ama",
			kebele="Kebele 02",
			meeting_location="Cooperative Office",
			notes="Bring farm certificates"
		)

		self.assertEqual(res["status"], "success")
		self.assertTrue(res["data"]["schedule_id"])

		schedule_id = res["data"]["schedule_id"]

		# Verify DB record values
		schedule = frappe.get_doc("A2C Visit Schedule", schedule_id)
		self.assertEqual(schedule.lead, self.lead_id)
		self.assertEqual(str(schedule.visit_date), "2026-06-10")
		self.assertEqual(schedule.region, "Oromia")
		self.assertEqual(schedule.zone, "East Shewa")
		self.assertEqual(schedule.woreda, "Ada'ama")
		self.assertEqual(schedule.kebele, "Kebele 02")
		self.assertEqual(schedule.status, "Scheduled")

		# Verify Lead status remains Active after scheduling (since the visit is only Scheduled, not Completed yet)
		lead_status = frappe.db.get_value("A2C Lead", self.lead_id, "status")
		self.assertEqual(lead_status, "Active")

		# Promote the visit schedule status to Completed
		from oan_a2c.api.v1.leads import update_visit_schedule_status
		update_visit_schedule_status(schedule_id=schedule_id, status="Completed")

		# Verify Lead status remains Active (since promotion is manual in the UI)
		lead_status = frappe.db.get_value("A2C Lead", self.lead_id, "status")
		self.assertEqual(lead_status, "Active")

		# Manually promote Lead status to Verified (simulating manual UI action)
		from oan_a2c.api.v1.leads import update_lead_status
		update_lead_status(lead_id=self.lead_id, status="Verified")

		# Verify Lead status is now Verified
		lead_status = frappe.db.get_value("A2C Lead", self.lead_id, "status")
		self.assertEqual(lead_status, "Verified")

		# Verify system timeline comment is created
		comments = frappe.get_all(
			"A2C Lead Audit Event",
			filters={"lead": self.lead_id},
			fields=["event_description"]
		)
		self.assertTrue(any("Visit scheduled for 2026-06-10" in c["event_description"] for c in comments))

	def test_get_visit_schedules_filtering(self):
		"""Verifies filtering and pagination of get_visit_schedules API."""
		from oan_a2c.api.v1.leads import schedule_visit, get_visit_schedules

		# Create two schedules
		schedule_visit(
			lead_id=self.lead_id,
			visit_date="2026-06-10",
			visit_time="10:00:00",
			region="Oromia",
			zone="East Shewa",
			woreda="Ada'ama",
			kebele="01"
		)
		schedule_visit(
			lead_id=self.lead_id,
			visit_date="2026-06-11",
			visit_time="15:00:00",
			region="Oromia",
			zone="East Shewa",
			woreda="Ada'ama",
			kebele="02"
		)

		# Fetch all schedules
		res = get_visit_schedules(lead_id=self.lead_id)
		self.assertEqual(res["status"], "success")
		self.assertEqual(res["pagination"]["total"], 2)
		self.assertEqual(len(res["data"]), 2)

		# Fetch with date filter
		res_filtered = get_visit_schedules(lead_id=self.lead_id, start_date="2026-06-11")
		self.assertEqual(res_filtered["pagination"]["total"], 1)
		self.assertEqual(str(res_filtered["data"][0]["visit_date"]), "2026-06-11")

	def test_visit_status_transitions(self):
		"""Verifies that transitioning from Missed or Completed status is blocked, but Cancelled is allowed."""
		from oan_a2c.api.v1.leads import schedule_visit, update_visit_schedule_status

		# 1. Create a visit (defaults to Scheduled)
		res = schedule_visit(
			lead_id=self.lead_id,
			visit_date="2026-06-10",
			visit_time="10:00:00",
			region="Oromia",
			zone="East Shewa",
			woreda="Ada'ama",
			kebele="01"
		)
		schedule_id = res["data"]["schedule_id"]

		# 2. Transition from Scheduled to Cancelled (should be allowed)
		update_visit_schedule_status(schedule_id=schedule_id, status="Cancelled")
		self.assertEqual(frappe.db.get_value("A2C Visit Schedule", schedule_id, "status"), "Cancelled")

		# 3. Transition from Cancelled back to Scheduled (should be allowed)
		update_visit_schedule_status(schedule_id=schedule_id, status="Scheduled")
		self.assertEqual(frappe.db.get_value("A2C Visit Schedule", schedule_id, "status"), "Scheduled")

		# 4. Transition from Scheduled to Completed (should be allowed)
		update_visit_schedule_status(schedule_id=schedule_id, status="Completed")
		self.assertEqual(frappe.db.get_value("A2C Visit Schedule", schedule_id, "status"), "Completed")

		# 5. Attempting to transition from Completed to Cancelled (should be blocked)
		res_cancel = update_visit_schedule_status(schedule_id=schedule_id, status="Cancelled")
		self.assertEqual(res_cancel["status"], "error")
		self.assertEqual(res_cancel["code"], "VALIDATION_ERROR")

		# 6. Create another visit to test Missed
		res2 = schedule_visit(
			lead_id=self.lead_id,
			visit_date="2026-06-10",
			visit_time="10:00:00",
			region="Oromia",
			zone="East Shewa",
			woreda="Ada'ama",
			kebele="01"
		)
		schedule_id_2 = res2["data"]["schedule_id"]

		# 7. Transition from Scheduled to Missed (should be allowed)
		update_visit_schedule_status(schedule_id=schedule_id_2, status="Missed")
		self.assertEqual(frappe.db.get_value("A2C Visit Schedule", schedule_id_2, "status"), "Missed")

		# 8. Attempting to transition from Missed to Scheduled (should be blocked)
		res_missed = update_visit_schedule_status(schedule_id=schedule_id_2, status="Scheduled")
		self.assertEqual(res_missed["status"], "error")
		self.assertEqual(res_missed["code"], "VALIDATION_ERROR")


class TestLeadStatusUpdateAPI(unittest.TestCase):
	"""Tests for A2C Lead status updates and transition locking API endpoint."""

	@classmethod
	def setUpClass(cls):
		frappe.set_user("Administrator")
		cls._clear_records()

		# Insert a test lead for updating statuses
		cls.lead = frappe.new_doc("A2C Lead")
		cls.lead.phone_number = "+251966000001"
		cls.lead.lead_source = "Agent Entry"
		cls.lead.status = "Active"
		cls.lead.insert(ignore_permissions=True)
		cls.lead_id = cls.lead.name
		frappe.db.commit()

	@classmethod
	def tearDownClass(cls):
		frappe.set_user("Administrator")
		cls._clear_records()
		frappe.db.commit()

	@classmethod
	def _clear_records(cls):
		for name in frappe.get_all("A2C Lead", filters={"phone_number": "+251966000001"}, pluck="name"):
			frappe.delete_doc("A2C Lead", name, ignore_permissions=True, force=True)

	def setUp(self):
		frappe.set_user("Administrator")
		frappe.db.set_value("A2C Lead", self.lead_id, "status", "Active")
		# Delete comments for this lead before each test
		comments = frappe.get_all("A2C Lead Audit Event", filters={"lead": self.lead_id}, pluck="name")
		for comment in comments:
			frappe.delete_doc("A2C Lead Audit Event", comment, ignore_permissions=True, force=True)
		frappe.db.commit()

	def test_1_update_status_success(self):
		"""Verifies that update_lead_status successfully updates status and records the reason as a timeline comment."""
		from oan_a2c.api.v1.leads import update_lead_status

		res = update_lead_status(
			lead_id=self.lead_id,
			status="Verified",
			reason="Conducted discovery call and verified information."
		)

		self.assertEqual(res["status"], "success")
		self.assertEqual(res["data"]["new_status"], "Verified")

		# Check DB
		current_status = frappe.db.get_value("A2C Lead", self.lead_id, "status")
		self.assertEqual(current_status, "Verified")

		# Check timeline comment
		comments = frappe.get_all(
			"A2C Lead Audit Event",
			filters={"lead": self.lead_id},
			fields=["event_description"]
		)
		self.assertEqual(len(comments), 1)
		self.assertIn("Changed to Verified", comments[0]["event_description"])
		self.assertIn("Conducted discovery call and verified information.", comments[0]["event_description"])
		self.assertIn("Administrator", comments[0]["event_description"])

	def test_2_invalid_status_name_throws(self):
		"""Verifies update_lead_status rejects target statuses not defined in the Select choices."""
		from oan_a2c.api.v1.leads import update_lead_status

		res = update_lead_status(
			lead_id=self.lead_id,
			status="InvalidOutcomeStatusName"
		)
		self.assertEqual(res["status"], "error")
		self.assertEqual(res["code"], "VALIDATION_ERROR")

	def test_3_update_status_locked_when_terminal(self):
		"""Verifies update_lead_status blocks modifications when a lead is in a locked/terminal state."""
		from oan_a2c.api.v1.leads import update_lead_status

		# 1. Promote to Processed (Terminal)
		update_lead_status(
			lead_id=self.lead_id,
			status="Processed",
			reason="Processing lead to loan application."
		)

		# 2. Attempting to change status again must raise a ValidationError
		res = update_lead_status(
			lead_id=self.lead_id,
			status="Active",
			reason="Try to make it active again"
		)
		self.assertEqual(res["status"], "error")
		self.assertEqual(res["code"], "VALIDATION_ERROR")

	def test_4_rejected_lead_status_locked(self):
		"""Verifies that once lead status is set to Rejected, saving any further status update throws ValidationError."""
		# Reset lead status to Active, save it, then set to Rejected
		frappe.db.set_value("A2C Lead", self.lead_id, "status", "Active")
		frappe.db.commit()

		lead = frappe.get_doc("A2C Lead", self.lead_id)
		lead.status = "Rejected"
		lead.save()

		# Try to change status to Active and save, should raise ValidationError
		lead = frappe.get_doc("A2C Lead", self.lead_id)
		lead.status = "Active"
		self.assertRaises(frappe.ValidationError, lead.save)



class TestLeadAssignmentAPI(unittest.TestCase):
	"""Tests for Lead Assignment, Date stamp, and User search lookup APIs."""

	@classmethod
	def setUpClass(cls):
		frappe.set_user("Administrator")
		cls._clear_records()

		# Create a test Lead for assignment
		cls.lead = frappe.new_doc("A2C Lead")
		cls.lead.phone_number = "+251966000002"
		cls.lead.lead_source = "Agent Entry"
		cls.lead.status = "Active"
		cls.lead.insert(ignore_permissions=True)
		cls.lead_id = cls.lead.name
		frappe.db.commit()

	@classmethod
	def tearDownClass(cls):
		frappe.set_user("Administrator")
		cls._clear_records()
		frappe.db.commit()

	@classmethod
	def _clear_records(cls):
		for name in frappe.get_all("A2C Lead", filters={"phone_number": "+251966000002"}, pluck="name"):
			frappe.delete_doc("A2C Lead", name, ignore_permissions=True, force=True)

	def setUp(self):
		frappe.set_user("Administrator")
		frappe.db.set_value("A2C Lead", self.lead_id, "assigned_to", None)
		frappe.db.set_value("A2C Lead", self.lead_id, "assigned_date", None)
		# Delete comments for this lead before each test
		comments = frappe.get_all("A2C Lead Audit Event", filters={"lead": self.lead_id}, pluck="name")
		for comment in comments:
			frappe.delete_doc("A2C Lead Audit Event", comment, ignore_permissions=True, force=True)
		frappe.db.commit()

	def test_1_get_assignable_users(self):
		"""Verifies that get_assignable_users returns users with appropriate roles."""
		from oan_a2c.api.v1.leads import get_assignable_users
		# Verify that we can run the query and check formatting keys are present
		res = get_assignable_users()
		self.assertEqual(res["status"], "success")
		self.assertTrue(isinstance(res["data"], list))

		# Create a dummy user with a role if none exists to ensure tests pass in clean environments
		if not res["data"]:
			# Ensure Development Agent role exists
			if not frappe.db.exists("Role", "Development Agent"):
				role = frappe.new_doc("Role")
				role.role_name = "Development Agent"
				role.insert(ignore_permissions=True)

			dummy_username = "test_agent_assignee"
			dummy_email = "test_agent_assignee@coopbank.com"
			if not frappe.db.exists("User", dummy_email):
				user = frappe.new_doc("User")
				user.email = dummy_email
				user.first_name = "Test Assignee Agent"
				user.username = dummy_username
				user.location = "Oromia"
				user.insert(ignore_permissions=True)
				user.add_roles("Development Agent")
				frappe.db.commit()

			res = get_assignable_users()
			self.assertTrue(len(res["data"]) >= 1)

		first_user = res["data"][0]
		self.assertTrue("email" in first_user)
		self.assertTrue("full_name" in first_user)
		self.assertTrue("agent_id" in first_user)
		self.assertTrue("region" in first_user)

	def test_2_assign_lead_success(self):
		"""Verifies that lead assignment updates properties and creates timeline logs."""
		from oan_a2c.api.v1.leads import assign_lead, get_leads
		from frappe.utils import today

		res = assign_lead(
			lead_id=self.lead_id,
			assigned_to="Administrator"
		)

		self.assertEqual(res["status"], "success")
		self.assertEqual(res["data"]["assigned_to"], "Administrator")
		self.assertEqual(res["data"]["assigned_date"], today())

		# Check DB
		lead = frappe.get_doc("A2C Lead", self.lead_id)
		self.assertEqual(lead.assigned_to, "Administrator")
		self.assertEqual(str(lead.assigned_date), today())

		# Check comment timeline log
		comments = frappe.get_all(
			"A2C Lead Audit Event",
			filters={"lead": self.lead_id},
			fields=["event_description"]
		)
		self.assertTrue(any("Assigned to" in c["event_description"] for c in comments))

		# Check that get_leads returns the assigned_date field
		list_res = get_leads(search_query=self.lead_id)
		self.assertEqual(list_res["pagination"]["total"], 1)
		self.assertEqual(str(list_res["data"][0]["assigned_date"]), today())

	def test_3_assign_lead_nonexistent_user_throws(self):
		"""Verifies assign_lead blocks assignment to nonexistent user."""
		from oan_a2c.api.v1.leads import assign_lead
		res = assign_lead(
			lead_id=self.lead_id,
			assigned_to="nonexistent_email_123@coopbank.com"
		)
		self.assertEqual(res["status"], "error")
		self.assertEqual(res["code"], "NOT_FOUND")


class TestLeadSanitizationXSS(unittest.TestCase):
	"""
	Tests that user free-text inputs are sanitized before persistence to mitigate stored-XSS vulnerabilities.
	"""

	@classmethod
	def setUpClass(cls):
		frappe.set_user("Administrator")
		# Create a test lead
		cls.lead = frappe.new_doc("A2C Lead")
		cls.lead.phone_number = "+251977000001"
		cls.lead.lead_source = "Agent Entry"
		cls.lead.status = "Active"
		cls.lead.insert(ignore_permissions=True)
		cls.lead_id = cls.lead.name
		frappe.db.commit()

	@classmethod
	def tearDownClass(cls):
		frappe.set_user("Administrator")
		for name in frappe.get_all("A2C Lead", filters={"phone_number": "+251977000001"}, pluck="name"):
			frappe.delete_doc("A2C Lead", name, ignore_permissions=True, force=True)
		frappe.db.commit()

	def test_comment_content_sanitization(self):
		from oan_a2c.api.v1.leads import add_lead_comment
		payload = "<script>alert('XSS')</script>Safe text <b>bold</b>"
		res = add_lead_comment(lead_id=self.lead_id, content=payload)
		self.assertEqual(res["status"], "success")
		comment_id = res["data"]["comment_id"]
		
		# Verify comment is sanitized in DB
		doc = frappe.get_doc("A2C Lead Audit Event", comment_id)
		self.assertNotIn("<script>", doc.event_description)
		self.assertIn("Safe text <b>bold</b>", doc.event_description)

	def test_lead_status_reason_sanitization(self):
		from oan_a2c.api.v1.leads import update_lead_status
		payload = "<iframe src='javascript:alert(1)'></iframe>Reason text"
		res = update_lead_status(lead_id=self.lead_id, status="Verified", reason=payload)
		self.assertEqual(res["status"], "success")

		# Check DB timeline comment
		comments = frappe.get_all(
			"A2C Lead Audit Event",
			filters={"lead": self.lead_id, "event_type": "Status Changed"},
			fields=["event_description"]
		)
		self.assertTrue(any("Reason text" in c["event_description"] for c in comments))
		self.assertTrue(all("<iframe>" not in c["event_description"] for c in comments))

	def test_credit_info_purpose_message_sanitization(self):
		from oan_a2c.api.v1.leads import add_lead_credit_info
		payload = "<img src=x onerror=alert(1)>Credit Purpose"
		res = add_lead_credit_info(
			lead_id=self.lead_id,
			loan_type="Input loan (seeds, agrochemicals)",
			loan_amount=5000.0,
			purpose_message=payload
		)
		self.assertEqual(res["status"], "success")
		credit_info_id = res["data"]["credit_info_id"]

		doc = frappe.get_doc("A2C Credit Information", credit_info_id)
		self.assertNotIn("onerror", doc.purpose_message)
		self.assertNotIn("<img", doc.purpose_message)
		self.assertIn("Credit Purpose", doc.purpose_message)

	def test_schedule_visit_notes_sanitization(self):
		from oan_a2c.api.v1.leads import schedule_visit
		payload = "<a href='javascript:alert(1)'>Click me</a>Visit Notes"
		res = schedule_visit(
			lead_id=self.lead_id,
			visit_date="2026-06-20",
			visit_time="11:00:00",
			region="Oromia",
			zone="East Shewa",
			woreda="Ada'ama",
			kebele="01",
			notes=payload
		)
		self.assertEqual(res["status"], "success")
		schedule_id = res["data"]["schedule_id"]

		doc = frappe.get_doc("A2C Visit Schedule", schedule_id)
		self.assertNotIn("javascript:", doc.notes)
		self.assertIn("Visit Notes", doc.notes)

	def test_webhook_call_notes_sanitization(self):
		from oan_a2c.api.v1.webhooks import lead_inbound
		# Unauthenticated Webhook parameters external_ref_id and timestamp sanitization
		xss_ref = "<script>alert('Ref')</script>"
		xss_time = "<img src=1 onerror=alert('Time')>"
		
		# Clear existing lead with phone 2 to ensure it creates one
		test_phone = "+251977000002"
		for name in frappe.get_all("A2C Lead", filters={"phone_number": test_phone}, pluck="name"):
			frappe.delete_doc("A2C Lead", name, ignore_permissions=True, force=True)
			
		res = lead_inbound(
			phone_number=test_phone,
			lead_source="Missed Call",
			external_ref_id=xss_ref,
			timestamp=xss_time
		)
		self.assertEqual(res["status"], "success")
		lead_id = res["data"]["lead_id"]

		doc = frappe.get_doc("A2C Lead", lead_id)
		self.assertNotIn("<script>", doc.call_notes)
		self.assertNotIn("onerror", doc.call_notes)
		
		# Clean up
		frappe.delete_doc("A2C Lead", lead_id, ignore_permissions=True, force=True)
		frappe.db.commit()



