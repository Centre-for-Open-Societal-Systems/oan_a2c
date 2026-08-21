import unittest

import frappe

from oan_a2c.a2c_marketplace.stats_cache import (
	_COUNTERS,
	_MAP_COUNTERS,
	_SCALAR_COUNTERS,
	_compute_from_db,
	compute_and_set,
	get_dashboard_stats,
	get_stats_for_bank,
)


class TestStatsCache(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		frappe.set_user("Administrator")
		suffix = frappe.generate_hash(length=6)
		cls.bank = frappe.get_doc(
			{
				"doctype": "A2C Participating Bank",
				"registered_city": "Test City",
				"kyc_document": "/private/files/test_kyc.pdf",
				"gro_name": "Test GRO",
				"ops_name": "Test Ops",
				"bank_name": f"Test Stats Bank {suffix}",
				"bank_code": f"TEST_STATS_{suffix}",
				"status": "In Review",
				"entity_type": "Commercial Bank",
				"registered_email": f"stats_{suffix}@test.com",
				"registered_phone": "+251911000000",
				"registered_region": "Addis Ababa",
				"registered_country": "Ethiopia",
			}
		).insert(ignore_permissions=True)
		cls.bank_name = cls.bank.name

		cls.agent_email = "stats_agent@test.com"
		if not frappe.db.exists("User", cls.agent_email):
			frappe.get_doc(
				{
					"doctype": "User",
					"email": cls.agent_email,
					"first_name": "Stats Agent",
					"roles": [{"role": "A2C Bank Agent"}],
				}
			).insert(ignore_permissions=True, ignore_mandatory=True)
		frappe.db.commit()

	@classmethod
	def tearDownClass(cls):
		frappe.set_user("Administrator")
		# Products first: force-deleting the bank leaves its products behind as rows
		# pointing at a bank that no longer exists. Those orphans are invisible to
		# the all-banks view (which walks the bank table) but still counted by any
		# unscoped list, so leaking them makes dashboard numbers irreproducible.
		frappe.db.delete("A2C Loan Product", {"bank": cls.bank_name})
		frappe.delete_doc("A2C Participating Bank", cls.bank_name, force=True)
		frappe.db.commit()

	def setUp(self):
		frappe.set_user("Administrator")
		frappe.db.delete("A2C Loan Product", {"bank": self.bank_name})
		frappe.db.commit()
		frappe.cache().delete_keys(f"dashboard_stats:{self.bank_name}:*")

	def test_total_products_includes_rejected(self):
		frappe.set_user(self.agent_email)
		# Create 1 Pending Approval, 1 Active, 1 Rejected, 1 Archived product
		frappe.get_doc(
			{
				"doctype": "A2C Loan Product",
				"product_name": "Test Draft",
				"bank": self.bank_name,
				"min_interest_rate": 5,
				"max_amount": 1000,
				"tenure_months": 12,
				"status": "Pending Approval",
			}
		).insert(ignore_permissions=True)

		p_active = frappe.get_doc(
			{
				"doctype": "A2C Loan Product",
				"product_name": "Test Active",
				"bank": self.bank_name,
				"min_interest_rate": 5,
				"max_amount": 1000,
				"tenure_months": 12,
				"status": "Active",
			}
		).insert(ignore_permissions=True)

		frappe.get_doc(
			{
				"doctype": "A2C Loan Product",
				"product_name": "Test Rejected",
				"bank": self.bank_name,
				"min_interest_rate": 5,
				"max_amount": 1000,
				"tenure_months": 12,
				"status": "Rejected",
			}
		).insert(ignore_permissions=True)

		frappe.get_doc(
			{
				"doctype": "A2C Loan Product",
				"product_name": "Test Archived",
				"bank": self.bank_name,
				"min_interest_rate": 5,
				"max_amount": 1000,
				"tenure_months": 12,
				"status": "Archived",
			}
		).insert(ignore_permissions=True)

		stats = _compute_from_db(self.bank_name)
		self.assertEqual(stats["total_products"], 4)  # Pending Approval + Active + Rejected + Archived
		self.assertEqual(stats["active_products"], 1)
		self.assertEqual(stats["rejected_products"], 1)
		self.assertEqual(stats["archived_products"], 1)

		# Test status update from Active -> Rejected
		p_active.status = "Rejected"
		p_active.save(ignore_permissions=True)

		stats_after = _compute_from_db(self.bank_name)
		self.assertEqual(stats_after["total_products"], 4)  # All 4 still exist
		self.assertEqual(stats_after["active_products"], 0)
		self.assertEqual(stats_after["rejected_products"], 2)

		# Test status update from Rejected -> Active
		p_active.status = "Active"
		p_active.save(ignore_permissions=True)

		stats_resumed = _compute_from_db(self.bank_name)
		self.assertEqual(stats_resumed["total_products"], 4)
		self.assertEqual(stats_resumed["active_products"], 1)
		self.assertEqual(stats_resumed["rejected_products"], 1)


class _BankFixtureMixin:
	"""A bank, an approved consent, and a helper to hang applications off them.

	`A2CLoanApplication.before_save` refuses the move into `In Transition` without
	an approved consent, so the consent is part of the fixture rather than
	something individual tests opt into.
	"""

	@classmethod
	def _make_bank(cls, label: str, phone: str):
		suffix = frappe.generate_hash(length=6)
		slug = label.replace(" ", "")
		bank = frappe.get_doc(
			{
				"doctype": "A2C Participating Bank",
				"registered_city": "Test City",
				"kyc_document": "/private/files/test_kyc.pdf",
				"gro_name": "Test GRO",
				"ops_name": "Test Ops",
				"bank_name": f"{label} {suffix}",
				"bank_code": f"TEST_{slug.upper()}_{suffix}",
				"status": "Active",
				"entity_type": "Commercial Bank",
				"registered_email": f"{slug.lower()}_{suffix}@test.com",
				"registered_phone": phone,
				"registered_region": "Addis Ababa",
				"registered_country": "Ethiopia",
			}
		).insert(ignore_permissions=True)
		cls.bank_name = bank.name
		cls.consent = frappe.get_doc(
			{
				"doctype": "A2C Consent Request",
				"consent_type": "Credit Assessment",
				"purpose": "Stats cache fixture",
				"status": "Approved",
			}
		).insert(ignore_permissions=True)
		return bank

	@classmethod
	def _make_application(cls, status: str, farmer_profile: str | None = None):
		doc = {
			"doctype": "A2C Loan Application",
			"bank": cls.bank_name,
			"first_name": "Stats",
			"last_name": status.replace(" ", ""),
			"phone_number": "+251911000002",
			"loan_amount": 1000,
			"requested_amount": 1000,
			"status": status,
			"consent_id": cls.consent.name,
		}
		if farmer_profile:
			doc["farmer_profile"] = farmer_profile
		return frappe.get_doc(doc).insert(ignore_permissions=True)

	@classmethod
	def _drop_bank_fixture(cls):
		# Products before the bank: force-deleting the bank first would leave rows
		# pointing at a bank that no longer exists, and those orphans are invisible
		# to the all-banks view, which walks the bank table.
		frappe.db.delete("A2C Loan Product", {"bank": cls.bank_name})
		frappe.delete_doc("A2C Participating Bank", cls.bank_name, force=True)
		frappe.delete_doc("A2C Consent Request", cls.consent.name, force=True)


class TestAllBanksView(_BankFixtureMixin, unittest.TestCase):
	"""The platform-admin (bank=None) aggregation.

	Regression cover for the all-banks view summing `stage_counts` -- a dict --
	into an int accumulator, which raised TypeError for every unbound admin the
	moment a single Participating Bank existed.
	"""

	@classmethod
	def setUpClass(cls):
		frappe.set_user("Administrator")
		cls._make_bank("All Banks View", "+251911000001")
		cls.visible_app = cls._make_application("In Transition")
		frappe.db.commit()

	@classmethod
	def tearDownClass(cls):
		frappe.set_user("Administrator")
		frappe.delete_doc("A2C Loan Application", cls.visible_app.name, force=True)
		cls._drop_bank_fixture()
		frappe.db.commit()

	def setUp(self):
		frappe.set_user("Administrator")
		frappe.cache().delete_keys(f"dashboard_stats:{self.bank_name}:*")

	def test_counter_tuples_partition_counters(self):
		"""Every counter is aggregated exactly once, by exactly one strategy."""
		self.assertEqual(set(_SCALAR_COUNTERS) | set(_MAP_COUNTERS), set(_COUNTERS))
		self.assertEqual(set(_SCALAR_COUNTERS) & set(_MAP_COUNTERS), set())
		self.assertEqual(len(_SCALAR_COUNTERS) + len(_MAP_COUNTERS), len(_COUNTERS))

	def test_all_banks_view_does_not_raise_on_map_counter(self):
		"""bank=None must aggregate, not TypeError, once any bank exists."""
		payload = get_dashboard_stats(None)

		self.assertIn("stats", payload)
		self.assertIn("by_bank", payload)
		self.assertIsInstance(payload["stats"]["stage_counts"], dict)
		for counter in _SCALAR_COUNTERS:
			self.assertIsInstance(payload["stats"][counter], int)

	def test_all_banks_view_merges_stage_counts_key_wise(self):
		"""Stage buckets merge by label; they are not summed into one number."""
		payload = get_dashboard_stats(None)

		by_bank = {row["bank"]: row for row in payload["by_bank"]}
		self.assertIn(self.bank_name, by_bank)
		self.assertEqual(by_bank[self.bank_name]["stage_counts"], {"In Transition": 1})
		self.assertEqual(by_bank[self.bank_name]["total_applications"], 1)

		# The platform bucket for this stage includes our row, and the scalar
		# total is at least our contribution -- other banks on the site may add
		# to both, so this asserts a floor rather than an exact figure.
		self.assertGreaterEqual(payload["stats"]["stage_counts"].get("In Transition", 0), 1)
		self.assertGreaterEqual(payload["stats"]["total_applications"], 1)

	def test_single_bank_view_is_unwrapped(self):
		"""bank=<code> returns just that bank's stats, with no by_bank breakdown."""
		payload = get_dashboard_stats(self.bank_name)

		self.assertNotIn("by_bank", payload)
		self.assertEqual(payload["stats"]["total_applications"], 1)
		self.assertEqual(payload["stats"]["stage_counts"], {"In Transition": 1})


class TestActiveExclusion(_BankFixtureMixin, unittest.TestCase):
	"""`Active` is the farmer's own pre-submission stage.

	loan_application_scope_query hides it from bank users, so a counter that
	included it would put a number on the dashboard card that the list beneath it
	can never show -- and would disclose how many hidden rows exist.
	"""

	@classmethod
	def setUpClass(cls):
		frappe.set_user("Administrator")
		cls._make_bank("Active Exclusion", "+251911000005")
		cls.visible_app = cls._make_application("In Transition")
		cls.farmer_private_app = cls._make_application("Active")
		frappe.db.commit()

	@classmethod
	def tearDownClass(cls):
		frappe.set_user("Administrator")
		for app in (cls.visible_app, cls.farmer_private_app):
			frappe.delete_doc("A2C Loan Application", app.name, force=True)
		cls._drop_bank_fixture()
		frappe.db.commit()

	def setUp(self):
		frappe.set_user("Administrator")
		frappe.cache().delete_keys(f"dashboard_stats:{self.bank_name}:*")

	def test_active_applications_are_excluded_from_db_computation(self):
		"""`Active` never reaches a bank counter."""
		stats = _compute_from_db(self.bank_name)

		# Two applications exist on this bank; only the non-Active one counts.
		self.assertEqual(frappe.db.count("A2C Loan Application", {"bank": self.bank_name}), 2)
		self.assertEqual(stats["total_applications"], 1)
		self.assertNotIn("Active", stats["stage_counts"])

	def test_submitting_moves_an_application_into_the_counters(self):
		"""Leaving `Active` is when an application first becomes countable."""
		compute_and_set(self.bank_name)  # warm the cache so incr/decr apply
		self.assertEqual(get_stats_for_bank(self.bank_name)["total_applications"], 1)

		self.farmer_private_app.reload()
		self.farmer_private_app.status = "In Transition"
		self.farmer_private_app.save(ignore_permissions=True)

		warm = get_stats_for_bank(self.bank_name)
		self.assertEqual(warm["total_applications"], 2)
		self.assertEqual(warm["stage_counts"], {"In Transition": 2})

		# The incremental hooks must agree with a cold recompute, or the hourly
		# reconcile would silently correct a number the user already acted on.
		self.assertEqual(warm, _compute_from_db(self.bank_name))

		self.farmer_private_app.status = "Active"
		self.farmer_private_app.save(ignore_permissions=True)

		reverted = get_stats_for_bank(self.bank_name)
		self.assertEqual(reverted["total_applications"], 1)
		self.assertEqual(reverted, _compute_from_db(self.bank_name))
