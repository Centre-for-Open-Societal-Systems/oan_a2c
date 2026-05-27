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
		lead.status = "Open"
		lead.insert()

		self.assertTrue(lead.name.startswith("LEAD-"))
		self.assertEqual(lead.status, "Open")

	def test_2_duplicate_active_lead_blocked(self):
		"""Two active leads for the same phone number must be rejected."""
		lead = frappe.new_doc("A2C Lead")
		lead.phone_number = self.TEST_PHONE
		lead.lead_source = "Missed Call"
		lead.status = "Open"
		lead.insert()

		duplicate = frappe.new_doc("A2C Lead")
		duplicate.phone_number = self.TEST_PHONE
		duplicate.lead_source = "IVR"
		duplicate.status = "Open"

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
		new_lead.status = "Open"
		new_lead.insert()

		self.assertTrue(new_lead.name.startswith("LEAD-"))

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
		self.assertTrue(response["lead_id"].startswith("LEAD-"))

		lead = frappe.get_doc("A2C Lead", response["lead_id"])
		self.assertEqual(lead.phone_number, self.TEST_PHONE)
		self.assertEqual(lead.status, "Open")
		self.assertIn("TELCO-001", lead.call_notes)

	def test_5_webhook_idempotent_on_duplicate_call(self):
		"""A second call for the same active phone must update, not duplicate."""
		r1 = lead_inbound(
			phone_number=self.TEST_PHONE,
			lead_source="Missed Call",
			external_ref_id="TELCO-001",
			timestamp="2026-05-19T10:00:00Z",
		)
		lead_id = r1["lead_id"]

		r2 = lead_inbound(
			phone_number=self.TEST_PHONE,
			lead_source="IVR",
			external_ref_id="TELCO-002",
			timestamp="2026-05-19T10:05:00Z",
		)

		self.assertEqual(r2["lead_id"], lead_id)
		self.assertIn("Existing active lead updated", r2["message"])

		notes = frappe.db.get_value("A2C Lead", lead_id, "call_notes")
		self.assertIn("TELCO-001", notes)
		self.assertIn("TELCO-002", notes)

		count = frappe.db.count("A2C Lead", {"phone_number": self.TEST_PHONE})
		self.assertEqual(count, 1)

	def test_6_webhook_missing_phone_raises(self):
		"""Webhook must reject requests with no phone_number."""
		with self.assertRaises(Exception):
			lead_inbound(phone_number="")

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

	def test_9_webhook_deduplication_by_external_id(self):
		"""Webhook matches and updates existing lead by external_id even across status changes."""
		r1 = lead_inbound(
			phone_number=self.TEST_PHONE,
			lead_source="IVR",
			external_ref_id="EXT-100",
			timestamp="2026-05-19T10:00:00Z",
		)
		lead_id = r1["lead_id"]

		# Set status to processed (terminal)
		frappe.db.set_value("A2C Lead", lead_id, "status", "Processed")

		# Webhook receives retry with same external_ref_id
		r2 = lead_inbound(
			phone_number=self.TEST_PHONE,
			lead_source="Missed Call",
			external_ref_id="EXT-100",
			timestamp="2026-05-19T10:05:00Z",
		)

		self.assertEqual(r2["lead_id"], lead_id)
		self.assertIn("Existing active lead updated", r2["message"])


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
				"status": "Open",
			},
			{
				"phone_number": "+251922000002",
				"external_id": "TEL-LIST-002",
				"lead_source": "IVR",
				"status": "Initiated",
			},
			{
				"phone_number": "+251922000003",
				"external_id": "TEL-LIST-003",
				"lead_source": "SMS",
				"status": "Qualified",
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
			filters={"phone_number": ("like", "+251922000%")},
			pluck="name",
		):
			frappe.delete_doc("A2C Lead", name, ignore_permissions=True, force=True)

	def test_get_leads_pagination(self):
		"""Verifies list pagination slice parameters start and page_length work."""
		from oan_a2c.api.v1.leads import get_leads
		res = get_leads(start=0, page_length=2, search_query="+251922000")
		self.assertEqual(res["status"], "success")
		self.assertEqual(res["page_length"], 2)
		self.assertEqual(res["total_count"], 5)
		self.assertEqual(len(res["results"]), 2)

		# Fetch next page
		res_page_2 = get_leads(start=2, page_length=2, search_query="+251922000")
		self.assertEqual(res_page_2["start"], 2)
		self.assertEqual(len(res_page_2["results"]), 2)

	def test_get_leads_search(self):
		"""Verifies searching by phone number, Lead ID, and external ID works."""
		from oan_a2c.api.v1.leads import get_leads
		
		# Search by Phone Number
		res_phone = get_leads(search_query="+251922000003")
		self.assertEqual(res_phone["total_count"], 1)
		self.assertEqual(res_phone["results"][0]["phone_number"], "+251922000003")

		# Search by External ID
		res_ext = get_leads(search_query="TEL-LIST-005")
		self.assertEqual(res_ext["total_count"], 1)
		self.assertEqual(res_ext["results"][0]["external_id"], "TEL-LIST-005")

		# Search by Lead ID (name)
		lead_name = self.leads[0]
		res_name = get_leads(search_query=lead_name)
		self.assertEqual(res_name["total_count"], 1)
		self.assertEqual(res_name["results"][0]["name"], lead_name)

	def test_get_leads_filters(self):
		"""Verifies applying status and lead_source filters works."""
		from oan_a2c.api.v1.leads import get_leads
		
		# Filter by Qualified status (new status)
		res_status = get_leads(status="Qualified", search_query="+251922000")
		self.assertEqual(res_status["total_count"], 1)
		self.assertEqual(res_status["results"][0]["status"], "Qualified")

		# Filter by Lead Source
		res_source = get_leads(lead_source="Missed Call", search_query="+251922000")
		self.assertEqual(res_source["total_count"], 2)
		for lead in res_source["results"]:
			self.assertEqual(lead["lead_source"], "Missed Call")

	def test_get_lead_summary(self):
		"""Verifies that get_lead_summary correctly returns counts of all leads by status."""
		from oan_a2c.api.v1.leads import get_lead_summary
		res = get_lead_summary()
		
		self.assertEqual(res["status"], "success")
		self.assertEqual(res["total"], 5)
		
		by_status = res["by_status"]
		self.assertEqual(by_status["Open"], 1)
		self.assertEqual(by_status["Initiated"], 1)
		self.assertEqual(by_status["Qualified"], 1)
		self.assertEqual(by_status["Not Interested"], 0)
		self.assertEqual(by_status["Processed"], 2)



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
		self.assertTrue(res["lead_id"].startswith("LEAD-"))

		lead = frappe.get_doc("A2C Lead", res["lead_id"])
		self.assertEqual(lead.phone_number, self.TEST_PHONE)
		self.assertEqual(lead.first_name, "Abebe")
		self.assertEqual(lead.last_name, "Bikila")
		self.assertEqual(lead.email, "abebe@coopbank.com")
		self.assertEqual(lead.lead_source, "Agent Entry")
		self.assertEqual(lead.external_id, "EXT-API-999")

	def test_create_lead_api_invalid_email(self):
		"""Verifies that an invalid email address raises a ValidationError."""
		from oan_a2c.api.v1.leads import create_lead
		with self.assertRaises(frappe.ValidationError):
			create_lead(
				phone_number=self.TEST_PHONE,
				first_name="Abebe",
				last_name="Bikila",
				email="invalid-email-format"
			)

	def test_create_lead_api_duplicate(self):
		"""Verifies that duplicate lead validation is triggered via the API creation flow."""
		from oan_a2c.api.v1.leads import create_lead
		create_lead(
			phone_number=self.TEST_PHONE,
			first_name="First",
			last_name="Last"
		)
		
		# Attempting to create a duplicate active lead
		with self.assertRaises(frappe.DuplicateEntryError):
			create_lead(
				phone_number=self.TEST_PHONE,
				first_name="Second",
				last_name="Last"
			)
