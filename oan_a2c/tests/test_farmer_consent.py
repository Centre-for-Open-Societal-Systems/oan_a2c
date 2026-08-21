"""Farmer-side consent: lead bootstrap and the row-level scoping it depends on.

The consent endpoints themselves are covered by test_consent.py — they are shared
with the Development Agent and are not re-tested here. What is new for the farmer
is that they now hold write on A2C Lead / A2C Consent Request, which is only safe
because farmer_own_lead_query / farmer_own_consent_query / farmer_own_doc_permission
bound it to their own records. Those bounds are what this file asserts.
"""

import random
import unittest


class TestFarmerConsentBootstrap(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		import frappe

		from oan_a2c.a2c_marketplace.roles import DEVELOPMENT_AGENT_ROLE, FARMER_ROLE

		cls.h = frappe.generate_hash(length=8)
		# Phone numbers must be numeric: A2C Lead.phone_number is Data(Phone) and
		# Frappe validates it, so a hex hash suffix is rejected outright.
		cls.num = str(random.randint(100000, 999999))

		cls.farmer_a = f"fc-farmer-a-{cls.h}@example.com"
		frappe.get_doc(
			{
				"doctype": "User",
				"email": cls.farmer_a,
				"first_name": "FarmerA",
				"mobile_no": f"+25191{cls.num}0",
				"roles": [{"role": FARMER_ROLE}],
			}
		).insert(ignore_permissions=True)

		cls.farmer_b = f"fc-farmer-b-{cls.h}@example.com"
		frappe.get_doc(
			{
				"doctype": "User",
				"email": cls.farmer_b,
				"first_name": "FarmerB",
				"mobile_no": f"+25191{cls.num}1",
				"roles": [{"role": FARMER_ROLE}],
			}
		).insert(ignore_permissions=True)

		# Its own user, so the "first ever call" assertions below cannot be
		# perturbed by another test having already started a consent.
		cls.farmer_fresh = f"fc-farmer-fresh-{cls.h}@example.com"
		frappe.get_doc(
			{
				"doctype": "User",
				"email": cls.farmer_fresh,
				"first_name": "FarmerFresh",
				"mobile_no": f"+25191{cls.num}2",
				"roles": [{"role": FARMER_ROLE}],
			}
		).insert(ignore_permissions=True)

		cls.farmer_no_phone = f"fc-farmer-np-{cls.h}@example.com"
		frappe.get_doc(
			{
				"doctype": "User",
				"email": cls.farmer_no_phone,
				"first_name": "FarmerNoPhone",
				"roles": [{"role": FARMER_ROLE}],
			}
		).insert(ignore_permissions=True)

		cls.dev_agent = f"fc-agent-{cls.h}@example.com"
		frappe.get_doc(
			{
				"doctype": "User",
				"email": cls.dev_agent,
				"first_name": "Agent",
				"roles": [{"role": DEVELOPMENT_AGENT_ROLE}],
			}
		).insert(ignore_permissions=True)

		# A lead belonging to nobody in this test, to prove it stays invisible.
		cls.foreign_lead = frappe.get_doc(
			{
				"doctype": "A2C Lead",
				"lead_source": "Agent Entry",
				"status": "Active",
				"first_name": "Someone",
				"last_name": "Else",
				"phone_number": f"+25191{cls.num}3",
			}
		).insert(ignore_permissions=True)

	@classmethod
	def tearDownClass(cls):
		import frappe

		frappe.set_user("Administrator")
		frappe.db.rollback()

	def tearDown(self):
		import frappe

		frappe.set_user("Administrator")

	# ── Lead bootstrap ────────────────────────────────────────────────────────

	def test_start_consent_creates_lead_owned_by_the_farmer(self):
		import frappe

		from oan_a2c.api.v1.farmer.consent import start_consent

		frappe.set_user(self.farmer_fresh)
		res = start_consent()

		lead_id = res["data"]["lead_id"]
		self.assertTrue(lead_id)
		self.assertFalse(res["data"]["consent_completed"])
		self.assertIsNone(res["data"]["consent_request"])

		lead = frappe.get_doc("A2C Lead", lead_id)
		self.assertEqual(lead.owner, self.farmer_fresh)
		self.assertEqual(lead.lead_source, "Self Service")

	def test_start_consent_is_idempotent(self):
		"""Re-entering the apply page reuses the lead instead of piling up new ones."""
		import frappe

		from oan_a2c.api.v1.farmer.consent import start_consent

		frappe.set_user(self.farmer_b)
		first = start_consent()["data"]["lead_id"]
		second = start_consent()["data"]["lead_id"]

		self.assertEqual(first, second)

	def test_start_consent_without_a_phone_number_is_rejected(self):
		"""The phone is how the consent webhook matches the registry profile back
		to this account, and it is mandatory on A2C Lead — fail with the reason.

		Asserted on the helper as well as the endpoint: @handle_api_errors turns
		the throw into an error envelope, so the endpoint never raises and a
		test that only wrapped it in assertRaises would pass vacuously.
		"""
		import frappe

		from oan_a2c.api.v1.farmer.consent import get_or_create_self_service_lead, start_consent

		frappe.set_user(self.farmer_no_phone)
		with self.assertRaises(frappe.ValidationError):
			get_or_create_self_service_lead()

		frappe.local.message_log = []
		res = start_consent()
		self.assertEqual(res.get("status"), "error")

	def test_start_consent_does_not_create_a_second_lead_under_a_held_lock(self):
		"""Regression: two concurrent first-time calls used to both insert, which
		deadlocked on the `tabSeries` row A2C Lead's LD-#### autoname locks.

		The real concurrency cannot be reproduced in one transaction, so this
		asserts the guard instead: with the lock already held, a caller that
		finds an existing lead returns it rather than queuing or inserting.
		"""
		import frappe

		from oan_a2c.api.v1.farmer.consent import get_or_create_self_service_lead

		frappe.set_user(self.farmer_a)
		lead = get_or_create_self_service_lead().name

		cache = frappe.cache()
		lock_key = f"oan_a2c:self_service_lead_lock:{self.farmer_a}"
		cache.set(lock_key, b"1", ex=15)
		try:
			# The early check runs before the lock is consulted at all, so an
			# established farmer is never delayed by a stale lock.
			self.assertEqual(get_or_create_self_service_lead().name, lead)
		finally:
			cache.delete(lock_key)

		leads = frappe.get_list(
			"A2C Lead", filters={"lead_source": "Self Service", "owner": self.farmer_a}, pluck="name"
		)
		self.assertEqual(len(leads), 1)

	def test_create_application_reuses_the_consent_lead(self):
		"""A second lead here would be one with no consent attached to it."""
		import frappe

		from oan_a2c.api.v1.farmer.consent import get_or_create_self_service_lead

		frappe.set_user(self.farmer_a)
		first = get_or_create_self_service_lead()
		second = get_or_create_self_service_lead()
		self.assertEqual(first.name, second.name)

	# ── Row-level scoping ─────────────────────────────────────────────────────

	def test_farmer_cannot_list_another_farmers_lead(self):
		import frappe

		from oan_a2c.api.v1.farmer.consent import get_or_create_self_service_lead

		frappe.set_user(self.farmer_a)
		own = get_or_create_self_service_lead().name

		frappe.set_user(self.farmer_b)
		visible = frappe.get_list("A2C Lead", pluck="name")
		self.assertNotIn(own, visible)
		self.assertNotIn(self.foreign_lead.name, visible)

	def test_farmer_cannot_write_another_farmers_lead(self):
		"""The whole reason the doc hook exists: DocPerm write on A2C Lead would
		otherwise mean write on every lead in the system."""
		import frappe

		from oan_a2c.api.v1.farmer.consent import get_or_create_self_service_lead

		frappe.set_user(self.farmer_a)
		own = get_or_create_self_service_lead().name

		frappe.set_user(self.farmer_b)
		self.assertFalse(frappe.has_permission("A2C Lead", "write", doc=own))
		self.assertFalse(frappe.has_permission("A2C Lead", "write", doc=self.foreign_lead.name))

	def test_farmer_can_write_their_own_lead(self):
		"""request_otp / verify_otp / submit_consent all check write on the lead."""
		import frappe

		from oan_a2c.api.v1.farmer.consent import get_or_create_self_service_lead

		frappe.set_user(self.farmer_a)
		own = get_or_create_self_service_lead().name
		self.assertTrue(frappe.has_permission("A2C Lead", "write", doc=own))

	def test_consent_request_scoping_follows_the_lead(self):
		import frappe

		from oan_a2c.api.v1.farmer.consent import get_or_create_self_service_lead

		frappe.set_user(self.farmer_a)
		lead_a = get_or_create_self_service_lead().name

		frappe.set_user("Administrator")
		consent = frappe.get_doc(
			{
				"doctype": "A2C Consent Request",
				"lead": lead_a,
				"farmer": "999999",
				"farmer_fayda_id": f"FID{self.h}",
				"status": "Pending OTP",
			}
		).insert(ignore_permissions=True)

		frappe.set_user(self.farmer_a)
		self.assertIn(consent.name, frappe.get_list("A2C Consent Request", pluck="name"))
		self.assertTrue(frappe.has_permission("A2C Consent Request", "write", doc=consent.name))

		frappe.set_user(self.farmer_b)
		self.assertNotIn(consent.name, frappe.get_list("A2C Consent Request", pluck="name"))
		self.assertFalse(frappe.has_permission("A2C Consent Request", "write", doc=consent.name))

	def test_development_agent_scoping_is_unchanged(self):
		"""The new hooks bound the farmer role only — platform staff still see
		every lead, which is their workload."""
		import frappe

		from oan_a2c.api.v1.farmer.consent import get_or_create_self_service_lead

		frappe.set_user(self.farmer_a)
		farmer_lead = get_or_create_self_service_lead().name

		frappe.set_user(self.dev_agent)
		visible = frappe.get_list("A2C Lead", pluck="name")
		self.assertIn(self.foreign_lead.name, visible)
		self.assertIn(farmer_lead, visible)
		self.assertTrue(frappe.has_permission("A2C Lead", "write", doc=farmer_lead))
