"""Runtime tests for the farmer-facing B2C surface.

These cover the things static analysis cannot: that the permission hooks actually
scope rows the way the catalog and application endpoints assume, and that the
filter-composition in list_catalog narrows rather than replaces.
"""

import unittest


class FarmerB2CFixtures(unittest.TestCase):
	"""Shared fixtures: one bank, two farmers, one dev agent, a small catalog."""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		import frappe

		from oan_a2c.a2c_marketplace.roles import (
			BANK_AGENT_ROLE,
			DEVELOPMENT_AGENT_ROLE,
			FARMER_ROLE,
		)

		frappe.set_user("Administrator")
		cls.h = frappe.generate_hash(length=8)

		cls.bank_label = f"B2CBank-{cls.h}"
		bank_doc = frappe.get_doc(
			{
				"doctype": "A2C Participating Bank",
				"bank_name": cls.bank_label,
				"bank_code": cls.bank_label,
			}
		).insert(ignore_permissions=True, ignore_mandatory=True)
		frappe.db.set_value("A2C Participating Bank", bank_doc.name, "status", "Active")
		cls.bank = bank_doc.name

		def _user(prefix, role):
			email = f"{prefix}-{cls.h}@example.com"
			if not frappe.db.exists("User", email):
				frappe.flags.in_import = True
				try:
					frappe.get_doc(
						{
							"doctype": "User",
							"email": email,
							"first_name": prefix,
							"roles": [{"role": role}],
						}
					).insert(ignore_permissions=True, ignore_mandatory=True)
				finally:
					frappe.flags.in_import = False
			return email

		cls.farmer_a = _user("b2c-farmer-a", FARMER_ROLE)
		cls.farmer_b = _user("b2c-farmer-b", FARMER_ROLE)
		cls.dev_agent = _user("b2c-dev", DEVELOPMENT_AGENT_ROLE)
		cls.bank_agent = _user("b2c-bankagent", BANK_AGENT_ROLE)
		frappe.get_doc(
			{
				"doctype": "User Permission",
				"user": cls.bank_agent,
				"allow": "A2C Participating Bank",
				"for_value": cls.bank,
			}
		).insert(ignore_permissions=True, ignore_mandatory=True)

		cls.profile_a = frappe.get_doc(
			{
				"doctype": "A2C Farmer Profile",
				"user": cls.farmer_a,
				"first_name": "A",
				"last_name": "Farmer",
				"phone_number": f"+25191{int(cls.h[:6], 16):06d}",
			}
		).insert(ignore_permissions=True, ignore_mandatory=True)
		cls.profile_b = frappe.get_doc(
			{
				"doctype": "A2C Farmer Profile",
				"user": cls.farmer_b,
				"first_name": "B",
				"last_name": "Farmer",
				"phone_number": f"+25192{int(cls.h[:6], 16):06d}",
			}
		).insert(ignore_permissions=True, ignore_mandatory=True)

		def _product(name):
			return frappe.get_doc(
				{
					"doctype": "A2C Loan Product",
					"product_name": name,
					"bank": cls.bank,
					"min_interest_rate": 5,
					"max_amount": 1000,
					"tenure_months": 12,
					"status": "Active",
				}
			).insert(ignore_permissions=True, ignore_mandatory=True)

		cls.prod_1 = _product(f"B2CProd1-{cls.h}")
		cls.prod_2 = _product(f"B2CProd2-{cls.h}")
		frappe.db.commit()

	@classmethod
	def tearDownClass(cls):
		import frappe

		frappe.set_user("Administrator")
		frappe.db.rollback()
		super().tearDownClass()

	def tearDown(self):
		import frappe

		frappe.set_user("Administrator")


class TestSavedProducts(FarmerB2CFixtures):
	"""A saved product belongs to a User, and to nobody else."""

	def setUp(self):
		import frappe

		frappe.set_user("Administrator")
		frappe.db.delete("A2C Saved Product")

	def test_saved_products_are_scoped_to_the_saving_user(self):
		import frappe

		from oan_a2c.api.v1.farmer.catalog import get_saved_products, save_product

		frappe.set_user(self.farmer_a)
		save_product(loan_product=self.prod_1.name)

		mine = get_saved_products()["data"]["products"]
		self.assertEqual([p["name"] for p in mine], [self.prod_1.name])

		frappe.set_user(self.farmer_b)
		theirs = get_saved_products()["data"]["products"]
		self.assertEqual(theirs, [], "farmer B must not see farmer A's bookmarks")

		# The row itself must be invisible, not merely filtered by the endpoint.
		self.assertEqual(frappe.get_list("A2C Saved Product", pluck="name"), [])

	def test_saving_does_not_require_a_farmer_profile(self):
		"""Bookmarking is a browsing convenience open to any signed-in user."""
		import frappe

		from oan_a2c.api.v1.farmer.catalog import get_saved_products, save_product

		frappe.set_user(self.bank_agent)
		save_product(loan_product=self.prod_1.name)
		saved = get_saved_products()["data"]["products"]
		self.assertEqual([p["name"] for p in saved], [self.prod_1.name])

	def test_saving_twice_is_idempotent(self):
		import frappe

		from oan_a2c.api.v1.farmer.catalog import get_saved_products, save_product

		frappe.set_user(self.farmer_a)
		save_product(loan_product=self.prod_2.name)
		save_product(loan_product=self.prod_2.name)

		saved = get_saved_products()["data"]["products"]
		self.assertEqual([p["name"] for p in saved], [self.prod_2.name])

	def test_a_user_cannot_write_into_another_users_saved_list(self):
		"""DocPerm is role "All", so the controller is what binds the row to its owner."""
		import frappe

		frappe.set_user(self.farmer_b)
		doc = frappe.get_doc(
			{
				"doctype": "A2C Saved Product",
				"user": self.farmer_a,
				"loan_product": self.prod_1.name,
			}
		).insert()

		self.assertEqual(doc.user, self.farmer_b)

	def test_unsave_removes_only_the_callers_row(self):
		import frappe

		from oan_a2c.api.v1.farmer.catalog import get_saved_products, save_product, unsave_product

		frappe.set_user(self.farmer_a)
		save_product(loan_product=self.prod_1.name)
		frappe.set_user(self.farmer_b)
		save_product(loan_product=self.prod_1.name)

		unsave_product(loan_product=self.prod_1.name)
		self.assertEqual(get_saved_products()["data"]["products"], [])

		frappe.set_user(self.farmer_a)
		still_mine = get_saved_products()["data"]["products"]
		self.assertEqual([p["name"] for p in still_mine], [self.prod_1.name])


class TestCatalogFilterComposition(FarmerB2CFixtures):
	"""Filters that all constrain `name` must intersect, never overwrite."""

	def _catalog(self, **kwargs):
		from oan_a2c.api.v1.farmer.catalog import list_catalog

		kwargs.setdefault("limit", 20)
		kwargs.setdefault("start", 0)
		kwargs.setdefault("sort_by", "product_name")
		return list_catalog(**kwargs)["data"]["products"]

	def test_is_saved_intersects_with_an_explicit_loan_product(self):
		"""`?loan_product=X&is_saved=1` asks whether X is saved -- not what is saved."""
		import frappe

		from oan_a2c.api.v1.farmer.catalog import save_product

		frappe.set_user(self.farmer_a)
		save_product(loan_product=self.prod_1.name)

		hit = self._catalog(loan_product=self.prod_1.name, is_saved=True)
		self.assertEqual([p["name"] for p in hit], [self.prod_1.name])

		# prod_2 is not saved, so constraining to it must yield nothing rather than
		# falling back to "everything the farmer has saved".
		miss = self._catalog(loan_product=self.prod_2.name, is_saved=True)
		self.assertEqual(miss, [])

	def test_is_saved_with_no_bookmarks_returns_an_empty_page(self):
		import frappe

		frappe.set_user(self.farmer_b)
		page = self._catalog(is_saved=True)
		self.assertEqual(page, [])

	def test_every_sort_is_deterministic_across_pages(self):
		"""Ties must break on a unique key or pagination repeats and skips rows.

		The fixture products share a rate, amount and tenure, so every sort except
		product_name is entirely ties -- exactly the case where an unstable sort
		shows up.
		"""
		from oan_a2c.api.v1.farmer.catalog import _SORT_COLUMNS

		for key, clause in _SORT_COLUMNS.items():
			self.assertTrue(
				clause.strip().endswith("name asc"),
				f"sort '{key}' has no tiebreaker: {clause!r}",
			)

	def test_paging_a_tied_sort_yields_no_duplicates(self):
		import frappe

		frappe.set_user(self.farmer_a)
		# Both fixture products carry min_interest_rate 5, so this sort is all ties.
		page_1 = self._catalog(sort_by="interest_low_high", limit=1, start=0)
		page_2 = self._catalog(sort_by="interest_low_high", limit=1, start=1)

		names = [p["name"] for p in page_1] + [p["name"] for p in page_2]
		self.assertEqual(len(names), len(set(names)), "a product appeared on two pages")

	def test_pagination_shape_is_identical_on_an_empty_page(self):
		"""Short-circuited empty results must not need a client special case."""
		import frappe

		from oan_a2c.api.v1.farmer.catalog import list_catalog

		frappe.set_user(self.farmer_b)
		empty = list_catalog(limit=20, start=0, sort_by="product_name", is_saved=True)
		populated = list_catalog(limit=20, start=0, sort_by="product_name")

		self.assertEqual(sorted(empty["pagination"]), sorted(populated["pagination"]))
		self.assertEqual(empty["pagination"]["total"], 0)

	def test_catalog_and_saved_products_enrich_bank_info(self):
		"""Products must include bank_name and bank_logo alongside bank id."""
		import frappe

		from oan_a2c.api.v1.farmer.catalog import get_saved_products, list_catalog, save_product

		frappe.set_user(self.farmer_a)
		catalog_res = list_catalog(limit=5, start=0, sort_by="product_name")
		products = catalog_res["data"]["products"]
		self.assertTrue(len(products) > 0)
		for p in products:
			self.assertIn("bank", p)
			self.assertIn("bank_name", p)
			self.assertIn("bank_logo", p)
			if p["bank"] == self.bank:
				self.assertEqual(p["bank_name"], self.bank_label)

		save_product(loan_product=self.prod_1.name)
		saved_res = get_saved_products(limit=5, start=0)
		saved_products = saved_res["data"]["products"]
		self.assertTrue(len(saved_products) > 0)
		self.assertEqual(saved_products[0]["bank_name"], self.bank_label)


class TestApplicationSourceScoping(FarmerB2CFixtures):
	"""Self-service applications belong to the farmer, not to the CRM pipeline."""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		import frappe

		def _app(profile, source, status, phone):
			return frappe.get_doc(
				{
					"doctype": "A2C Loan Application",
					"application_source": source,
					"bank": cls.bank,
					"loan_product": cls.prod_1.name,
					"requested_amount": 100,
					"loan_amount": 100,
					"status": status,
					"first_name": "T",
					"last_name": "T",
					"phone_number": phone,
					"farmer_profile": profile,
				}
			).insert(ignore_permissions=True, ignore_mandatory=True)

		cls.self_service_app = _app(cls.profile_a.name, "Self Service", "In Transition", "10000001")
		cls.agent_app = _app(cls.profile_a.name, "Agent", "In Transition", "20000001")

	def test_development_agent_does_not_see_self_service_applications(self):
		import frappe

		frappe.set_user(self.dev_agent)
		visible = frappe.get_list("A2C Loan Application", pluck="name")
		self.assertIn(self.agent_app.name, visible)
		self.assertNotIn(self.self_service_app.name, visible)

	def test_development_agent_is_denied_a_self_service_application_by_name(self):
		"""The single-doc hook must mirror the query hook, or get_doc leaks the row."""
		import frappe

		frappe.set_user(self.dev_agent)
		self.assertFalse(
			frappe.has_permission("A2C Loan Application", "read", doc=self.self_service_app.name)
		)

	def test_bank_users_do_see_submitted_self_service_applications(self):
		"""The bank has to be able to work an application a farmer sent them."""
		import frappe

		frappe.set_user(self.bank_agent)
		visible = frappe.get_list("A2C Loan Application", pluck="name")
		self.assertIn(self.self_service_app.name, visible)

	def test_farmer_sees_agent_raised_applications_for_their_own_profile(self):
		"""Scoping is on farmer_profile, not owner -- an agent-raised application is
		still the farmer's to see."""
		import frappe

		frappe.set_user(self.farmer_a)
		visible = frappe.get_list("A2C Loan Application", pluck="name")
		self.assertIn(self.agent_app.name, visible)
		self.assertIn(self.self_service_app.name, visible)

	def test_farmer_sees_nothing_of_another_farmer(self):
		import frappe

		frappe.set_user(self.farmer_b)
		visible = frappe.get_list("A2C Loan Application", pluck="name")
		self.assertNotIn(self.agent_app.name, visible)
		self.assertNotIn(self.self_service_app.name, visible)

	def test_scope_query_does_not_enumerate_users(self):
		"""Regression lock: the exclusion must be a predicate on the row, so its cost
		is independent of how many farmers exist."""
		from oan_a2c.a2c_marketplace.permissions import loan_application_scope_query

		condition = loan_application_scope_query(self.dev_agent)
		self.assertIn("application_source", condition)
		self.assertNotIn("@example.com", condition)


class TestFarmerApplicationCreation(FarmerB2CFixtures):
	def test_self_service_applications_are_stamped_and_leadless(self):
		import frappe

		from oan_a2c.api.v1.farmer.applications import create_application

		frappe.set_user(self.farmer_a)
		res = create_application(loan_product=self.prod_1.name, requested_amount=500)
		doc = frappe.get_doc("A2C Loan Application", res["data"]["application_id"])

		self.assertEqual(doc.application_source, "Self Service")
		self.assertFalse(doc.lead_id, "the B2C flow deliberately creates no A2C Lead")
		self.assertEqual(doc.farmer_profile, self.profile_a.name)
		self.assertEqual(doc.status, "Active")

	def test_requested_amount_must_fit_the_product(self):
		"""The cap is per-product, so the schema's global bound cannot enforce it."""
		import frappe

		from oan_a2c.api.v1.farmer.applications import create_application

		frappe.set_user(self.farmer_a)
		# prod_1 has max_amount = 1000.
		res = create_application(loan_product=self.prod_1.name, requested_amount=5000)
		self.assertEqual(res["status"], "error")
		self.assertIn("exceeds", res["message"].lower())

	def test_a_consent_request_belonging_to_someone_else_is_rejected(self):
		import frappe

		from oan_a2c.api.v1.farmer.applications import create_application

		frappe.set_user(self.farmer_b)
		foreign = frappe.get_doc(
			{
				"doctype": "A2C Consent Request",
				"farmer": "openg2p-id-1",
				"farmer_fayda_id": f"fayda-{self.h}",
				"status": "Approved",
			}
		).insert(ignore_permissions=True, ignore_mandatory=True)

		# handle_api_errors turns a PermissionError into an error envelope rather
		# than letting it propagate, so assert on the envelope.
		frappe.set_user(self.farmer_a)
		res = create_application(
			loan_product=self.prod_1.name,
			requested_amount=500,
			consent_request=foreign.name,
		)
		self.assertEqual(res["status"], "error")
		self.assertEqual(res["code"], "PERMISSION_DENIED")


class TestConsentRequestOwnership(FarmerB2CFixtures):
	"""The lead a consent belongs to comes from the record, not the request body."""

	def test_omitting_lead_id_cannot_skip_the_ownership_check(self):
		import frappe

		from oan_a2c.api.v1.consent.consent import _lead_for_consent_request

		frappe.set_user("Administrator")
		lead = frappe.get_doc(
			{
				"doctype": "A2C Lead",
				"lead_source": "Self Service",
				"status": "Active",
				"first_name": "L",
				"last_name": "L",
				"phone_number": "90000001",
			}
		).insert(ignore_permissions=True, ignore_mandatory=True)

		bound = frappe.get_doc(
			{
				"doctype": "A2C Consent Request",
				"farmer": "openg2p-id-2",
				"farmer_fayda_id": f"fayda-b-{self.h}",
				"reference_doctype": "A2C Lead",
				"reference_name": lead.name,
				"status": "Pending OTP",
			}
		).insert(ignore_permissions=True, ignore_mandatory=True)

		# Omitting lead_id resolves it from the record rather than skipping the check.
		self.assertEqual(_lead_for_consent_request(bound), lead.name)
		# Asserting the wrong lead is rejected.
		with self.assertRaises(frappe.ValidationError):
			_lead_for_consent_request(bound, "some-other-lead")

	def test_a_superseded_consent_request_can_still_resolve_its_lead(self):
		"""Retrying the OTP must not orphan the earlier attempt.

		A2C Lead.consent_id only holds the latest attempt, so it is a cache. The
		relationship lives on the consent request, which is what an in-flight webhook
		for the *first* attempt has to resolve from after a second one supersedes it.
		"""
		import frappe

		from oan_a2c.api.v1.consent.consent import _lead_for_consent_request

		frappe.set_user("Administrator")
		lead = frappe.get_doc(
			{
				"doctype": "A2C Lead",
				"lead_source": "Self Service",
				"status": "Active",
				"first_name": "R",
				"last_name": "R",
				"phone_number": "80000001",
			}
		).insert(ignore_permissions=True, ignore_mandatory=True)

		def _attempt(suffix):
			cr = frappe.get_doc(
				{
					"doctype": "A2C Consent Request",
					"farmer": "openg2p-id-retry",
					"farmer_fayda_id": f"fayda-retry-{suffix}-{self.h}",
					"reference_doctype": "A2C Lead",
					"reference_name": lead.name,
					"status": "Pending OTP",
				}
			).insert(ignore_permissions=True, ignore_mandatory=True)
			# Mirrors what request_otp writes.
			frappe.db.set_value("A2C Lead", lead.name, "consent_id", cr.name, update_modified=False)
			return cr

		first = _attempt("one")
		second = _attempt("two")

		# The cache names the latest attempt...
		self.assertEqual(frappe.db.get_value("A2C Lead", lead.name, "consent_id"), second.name)
		# ...but the superseded attempt still knows its own lead.
		self.assertEqual(_lead_for_consent_request(first), lead.name)
		self.assertEqual(_lead_for_consent_request(second), lead.name)

	def test_a_self_service_consent_has_no_lead(self):
		import frappe

		from oan_a2c.api.v1.consent.consent import _lead_for_consent_request

		frappe.set_user("Administrator")
		standalone = frappe.get_doc(
			{
				"doctype": "A2C Consent Request",
				"farmer": "openg2p-id-3",
				"farmer_fayda_id": f"fayda-c-{self.h}",
				"status": "Pending OTP",
			}
		).insert(ignore_permissions=True, ignore_mandatory=True)

		self.assertIsNone(_lead_for_consent_request(standalone))
		with self.assertRaises(frappe.ValidationError):
			_lead_for_consent_request(standalone, "any-lead")


class TestCatalogLimits(unittest.TestCase):
	"""Facet boundaries and schema bounds must come from the same constants, or the
	UI offers a range the API rejects."""

	def test_facets_publish_the_schema_bounds(self):
		import frappe

		from oan_a2c.a2c_marketplace.doctype_schemas import (
			MAX_INTEREST_RATE,
			MAX_LOAN_AMOUNT,
			MAX_TENURE_MONTHS,
		)
		from oan_a2c.api.v1.farmer.catalog import get_catalog_facets

		frappe.set_user("Administrator")
		data = get_catalog_facets()["data"]

		self.assertEqual(data["amount_range"]["max"], float(MAX_LOAN_AMOUNT))
		self.assertEqual(data["max_interest_rate"], float(MAX_INTEREST_RATE))
		self.assertEqual(data["tenure_range"]["max"], MAX_TENURE_MONTHS)
		self.assertIn("categories", data)
		self.assertIn("tags", data)
		for c in data["categories"]:
			self.assertIn("id", c)
			self.assertIn("name", c)
		for t in data["tags"]:
			self.assertIn("id", t)
			self.assertIn("name", t)

	def test_facets_always_carry_a_tenures_list(self):
		"""The sidebar reads `tenures` unconditionally; dropping it crashed the page.

		It is also the data the tenure chips are built from -- `tenure_range` is the
		schema's 1..1200 validation span, which is not something to render chips
		inside. The values must be real tenures, sorted, with no blanks.
		"""
		import frappe

		from oan_a2c.api.v1.farmer.catalog import get_catalog_facets, list_catalog

		frappe.set_user("Administrator")
		data = get_catalog_facets()["data"]

		self.assertIn("tenures", data)
		tenures = data["tenures"]
		self.assertIsInstance(tenures, list)
		self.assertTrue(all(isinstance(t, int) and t > 0 for t in tenures))
		self.assertEqual(tenures, sorted(tenures))
		self.assertEqual(len(tenures), len(set(tenures)))

		# Every offered tenure has to return something -- that is the whole contract
		# of a facet, and ListCatalogSchema takes it as a min/max span.
		for months in tenures:
			page = list_catalog(min_tenure_months=months, max_tenure_months=months)
			self.assertEqual(page["status"], "success")
			self.assertTrue(
				page["data"]["products"],
				f"tenure facet {months} is offered but matches no product",
			)

	def test_product_schema_rejects_values_beyond_the_published_bounds(self):
		from pydantic import ValidationError

		from oan_a2c.a2c_marketplace.doctype_schemas import MAX_INTEREST_RATE, SingleProductSchema

		with self.assertRaises(ValidationError):
			SingleProductSchema(
				product_name="Too Expensive",
				min_interest_rate=MAX_INTEREST_RATE + 1,
				max_amount=1000,
				tenure_months=12,
			)


class TestFarmerProfileAndConsent(FarmerB2CFixtures):
	def test_farmer_get_basic_profile(self):
		import frappe

		from oan_a2c.api.v1.loan_applications import get_basic_profile

		cr = frappe.get_doc(
			{
				"doctype": "A2C Consent Request",
				"farmer": "openg2p-id-test",
				"farmer_fayda_id": f"fayda-prof-{self.h}",
				"status": "Approved",
				"owner": self.farmer_a,
			}
		).insert(ignore_permissions=True, ignore_mandatory=True)
		frappe.db.set_value("A2C Farmer Profile", self.profile_a.name, "consent_id", cr.name)

		frappe.set_user(self.farmer_a)
		res = get_basic_profile()
		self.assertEqual(res["status"], "success")
		self.assertTrue(res["data"]["farmer_profile_created"])
		self.assertEqual(res["data"]["first_name"], "A")
		self.assertEqual(res["data"]["consent_request"]["name"], cr.name)
		self.assertEqual(res["data"]["consent_request"]["status"], "Approved")

	def test_farmer_get_basic_profile_no_consent_does_not_throw(self):
		import frappe

		from oan_a2c.api.v1.loan_applications import get_basic_profile

		# Farmer without profile or consent gets 200 OK with farmer_profile_created=False
		new_farmer_user = frappe.get_doc(
			{
				"doctype": "User",
				"email": f"newfarmer_{self.h}@example.com",
				"first_name": "New",
				"roles": [{"role": "A2C Farmer"}],
			}
		).insert(ignore_permissions=True, ignore_mandatory=True)

		frappe.set_user(new_farmer_user.name)
		res = get_basic_profile()
		self.assertEqual(res["status"], "success")
		self.assertFalse(res["data"]["farmer_profile_created"])
		self.assertIsNone(res["data"]["consent_request"])

	def test_failed_or_pending_consent_blocks_submission(self):
		from unittest.mock import MagicMock, patch

		import frappe

		from oan_a2c.api.v1.consent.consent import request_otp
		from oan_a2c.api.v1.farmer.applications import create_application, submit_application

		# Initial state: farmer_b has no active consent
		frappe.set_user("Administrator")
		cr_approved = frappe.get_doc(
			{
				"doctype": "A2C Consent Request",
				"farmer": "openg2p-id-b",
				"farmer_fayda_id": f"fayda-b-{self.h}",
				"status": "Approved",
				"owner": self.farmer_b,
			}
		).insert(ignore_permissions=True, ignore_mandatory=True)
		frappe.db.set_value("A2C Farmer Profile", self.profile_b.name, "consent_id", cr_approved.name)

		# Farmer B creates a draft application using the approved consent
		frappe.set_user(self.farmer_b)
		create_application(loan_product=self.prod_1.name, requested_amount=200)

		# Now Farmer B starts a new consent request which gets stuck in Pending OTP
		with patch("oan_a2c.api.v1.consent.consent.OpenG2PConsentClient") as MockClient:
			mock_inst = MockClient.return_value
			mock_inst.get_farmer_by_fayda_id.return_value = {"id": "openg2p-id-b"}
			mock_inst.request_otp.return_value = {
				"transaction_id": f"TXN-NEW-{self.h}",
				"masked_mobile": "091****2222",
			}
			mock_inst.session = MagicMock()
			mock_inst.session.cookies = MagicMock()
			mock_inst.session.cookies.get.return_value = "COOKIE"

			otp_res = request_otp(fayda_id=f"fayda-b-{self.h}")
			new_cr_name = otp_res["data"]["consent_request"]

		# Profile consent_id should now point to the new consent request (Pending OTP)
		profile_consent = frappe.db.get_value("A2C Farmer Profile", self.profile_b.name, "consent_id")
		self.assertEqual(profile_consent, new_cr_name)

		# Mark the new consent as Rejected
		frappe.db.set_value("A2C Consent Request", new_cr_name, "status", "Rejected")

		# Create a new application - it should fail or use the rejected consent and fail on submission
		# When creating application with default profile consent (which is now Rejected):
		app_res2 = create_application(loan_product=self.prod_1.name, requested_amount=200)
		app_id2 = app_res2["data"]["application_id"]
		doc2 = frappe.get_doc("A2C Loan Application", app_id2)
		self.assertEqual(doc2.consent_id, new_cr_name)

		# Attempting to submit application with rejected consent must fail
		submit_res = submit_application(application_id=app_id2)
		self.assertEqual(submit_res["status"], "error")
		self.assertIn("not approved", submit_res["message"].lower())

	def test_dev_agent_leadless_request_otp_raises_validation_error(self):
		"""Development Agent calling request_otp without lead_id must be rejected."""
		import frappe

		from oan_a2c.api.v1.consent.consent import request_otp

		frappe.set_user(self.dev_agent)
		res = request_otp(fayda_id=f"fayda-dev-{self.h}")
		self.assertEqual(res["status"], "error")
		self.assertEqual(res.get("code"), "VALIDATION_ERROR")
		self.assertIn("lead_id is required", res.get("message", "").lower())

	def test_dev_agent_can_submit_application(self):
		"""Development Agent can call submit_application to submit an Active application."""
		import frappe

		from oan_a2c.api.v1.farmer.applications import submit_application

		frappe.set_user("Administrator")
		cr_approved = frappe.get_doc(
			{
				"doctype": "A2C Consent Request",
				"farmer": "openg2p-id-dev-sub",
				"farmer_fayda_id": f"fayda-dev-sub-{self.h}",
				"status": "Approved",
				"owner": self.dev_agent,
			}
		).insert(ignore_permissions=True, ignore_mandatory=True)

		app = frappe.get_doc(
			{
				"doctype": "A2C Loan Application",
				"application_source": "Agent",
				"farmer_profile": self.profile_a.name,
				"bank": self.bank,
				"loan_product": self.prod_1.name,
				"requested_amount": 200,
				"loan_amount": 200,
				"consent_id": cr_approved.name,
				"status": "Active",
				"current_step": 1,
				"first_name": "DevSubmitted",
				"last_name": "DevTest",
				"phone_number": "+251911999888",
			}
		).insert(ignore_permissions=True)

		frappe.set_user(self.dev_agent)
		res = submit_application(application_id=app.name)
		self.assertEqual(res["status"], "success")
		self.assertEqual(frappe.db.get_value("A2C Loan Application", app.name, "status"), "In Transition")


class TestProductDetailPermissions(FarmerB2CFixtures):
	"""Test get_product and catalog permissions for Farmer and Development Agent."""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		import frappe

		cls.inactive_prod = frappe.get_doc(
			{
				"doctype": "A2C Loan Product",
				"product_name": f"InactiveProd-{cls.h}",
				"bank": cls.bank,
				"min_interest_rate": 5,
				"max_amount": 1000,
				"tenure_months": 12,
			}
		).insert(ignore_permissions=True, ignore_mandatory=True)
		frappe.db.set_value("A2C Loan Product", cls.inactive_prod.name, "status", "Pending Approval")

	def test_farmer_can_call_get_product_for_active_product(self):
		import frappe

		from oan_a2c.api.v1.seller.loan_products import get_product

		frappe.set_user(self.farmer_a)
		res = get_product(product_id=self.prod_1.name)
		self.assertEqual(res["status"], "success")
		self.assertEqual(res["data"]["product"]["name"], self.prod_1.name)

	def test_farmer_cannot_call_get_product_for_inactive_product(self):
		import frappe

		from oan_a2c.api.v1.seller.loan_products import get_product

		frappe.set_user(self.farmer_a)
		res = get_product(product_id=self.inactive_prod.name)
		self.assertEqual(res["status"], "error")

	def test_dev_agent_can_call_get_product_for_active_product(self):
		import frappe

		from oan_a2c.api.v1.seller.loan_products import get_product

		frappe.set_user(self.dev_agent)
		res = get_product(product_id=self.prod_1.name)
		self.assertEqual(res["status"], "success")
		self.assertEqual(res["data"]["product"]["name"], self.prod_1.name)

	def test_dev_agent_cannot_call_get_product_for_inactive_product(self):
		import frappe

		from oan_a2c.api.v1.seller.loan_products import get_product

		frappe.set_user(self.dev_agent)
		res = get_product(product_id=self.inactive_prod.name)
		self.assertEqual(res["status"], "error")

	def test_dev_agent_query_loan_products_only_returns_active(self):
		import frappe

		frappe.set_user(self.dev_agent)
		visible = frappe.get_list("A2C Loan Product", pluck="name")
		self.assertIn(self.prod_1.name, visible)
		self.assertNotIn(self.inactive_prod.name, visible)

	def test_bank_agent_catalog_is_scoped_to_own_bank(self):
		import frappe

		from oan_a2c.api.v1.farmer.catalog import list_catalog

		other_bank = frappe.get_doc(
			{
				"doctype": "A2C Participating Bank",
				"bank_name": f"OtherBank-{self.h}",
				"bank_code": f"OtherBank-{self.h}",
			}
		).insert(ignore_permissions=True, ignore_mandatory=True)
		frappe.db.set_value("A2C Participating Bank", other_bank.name, "status", "Active")

		other_prod = frappe.get_doc(
			{
				"doctype": "A2C Loan Product",
				"product_name": f"OtherProd-{self.h}",
				"bank": other_bank.name,
				"min_interest_rate": 5,
				"max_amount": 1000,
				"tenure_months": 12,
			}
		).insert(ignore_permissions=True, ignore_mandatory=True)
		frappe.db.set_value("A2C Loan Product", other_prod.name, "status", "Active")

		frappe.set_user(self.bank_agent)
		catalog_res = list_catalog(limit=50, start=0)
		products = catalog_res["data"]["products"]
		product_names = [p["name"] for p in products]

		self.assertIn(self.prod_1.name, product_names)
		self.assertNotIn(other_prod.name, product_names)
		self.assertTrue(all("applications_count" in p for p in products))
		self.assertTrue(all("category" in p for p in products))

		frappe.set_user(self.farmer_a)
		farmer_catalog = list_catalog(limit=50, start=0)["data"]["products"]
		self.assertTrue(all("applications_count" not in p for p in farmer_catalog))
		self.assertTrue(all(p.get("status") == "Active" for p in farmer_catalog))
		self.assertTrue(all("category" in p for p in farmer_catalog))

	def test_bank_agent_cannot_call_get_product_for_another_bank(self):
		import frappe

		from oan_a2c.api.v1.seller.loan_products import get_product

		other_bank = frappe.get_doc(
			{
				"doctype": "A2C Participating Bank",
				"bank_name": f"OtherBank2-{self.h}",
				"bank_code": f"OtherBank2-{self.h}",
			}
		).insert(ignore_permissions=True, ignore_mandatory=True)
		frappe.db.set_value("A2C Participating Bank", other_bank.name, "status", "Active")

		other_prod = frappe.get_doc(
			{
				"doctype": "A2C Loan Product",
				"product_name": f"OtherProd2-{self.h}",
				"bank": other_bank.name,
				"min_interest_rate": 5,
				"max_amount": 1000,
				"tenure_months": 12,
			}
		).insert(ignore_permissions=True, ignore_mandatory=True)
		frappe.db.set_value("A2C Loan Product", other_prod.name, "status", "Active")

		frappe.set_user(self.bank_agent)
		res = get_product(product_id=other_prod.name)
		self.assertEqual(res["status"], "error")
