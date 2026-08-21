"""Discovery catalog: the filters and orderings the farmer-facing sidebar offers.

Every assertion here is about a control the UI renders. The bugs these cover were
not crashes -- the endpoint answered, it just answered about a column the farmer
could not see, or with a bound loose enough to match the whole catalog, so the
controls looked inert. Fixtures are suffixed per run and torn down in
tearDownClass; see docs/refactor-test-isolation.md.
"""

import unittest

import frappe

from oan_a2c.a2c_marketplace.roles import FARMER_ROLE
from oan_a2c.api.v1.farmer.catalog import (
	get_catalog_facets,
	list_catalog,
	save_product,
)

SUFFIX = frappe.generate_hash(length=6)
BANK_CODE = f"CATALOG_TEST_{SUFFIX}"
FARMER_EMAIL = f"catalog_farmer_{SUFFIX}@example.com"

# Chosen so no two products tie on any sortable column, and so the amount and rate
# axes disagree with each other: the product with the highest ceiling also has the
# highest floor and the longest tenure. A filter or sort reading the wrong column
# cannot accidentally produce the right answer.
PRODUCT_SPECS = [
	# label,             min_amt,  max_amt, min_rate, max_rate, tenure
	("Zeta Seed", 2_000, 50_000, 8.0, 10.5, 4),
	("Alpha Fertilizer", 5_000, 150_000, 9.0, 11.5, 6),
	("Mid Agri", 10_000, 200_000, 8.5, 12.0, 12),
	("Beta Livestock", 20_000, 300_000, 9.5, 12.5, 24),
]


class TestFarmerCatalog(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		frappe.set_user("Administrator")

		cls.bank = frappe.get_doc(
			{
				"doctype": "A2C Participating Bank",
				"bank_name": f"Catalog Test Bank {SUFFIX}",
				"bank_code": BANK_CODE,
				"status": "Active",
				"entity_type": "Commercial Bank",
				"registered_email": f"catalog_{SUFFIX}@test.com",
				"registered_phone": "+251911000000",
				"registered_city": "Test City",
				"registered_region": "Addis Ababa",
				"registered_country": "Ethiopia",
				"kyc_document": "/private/files/test_kyc.pdf",
				"gro_name": "Test GRO",
				"ops_name": "Test Ops",
			}
		).insert(ignore_permissions=True)

		cls.products = {}
		for label, min_amt, max_amt, min_rate, max_rate, tenure in PRODUCT_SPECS:
			doc = frappe.get_doc(
				{
					"doctype": "A2C Loan Product",
					"product_name": f"{label} {SUFFIX}",
					"bank": cls.bank.name,
					"status": "Active",
					"min_amount": min_amt,
					"max_amount": max_amt,
					"min_interest_rate": min_rate,
					"max_interest_rate": max_rate,
					"tenure_months": tenure,
				}
			).insert(ignore_permissions=True)
			cls.products[label] = doc.name

		# An Archived product deliberately placed inside every filter window below:
		# catalog visibility is a permission, not something a filter default hides.
		cls.archived = frappe.get_doc(
			{
				"doctype": "A2C Loan Product",
				"product_name": f"Hidden Archived {SUFFIX}",
				"bank": cls.bank.name,
				"status": "Archived",
				"min_amount": 1,
				"max_amount": 999_999,
				"min_interest_rate": 1.0,
				"max_interest_rate": 2.0,
				"tenure_months": 4,
			}
		).insert(ignore_permissions=True)

		frappe.get_doc(
			{
				"doctype": "User",
				"email": FARMER_EMAIL,
				"first_name": "Catalog",
				"last_name": "Farmer",
				"send_welcome_email": 0,
			}
		).insert(ignore_permissions=True)
		frappe.get_doc("User", FARMER_EMAIL).add_roles(FARMER_ROLE)

		cls.profile = frappe.get_doc(
			{
				"doctype": "A2C Farmer Profile",
				"first_name": "Catalog",
				"last_name": "Farmer",
				"user": FARMER_EMAIL,
			}
		).insert(ignore_permissions=True)

		frappe.db.commit()

	@classmethod
	def tearDownClass(cls):
		frappe.set_user("Administrator")
		for saved in frappe.get_all(
			"A2C Saved Product", filters={"farmer_profile": cls.profile.name}, pluck="name"
		):
			frappe.delete_doc("A2C Saved Product", saved, force=True, ignore_permissions=True)
		frappe.delete_doc("A2C Farmer Profile", cls.profile.name, force=True, ignore_permissions=True)
		frappe.delete_doc("User", FARMER_EMAIL, force=True, ignore_permissions=True)
		for name in [*cls.products.values(), cls.archived.name]:
			frappe.delete_doc("A2C Loan Product", name, force=True, ignore_permissions=True)
		frappe.delete_doc("A2C Participating Bank", cls.bank.name, force=True, ignore_permissions=True)
		frappe.db.commit()

	def setUp(self):
		frappe.set_user(FARMER_EMAIL)

	def tearDown(self):
		frappe.set_user("Administrator")

	# ---------------------------------------------------------------- helpers

	def _fixture_rows(self, response):
		"""Rows belonging to this run's fixtures, in the order returned.

		The site carries unrelated products, so narrowing to our own keeps the
		ordering assertions about the fixtures rather than about whatever else
		happens to be seeded.
		"""
		self.assertEqual(response["status"], "success", response)
		ours = set(self.products.values())
		return [p for p in response["data"]["products"] if p["name"] in ours]

	def _labels(self, response):
		by_name = {v: k for k, v in self.products.items()}
		return [by_name[p["name"]] for p in self._fixture_rows(response)]

	# ------------------------------------------------------------------- sort

	def test_sort_amount_orders_by_the_ceiling_the_card_displays(self):
		"""The card shows Max Amount, so "Amount" has to order by max_amount.

		Ordering by min_amount ranked a number that never appears on screen, and
		one that is 0 for most seeded products -- so the result looked unsorted.
		"""
		high_low = self._labels(list_catalog(limit=100, sort_by="amount_high_low"))
		self.assertEqual(high_low, ["Beta Livestock", "Mid Agri", "Alpha Fertilizer", "Zeta Seed"])

		low_high = self._labels(list_catalog(limit=100, sort_by="amount_low_high"))
		self.assertEqual(low_high, list(reversed(high_low)))

	def test_sort_interest_reads_one_column_in_both_directions(self):
		"""Both directions rank min_interest_rate -- the headline rate on the card.

		high_low used to read max_interest_rate, so reversing the sort quietly
		changed which number was being ranked.
		"""
		low_high = self._labels(list_catalog(limit=100, sort_by="interest_low_high"))
		self.assertEqual(low_high, ["Zeta Seed", "Mid Agri", "Alpha Fertilizer", "Beta Livestock"])

		high_low = self._labels(list_catalog(limit=100, sort_by="interest_high_low"))
		self.assertEqual(high_low, list(reversed(low_high)))

	def test_sort_by_name_and_by_tenure(self):
		self.assertEqual(
			self._labels(list_catalog(limit=100, sort_by="product_name")),
			["Alpha Fertilizer", "Beta Livestock", "Mid Agri", "Zeta Seed"],
		)
		self.assertEqual(
			self._labels(list_catalog(limit=100, sort_by="tenure_low_high")),
			["Zeta Seed", "Alpha Fertilizer", "Mid Agri", "Beta Livestock"],
		)

	def test_paging_a_tied_sort_never_repeats_a_product(self):
		"""Ties break on `name`, so consecutive pages partition the catalog.

		Without a tiebreaker MariaDB may order equal rows differently per query,
		and a catalog where most products share a rate showed the same product on
		two pages while another never appeared at all.
		"""
		first = [
			p["name"]
			for p in list_catalog(limit=10, start=0, sort_by="interest_low_high")["data"]["products"]
		]
		second = [
			p["name"]
			for p in list_catalog(limit=10, start=10, sort_by="interest_low_high")["data"]["products"]
		]
		self.assertEqual(set(first) & set(second), set())

	# ---------------------------------------------------------------- filters

	def test_tenure_filter_matches_exactly_not_as_an_upper_bound(self):
		"""The chips list the exact tenures on offer, so the filter is exact.

		As `<=`, selecting the longest tenure matched every shorter product too --
		picking "24 Mon" returned the entire catalog.
		"""
		rows = self._fixture_rows(list_catalog(limit=100, tenure_months="24"))
		self.assertEqual([r["tenure_months"] for r in rows], [24])
		self.assertEqual(self._labels(list_catalog(limit=100, tenure_months="24")), ["Beta Livestock"])

	def test_tenure_filter_accepts_several_chips(self):
		labels = self._labels(list_catalog(limit=100, tenure_months="4,6", sort_by="tenure_low_high"))
		self.assertEqual(labels, ["Zeta Seed", "Alpha Fertilizer"])

	def test_amount_filter_overlaps_rather_than_contains(self):
		"""A ceiling of ETB 100,000 keeps products that lend that much and more.

		Testing the product's own max_amount against the farmer's ceiling dropped
		exactly the products with the headroom to cover the request.
		"""
		labels = self._labels(list_catalog(limit=100, max_amount=100_000))
		self.assertIn("Beta Livestock", labels)  # lends up to 300,000, so covers 100,000
		self.assertIn("Zeta Seed", labels)

		# A floor still excludes products that cannot reach it.
		labels = self._labels(list_catalog(limit=100, min_amount=180_000))
		self.assertEqual(sorted(labels), ["Beta Livestock", "Mid Agri"])

	def test_interest_ceiling_filters_the_rate_on_the_card(self):
		labels = self._labels(list_catalog(limit=100, max_interest_rate=8.5))
		self.assertEqual(sorted(labels), ["Mid Agri", "Zeta Seed"])

	def test_archived_products_stay_hidden_under_every_filter(self):
		for params in (
			{},
			{"max_amount": 999_999},
			{"min_amount": 1},
			{"tenure_months": "4"},
			{"max_interest_rate": 100},
			{"search": "Hidden Archived"},
		):
			response = list_catalog(limit=100, **params)
			names = [p["name"] for p in response["data"]["products"]]
			self.assertNotIn(self.archived.name, names, params)

	def test_rejects_a_non_numeric_tenure(self):
		response = list_catalog(limit=10, tenure_months="not-a-number")
		self.assertEqual(response["status"], "error")
		self.assertEqual(response["code"], "VALIDATION_ERROR")

	def test_rejects_an_unknown_sort_key(self):
		response = list_catalog(limit=10, sort_by="best_match")
		self.assertEqual(response["status"], "error")
		self.assertEqual(response["code"], "VALIDATION_ERROR")
		self.assertIn("sort_by", response["details"])

	# ----------------------------------------------------------------- facets

	def test_every_offered_tenure_returns_something(self):
		"""A facet the catalog cannot satisfy is a chip that leads to an empty page."""
		response = get_catalog_facets()
		self.assertEqual(response["status"], "success")
		facets = response["data"]

		for tenure in (4, 6, 12, 24):
			self.assertIn(tenure, facets["tenures"])

		for tenure in facets["tenures"]:
			result = list_catalog(limit=1, tenure_months=str(tenure))
			self.assertGreater(result["pagination"]["total"], 0, tenure)

	def test_rate_ceiling_describes_the_column_the_filter_tests(self):
		"""The slider's ceiling is the highest headline rate, not the highest cap.

		Derived from max_interest_rate it sat above every product's headline rate,
		so the whole upper half of the slider selected the same set: dragging it
		changed nothing until it dropped below the cheapest product.
		"""
		ceiling = get_catalog_facets()["data"]["max_interest_rate"]
		self.assertIsNotNone(ceiling)

		highest_headline = max(
			frappe.get_all(
				"A2C Loan Product",
				filters={"status": "Active"},
				pluck="min_interest_rate",
				limit_page_length=0,
			)
		)
		self.assertEqual(ceiling, highest_headline)

		# At the ceiling every product qualifies; a hair below, at least one drops.
		at_ceiling = list_catalog(limit=1, max_interest_rate=ceiling)["pagination"]["total"]
		below = list_catalog(limit=1, max_interest_rate=ceiling - 0.01)["pagination"]["total"]
		self.assertGreater(at_ceiling, below)

	# -------------------------------------------------------------- bookmarks

	def test_catalog_reports_the_farmers_own_bookmarks(self):
		"""is_saved travels with the product so a reload keeps the icon filled."""
		target = self.products["Zeta Seed"]

		before = {p["name"]: p["is_saved"] for p in list_catalog(limit=100)["data"]["products"]}
		self.assertFalse(before[target])

		self.assertEqual(save_product(loan_product=target)["status"], "success")

		after = {p["name"]: p["is_saved"] for p in list_catalog(limit=100)["data"]["products"]}
		self.assertTrue(after[target])
		self.assertFalse(after[self.products["Mid Agri"]])
