import unittest


class TestBankScopeRuntime(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		import frappe

		from oan_a2c.a2c_marketplace.roles import BANK_AGENT_ROLE, FARMER_ROLE

		cls.h = frappe.generate_hash(length=8)
		cls.bank_label = f"Bank-{cls.h}"
		# A2C Participating Bank has mandatory registration fields; omitting them
		# made setUpClass raise MandatoryError before a single assertion ran.
		bank_doc = frappe.get_doc(
			{
				"doctype": "A2C Participating Bank",
				"bank_name": cls.bank_label,
				"bank_code": cls.bank_label,
				"status": "Active",
				"entity_type": "Commercial Bank",
				"registered_email": f"{cls.bank_label}@example.com",
				"registered_phone": "+251911000000",
				"registered_city": "Test City",
				"registered_region": "Addis Ababa",
				"registered_country": "Ethiopia",
				"kyc_document": "/private/files/test_kyc.pdf",
				"gro_name": "Test GRO",
				"ops_name": "Test Ops",
			}
		).insert(ignore_permissions=True)
		# The doctype autonames to PB-#####, so the label we passed in is not the
		# link target. Every `bank` link and User Permission below needs the real
		# name, or they fail link validation before any test body runs.
		cls.bank = bank_doc.name

		cls.bank_agent = f"agent-{cls.h}@example.com"
		frappe.get_doc(
			{
				"doctype": "User",
				"email": cls.bank_agent,
				"first_name": "Agent",
				"roles": [{"role": BANK_AGENT_ROLE}],
			}
		).insert(ignore_permissions=True, ignore_mandatory=True)
		frappe.get_doc(
			{
				"doctype": "User Permission",
				"user": cls.bank_agent,
				"allow": "A2C Participating Bank",
				"for_value": cls.bank,
			}
		).insert(ignore_permissions=True, ignore_mandatory=True)

		cls.farmer_a = f"farmer-a-{cls.h}@example.com"
		frappe.get_doc(
			{
				"doctype": "User",
				"email": cls.farmer_a,
				"first_name": "FarmerA",
				"roles": [{"role": FARMER_ROLE}],
			}
		).insert(ignore_permissions=True, ignore_mandatory=True)
		cls.profile_a = frappe.get_doc(
			{"doctype": "A2C Farmer Profile", "user": cls.farmer_a, "first_name": "F", "last_name": "A"}
		).insert(ignore_permissions=True, ignore_mandatory=True)

		cls.farmer_b = f"farmer-b-{cls.h}@example.com"
		frappe.get_doc(
			{
				"doctype": "User",
				"email": cls.farmer_b,
				"first_name": "FarmerB",
				"roles": [{"role": FARMER_ROLE}],
			}
		).insert(ignore_permissions=True, ignore_mandatory=True)
		cls.profile_b = frappe.get_doc(
			{"doctype": "A2C Farmer Profile", "user": cls.farmer_b, "first_name": "F", "last_name": "B"}
		).insert(ignore_permissions=True, ignore_mandatory=True)

		cls.farmer_no_profile = f"farmer-none-{cls.h}@example.com"
		frappe.get_doc(
			{
				"doctype": "User",
				"email": cls.farmer_no_profile,
				"first_name": "FarmerNone",
				"roles": [{"role": FARMER_ROLE}],
			}
		).insert(ignore_permissions=True, ignore_mandatory=True)

		cls.prod = frappe.get_doc(
			{
				"doctype": "A2C Loan Product",
				"product_name": f"Prod-{cls.h}",
				"bank": cls.bank,
				"min_interest_rate": 5,
				"max_amount": 1000,
				"tenure_months": 12,
				"status": "Active",
			}
		).insert(ignore_permissions=True, ignore_mandatory=True)

		# Application for Farmer A (Draft)
		cls.app_a_draft = frappe.get_doc(
			{
				"doctype": "A2C Loan Application",
				"bank": cls.bank,
				"loan_product": cls.prod.name,
				"requested_amount": 100,
				"loan_amount": 100,
				"status": "Draft",
				"first_name": "A",
				"last_name": "B",
				"phone_number": "111",
				"farmer_profile": cls.profile_a.name,
			}
		).insert(ignore_permissions=True, ignore_mandatory=True)
		# Application for Farmer A (Processing)
		cls.app_a_proc = frappe.get_doc(
			{
				"doctype": "A2C Loan Application",
				"bank": cls.bank,
				"loan_product": cls.prod.name,
				"requested_amount": 100,
				"loan_amount": 100,
				"status": "Processing",
				"first_name": "A",
				"last_name": "B",
				"phone_number": "222",
				"farmer_profile": cls.profile_a.name,
			}
		).insert(ignore_permissions=True, ignore_mandatory=True)
		# Application for Farmer B (Draft)
		cls.app_b = frappe.get_doc(
			{
				"doctype": "A2C Loan Application",
				"bank": cls.bank,
				"loan_product": cls.prod.name,
				"requested_amount": 100,
				"loan_amount": 100,
				"status": "Draft",
				"first_name": "C",
				"last_name": "D",
				"phone_number": "333",
				"farmer_profile": cls.profile_b.name,
			}
		).insert(ignore_permissions=True, ignore_mandatory=True)

		# --- A second tenant, so "sees only its own" is testable at all ---
		# Every application above belongs to cls.bank, which makes the existing
		# assertions unable to tell correct scoping apart from an empty filter.
		cls.other_bank_label = f"OtherBank-{cls.h}"
		other_bank_doc = frappe.get_doc(
			{
				"doctype": "A2C Participating Bank",
				"bank_name": cls.other_bank_label,
				"bank_code": cls.other_bank_label,
				"status": "Active",
				"entity_type": "Commercial Bank",
				"registered_email": f"{cls.other_bank_label}@example.com",
				"registered_phone": "+251911000001",
				"registered_city": "Test City",
				"registered_region": "Addis Ababa",
				"registered_country": "Ethiopia",
				"kyc_document": "/private/files/test_kyc.pdf",
				"gro_name": "Test GRO",
				"ops_name": "Test Ops",
			}
		).insert(ignore_permissions=True)
		cls.other_bank = other_bank_doc.name

		cls.other_bank_agent = f"agent-other-{cls.h}@example.com"
		frappe.get_doc({"doctype": "User", "email": cls.other_bank_agent, "first_name": "OtherAgent", "roles": [{"role": BANK_AGENT_ROLE}]}).insert(ignore_permissions=True)
		frappe.get_doc({"doctype": "User Permission", "user": cls.other_bank_agent, "allow": "A2C Participating Bank", "for_value": cls.other_bank}).insert(ignore_permissions=True)

		cls.other_prod = frappe.get_doc({"doctype": "A2C Loan Product", "product_name": f"OtherProd-{cls.h}", "bank": cls.other_bank, "min_interest_rate": 5, "max_amount": 1000, "tenure_months": 12, "status": "Active"}).insert(ignore_permissions=True)

		# Processing, not Draft: the Draft gate would hide it from both banks and
		# make a cross-bank leak indistinguishable from the lifecycle filter.
		cls.other_bank_app = frappe.get_doc({"doctype": "A2C Loan Application", "bank": cls.other_bank, "loan_product": cls.other_prod.name, "requested_amount": 100, "loan_amount": 100, "status": "Processing", "first_name": "E", "last_name": "F", "phone_number": "444", "farmer_profile": cls.profile_b.name}).insert(ignore_permissions=True)

	def tearDown(self):
		# Every test here impersonates a fixture user and none of them owned
		# putting the session back.
		from oan_a2c.tests import end_impersonation

		end_impersonation()
		super().tearDown()

	@classmethod
	def tearDownClass(cls):
		import frappe

		from oan_a2c.tests import end_impersonation

		# Before the rollback, not after: the rollback is what makes the fixture
		# users vanish, and anything cached against them has to go with them.
		end_impersonation()
		frappe.db.rollback()

	def test_farmer_sees_own_applications(self):
		import frappe

		frappe.set_user(self.farmer_a)
		apps = frappe.get_list("A2C Loan Application", pluck="name")
		self.assertIn(self.app_a_draft.name, apps)
		self.assertIn(self.app_a_proc.name, apps)
		self.assertNotIn(self.app_b.name, apps)

	def test_farmer_sees_zero_of_another_farmer(self):
		import frappe

		frappe.set_user(self.farmer_b)
		apps = frappe.get_list("A2C Loan Application", pluck="name")
		self.assertNotIn(self.app_a_draft.name, apps)
		self.assertNotIn(self.app_a_proc.name, apps)
		self.assertIn(self.app_b.name, apps)

	def test_farmer_with_no_profile_gets_empty_list(self):
		import frappe

		frappe.set_user(self.farmer_no_profile)
		apps = frappe.get_list("A2C Loan Application", pluck="name")
		self.assertEqual(apps, [])

	def test_bank_user_sees_no_draft(self):
		import frappe

		frappe.set_user(self.bank_agent)
		apps = frappe.get_list("A2C Loan Application", pluck="name")
		self.assertNotIn(self.app_a_draft.name, apps)
		self.assertNotIn(self.app_b.name, apps)
		self.assertIn(self.app_a_proc.name, apps)

	def test_bank_user_sees_only_own_bank_applications(self):
		"""A bank agent must never see another bank's loan applications.

		The Draft gate above already proves the lifecycle half of
		loan_application_scope_query; this covers the tenancy half, which is what
		the bank-side Applications List renders.
		"""
		import frappe

		frappe.set_user(self.bank_agent)
		apps = frappe.get_list("A2C Loan Application", pluck="name")
		self.assertIn(self.app_a_proc.name, apps)
		self.assertNotIn(self.other_bank_app.name, apps)

		frappe.set_user(self.other_bank_agent)
		apps = frappe.get_list("A2C Loan Application", pluck="name")
		self.assertIn(self.other_bank_app.name, apps)
		self.assertNotIn(self.app_a_proc.name, apps)

	def test_get_all_loans_is_bank_scoped(self):
		"""The endpoint the Applications List calls, not just the query hook.

		get_all_loans counts and pages through frappe.get_list, so a regression that
		swapped either call for raw SQL (or ignore_permissions=True) would leak
		cross-bank rows into the list while every hook-level test still passed.
		"""
		import frappe

		from oan_a2c.api.v1.loan_applications import get_all_loans

		frappe.set_user(self.bank_agent)
		res = get_all_loans(page_size=100)
		self.assertEqual(res["status"], "success")
		ids = [r["application_id"] for r in res["data"]]
		self.assertIn(self.app_a_proc.name, ids)
		self.assertNotIn(self.other_bank_app.name, ids)
		self.assertNotIn(self.app_a_draft.name, ids)
		# The page total must be scoped too — it is counted through the same hook.
		self.assertEqual(res["pagination"]["total"], len(ids))

	def test_get_full_profile_denies_other_bank_application(self):
		"""Knowing an application id must not be enough to read it."""
		import frappe

		from oan_a2c.api.v1.loan_applications import get_full_profile

		frappe.set_user(self.bank_agent)
		res = get_full_profile(application_id=self.other_bank_app.name)
		self.assertEqual(res["status"], "error")
		self.assertEqual(res["code"], "PERMISSION_DENIED")
		# Leave the shared response object clean for whatever runs next.
		frappe.local.response["http_status_code"] = 200
