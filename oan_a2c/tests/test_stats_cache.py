import unittest

import frappe

from oan_a2c.a2c_marketplace.stats_cache import (
	_COUNTERS,
	_EXCLUDED_APPLICATION_STATUSES,
	_EXCLUDED_PRODUCT_STATUSES,
	_MAP_COUNTERS,
	_NON_ADDITIVE_COUNTERS,
	_PENDING_APPLICATION_STATUS,
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


class TestStatusLiteralDrift(unittest.TestCase):
	"""Guard against the counters filtering on statuses the doctypes cannot hold.

	A status literal that no longer appears in its Select options matches nothing,
	so the counter it guards silently reads zero forever -- which is how
	`_PENDING_STATUSES = {"Submitted", "Under Review"}` survived in this module
	against a doctype that offered neither. Cheap to assert, and it fails at the
	moment the options change rather than on a dashboard weeks later.
	"""

	def _select_options(self, doctype: str, fieldname: str) -> set[str]:
		field = frappe.get_meta(doctype).get_field(fieldname)
		self.assertIsNotNone(field, f"{doctype}.{fieldname} does not exist")
		return {opt.strip() for opt in (field.options or "").split("\n") if opt.strip()}

	def test_excluded_application_statuses_exist(self):
		valid = self._select_options("A2C Loan Application", "status")
		self.assertTrue(
			_EXCLUDED_APPLICATION_STATUSES <= valid,
			f"stats_cache filters on {_EXCLUDED_APPLICATION_STATUSES - valid}, "
			f"which A2C Loan Application.status cannot hold (options: {sorted(valid)})",
		)

	def test_product_status_literals_exist(self):
		valid = self._select_options("A2C Loan Product", "status")
		# The statuses on_product_change and _compute_from_db branch on, plus any
		# configured exclusions. Every one has to be a value the field can take.
		counted = {"Active", "Pending Approval", "Rejected", "Archived"}
		self.assertTrue(
			counted <= valid,
			f"stats_cache branches on {counted - valid}, "
			f"which A2C Loan Product.status cannot hold (options: {sorted(valid)})",
		)
		self.assertTrue(
			_EXCLUDED_PRODUCT_STATUSES <= valid,
			f"stats_cache excludes {_EXCLUDED_PRODUCT_STATUSES - valid}, which is not a valid status",
		)

	def test_archetype_states_match_the_doctype(self):
		"""ARCHETYPE_STATES is what every dashboard buckets loans by.

		If it drifts from the Select options, `by_status` grows a bucket nothing
		can land in (reads 0 forever) or silently drops a real one from the
		breakdown while `total` still counts it.
		"""
		from oan_a2c.a2c_marketplace.stages import ARCHETYPE_STATES

		valid = self._select_options("A2C Loan Application", "status")
		self.assertEqual(
			set(ARCHETYPE_STATES),
			valid,
			f"ARCHETYPE_STATES {sorted(ARCHETYPE_STATES)} != "
			f"A2C Loan Application.status options {sorted(valid)}",
		)

	def test_lead_summary_covers_every_lead_status(self):
		"""get_lead_summary both filters and totals on this list.

		A status missing from it is excluded from `total` as well as from
		`by_status`, so the dashboard undercounts rather than merely omitting a
		bucket -- and nothing on screen shows that leads are missing.
		"""
		import inspect

		from oan_a2c.api.v1.leads import get_lead_summary

		source = inspect.getsource(get_lead_summary)
		valid = self._select_options("A2C Lead", "status")
		missing = sorted(status for status in valid if f'"{status}"' not in source)
		self.assertEqual(
			missing,
			[],
			f"A2C Lead statuses {missing} are never counted by get_lead_summary, "
			f"so they are absent from both by_status and total",
		)


class TestApplicantAndPendingCounters(unittest.TestCase):
	"""`total_applicants` (distinct farmers) and `pending_applications`.

	The applicant counter is the interesting one: distinctness cannot be carried
	in a running total, so the hooks ask the DB whether the farmer is still
	represented before moving it. These tests pin that a second application from
	the same farmer does not double-count, and that the incremental result always
	matches a cold recompute.
	"""

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
				"bank_name": f"Applicants {suffix}",
				"bank_code": f"TEST_APPLICANTS_{suffix}",
				"status": "Active",
				"entity_type": "Commercial Bank",
				"registered_email": f"applicants_{suffix}@test.com",
				"registered_phone": "+251911000003",
				"registered_region": "Addis Ababa",
				"registered_country": "Ethiopia",
			}
		).insert(ignore_permissions=True)
		cls.bank_name = cls.bank.name

		cls.consent = frappe.get_doc(
			{
				"doctype": "A2C Consent Request",
				"consent_type": "Credit Assessment",
				"purpose": "Applicant counter fixture",
				"status": "Approved",
			}
		).insert(ignore_permissions=True)

		cls.profiles = [
			frappe.get_doc(
				{
					"doctype": "A2C Farmer Profile",
					"first_name": "Farmer",
					"last_name": label,
					"phone_number": f"+2519110001{idx}",
				}
			).insert(ignore_permissions=True, ignore_mandatory=True)
			for idx, label in enumerate(("One", "Two"))
		]
		cls.created_apps = []
		frappe.db.commit()

	@classmethod
	def tearDownClass(cls):
		frappe.set_user("Administrator")
		for name in cls.created_apps:
			if frappe.db.exists("A2C Loan Application", name):
				frappe.delete_doc("A2C Loan Application", name, force=True)
		for profile in cls.profiles:
			frappe.delete_doc("A2C Farmer Profile", profile.name, force=True)
		frappe.db.delete("A2C Loan Product", {"bank": cls.bank_name})
		frappe.delete_doc("A2C Participating Bank", cls.bank_name, force=True)
		frappe.delete_doc("A2C Consent Request", cls.consent.name, force=True)
		frappe.db.commit()

	def setUp(self):
		frappe.set_user("Administrator")
		for name in self.created_apps:
			if frappe.db.exists("A2C Loan Application", name):
				frappe.delete_doc("A2C Loan Application", name, force=True)
		self.created_apps.clear()
		frappe.cache().delete_keys(f"dashboard_stats:{self.bank_name}:*")

	def _app(self, profile, status: str):
		doc = frappe.get_doc(
			{
				"doctype": "A2C Loan Application",
				"bank": self.bank_name,
				"farmer_profile": profile.name,
				"consent_id": self.consent.name,
				"first_name": "Stats",
				"last_name": "Probe",
				"phone_number": "+251911000004",
				"loan_amount": 1000,
				"requested_amount": 1000,
				"status": status,
			}
		).insert(ignore_permissions=True)
		self.created_apps.append(doc.name)
		return doc

	def test_repeat_applications_from_one_farmer_count_once(self):
		"""Three applications, one farmer -> three applications, one applicant."""
		for _ in range(3):
			self._app(self.profiles[0], "In Transition")

		stats = _compute_from_db(self.bank_name)
		self.assertEqual(stats["total_applications"], 3)
		self.assertEqual(stats["total_applicants"], 1)

	def test_distinct_farmers_each_count(self):
		self._app(self.profiles[0], "In Transition")
		self._app(self.profiles[1], "In Transition")

		stats = _compute_from_db(self.bank_name)
		self.assertEqual(stats["total_applications"], 2)
		self.assertEqual(stats["total_applicants"], 2)

	def test_incremental_applicant_count_matches_recompute(self):
		"""The hooks must not double-count a farmer who applies twice."""
		self._app(self.profiles[0], "In Transition")
		compute_and_set(self.bank_name)  # warm the cache

		# Second application from the SAME farmer: applications moves, applicants does not.
		self._app(self.profiles[0], "In Transition")
		warm = get_stats_for_bank(self.bank_name)
		self.assertEqual(warm["total_applications"], 2)
		self.assertEqual(warm["total_applicants"], 1)

		# First application from a DIFFERENT farmer: both move.
		self._app(self.profiles[1], "In Transition")
		warm = get_stats_for_bank(self.bank_name)
		self.assertEqual(warm["total_applications"], 3)
		self.assertEqual(warm["total_applicants"], 2)

		self.assertEqual(warm, _compute_from_db(self.bank_name))

	def test_deleting_one_of_two_keeps_the_applicant(self):
		"""A farmer stays an applicant while any countable application survives."""
		first = self._app(self.profiles[0], "In Transition")
		self._app(self.profiles[0], "In Transition")
		compute_and_set(self.bank_name)

		frappe.delete_doc("A2C Loan Application", first.name, force=True)
		self.created_apps.remove(first.name)

		warm = get_stats_for_bank(self.bank_name)
		self.assertEqual(warm["total_applications"], 1)
		self.assertEqual(warm["total_applicants"], 1)
		self.assertEqual(warm, _compute_from_db(self.bank_name))

	def test_pending_tracks_the_in_transition_archetype(self):
		"""Pending counts the bank's pipeline, whatever the bank names its stages."""
		self._app(self.profiles[0], "In Transition")
		self._app(self.profiles[1], "Completed")

		stats = _compute_from_db(self.bank_name)
		self.assertEqual(stats["total_applications"], 2)
		self.assertEqual(stats["pending_applications"], 1)

	def test_completing_an_application_clears_it_from_pending(self):
		doc = self._app(self.profiles[0], "In Transition")
		compute_and_set(self.bank_name)
		self.assertEqual(get_stats_for_bank(self.bank_name)["pending_applications"], 1)

		doc.reload()
		doc.status = "Completed"
		doc.save(ignore_permissions=True)

		warm = get_stats_for_bank(self.bank_name)
		self.assertEqual(warm["pending_applications"], 0)
		self.assertEqual(warm["total_applications"], 1)
		self.assertEqual(warm, _compute_from_db(self.bank_name))

	def test_pending_status_literal_exists(self):
		"""The archetype the pending counter filters on must be a real option."""
		options = {
			opt.strip()
			for opt in (frappe.get_meta("A2C Loan Application").get_field("status").options or "").split("\n")
			if opt.strip()
		}
		self.assertIn(_PENDING_APPLICATION_STATUS, options)


class TestCrossBankApplicantCount(unittest.TestCase):
	"""`total_applicants` on the all-banks view counts people, not (person, bank) pairs.

	Every other scalar counter counts rows, and a row belongs to exactly one bank,
	so the platform total is the sum of the per-bank values. `total_applicants`
	counts DISTINCT farmers within a bank, and distinctness does not survive a sum:
	summing would report one farmer who applied to two banks as two applicants.

	The per-bank figure is unaffected -- that farmer is genuinely one applicant at
	each bank -- so this pins both halves at once: 1 on the platform view, 1 in each
	`by_bank` row.
	"""

	@classmethod
	def _make_bank(cls, label: str, phone: str):
		suffix = frappe.generate_hash(length=6)
		return frappe.get_doc(
			{
				"doctype": "A2C Participating Bank",
				"registered_city": "Test City",
				"kyc_document": "/private/files/test_kyc.pdf",
				"gro_name": "Test GRO",
				"ops_name": "Test Ops",
				"bank_name": f"{label} {suffix}",
				"bank_code": f"TEST_XBANK_{suffix}",
				"status": "Active",
				"entity_type": "Commercial Bank",
				"registered_email": f"xbank_{suffix}@test.com",
				"registered_phone": phone,
				"registered_region": "Addis Ababa",
				"registered_country": "Ethiopia",
			}
		).insert(ignore_permissions=True)

	@classmethod
	def setUpClass(cls):
		frappe.set_user("Administrator")
		cls.bank_a = cls._make_bank("XBank A", "+251911000021")
		cls.bank_b = cls._make_bank("XBank B", "+251911000022")

		cls.consent = frappe.get_doc(
			{
				"doctype": "A2C Consent Request",
				"consent_type": "Credit Assessment",
				"purpose": "Cross-bank applicant fixture",
				"status": "Approved",
			}
		).insert(ignore_permissions=True)

		# One human, one profile, one application at each of two banks.
		cls.profile = frappe.get_doc(
			{
				"doctype": "A2C Farmer Profile",
				"first_name": "Cross",
				"last_name": "Bank",
				"phone_number": "+251911000023",
			}
		).insert(ignore_permissions=True, ignore_mandatory=True)

		cls.apps = [
			frappe.get_doc(
				{
					"doctype": "A2C Loan Application",
					"bank": bank.name,
					"first_name": "Cross",
					"last_name": "Bank",
					"phone_number": "+251911000023",
					"loan_amount": 1000,
					"requested_amount": 1000,
					"status": "In Transition",
					"consent_id": cls.consent.name,
					"farmer_profile": cls.profile.name,
				}
			).insert(ignore_permissions=True)
			for bank in (cls.bank_a, cls.bank_b)
		]
		frappe.db.commit()

	@classmethod
	def tearDownClass(cls):
		frappe.set_user("Administrator")
		for app in cls.apps:
			if frappe.db.exists("A2C Loan Application", app.name):
				frappe.delete_doc("A2C Loan Application", app.name, force=True)
		frappe.delete_doc("A2C Farmer Profile", cls.profile.name, force=True)
		for bank in (cls.bank_a, cls.bank_b):
			frappe.db.delete("A2C Loan Product", {"bank": bank.name})
			frappe.delete_doc("A2C Participating Bank", bank.name, force=True)
		frappe.delete_doc("A2C Consent Request", cls.consent.name, force=True)
		frappe.db.commit()

	def setUp(self):
		frappe.set_user("Administrator")
		for bank in (self.bank_a, self.bank_b):
			frappe.cache().delete_keys(f"dashboard_stats:{bank.name}:*")

	def test_each_bank_counts_the_shared_farmer_once(self):
		"""Per-bank figures are unchanged: one applicant at each bank."""
		for bank in (self.bank_a, self.bank_b):
			stats = _compute_from_db(bank.name)
			self.assertEqual(stats["total_applications"], 1)
			self.assertEqual(stats["total_applicants"], 1)

	def test_platform_total_does_not_double_count_the_shared_farmer(self):
		"""The two per-bank 1s must not become a platform 2."""
		payload = get_dashboard_stats(None)
		by_bank = {row["bank"]: row for row in payload["by_bank"]}

		# Our two rows each contribute one applicant and one application...
		self.assertEqual(by_bank[self.bank_a.name]["total_applicants"], 1)
		self.assertEqual(by_bank[self.bank_b.name]["total_applicants"], 1)

		# ...but the same person, so the platform view gains one applicant while
		# gaining two applications. Other banks on the site contribute to both
		# figures, so compare the gap rather than absolute values.
		summed = sum(row["total_applicants"] for row in payload["by_bank"])
		self.assertLess(
			payload["stats"]["total_applicants"],
			summed,
			"platform applicants must be a distinct count, not the sum of per-bank counts",
		)

	def test_non_additive_counters_are_scalar_counters(self):
		"""A name in _NON_ADDITIVE_COUNTERS that is not a scalar counter is skipped silently."""
		self.assertTrue(_NON_ADDITIVE_COUNTERS.issubset(set(_SCALAR_COUNTERS)))
