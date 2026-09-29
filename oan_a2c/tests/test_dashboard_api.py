import json
import unittest
from unittest.mock import patch

import frappe
from werkzeug.test import EnvironBuilder
from werkzeug.wrappers import Request

from oan_a2c.a2c_marketplace.stages import get_stage_map
from oan_a2c.api.router import _load_openapi_spec, dispatch_rest_request
from oan_a2c.api.v1 import dashboard


class TestDashboardApi(unittest.TestCase):
	"""Public chart data: bucket semantics, scoping, and that no row carries personal data.

	Every figure is read with the fixture bank as the provider filter, so rows left
	behind by other suites cannot move the numbers asserted here.
	"""

	@classmethod
	def setUpClass(cls):
		frappe.set_user("Administrator")
		cls.suffix = frappe.generate_hash(length=6)
		cls.region = f"Dash Region {cls.suffix}"
		cls.woreda_a = f"Dash Woreda A {cls.suffix}"
		cls.woreda_b = f"Dash Woreda B {cls.suffix}"
		cls.pii_first_name = f"Pii{cls.suffix}"
		cls.pii_phone = "+251911" + str(int(cls.suffix, 16) % 1000000).zfill(6)
		cls.pii_field_value = f"secret-{cls.suffix}"
		cls.reason = f"Collateral insufficient {cls.suffix}"
		cls.created: list[tuple[str, str]] = []

		cls.bank = cls._insert(
			{
				"doctype": "A2C Participating Bank",
				"bank_name": f"Dash Bank {cls.suffix}",
				"bank_code": f"DASH_{cls.suffix}",
				"status": "Active",
				"entity_type": "Commercial Bank",
				"registered_email": f"dash_{cls.suffix}@test.com",
				"registered_phone": "+251911000000",
				"registered_region": "Addis Ababa",
				"registered_country": "Ethiopia",
				"kyc_document": "/private/files/test_kyc.pdf",
				"gro_name": "Test GRO",
				"ops_name": "Test Ops",
			}
		)
		stages = sorted(get_stage_map(cls.bank).items(), key=lambda s: s[1]["sequence"])
		in_transition = [sid for sid, s in stages if s["archetype_state"] == "In Transition"]
		cls.entry_stage, cls.later_stage = in_transition[0], in_transition[1]

		cls.consent_delivered = cls._consent(
			"Approved", delivered=1, fields=["Farmer Profile", "Land Holding"]
		)
		cls.consent_pending = cls._consent("Approved", delivered=0, fields=["Farmer Profile"])
		cls.consent_failed = cls._consent("Failed", delivered=0, fields=["Land Holding"])

		cls.profile_a = cls._profile(cls.woreda_a, cls.consent_delivered)
		cls.profile_b = cls._profile(cls.woreda_b, cls.consent_failed)

		cls.app_completed = cls._application(
			cls.profile_a, cls.consent_delivered, "Completed", None, 1000, 800
		)
		cls.app_rejected = cls._application(cls.profile_a, cls.consent_pending, "Rejected", None, 500, 0)
		cls.app_entry = cls._application(
			cls.profile_b, cls.consent_failed, "In Transition", cls.entry_stage, 300, 0
		)
		cls.app_later = cls._application(cls.profile_b, None, "In Transition", cls.later_stage, 200, 0)
		cls.app_private = cls._application(cls.profile_b, None, "Active", None, 9999, 0)

		cls._insert(
			{
				"doctype": "A2C Loan Application Audit Event",
				"loan_application": cls.app_rejected,
				"bank": cls.bank,
				"event_type": "Status Changed",
				"event_title": "Status Updated",
				"event_description": f"Changed to Rejected (Rejected)\nReason: {cls.reason}\nUpdated by: x@test.com",
			}
		)

	@classmethod
	def tearDownClass(cls):
		frappe.set_user("Administrator")
		for doctype, name in reversed(cls.created):
			frappe.db.delete(doctype, {"name": name})
			if doctype == "A2C Consent Request":
				frappe.db.delete("A2C Consent Data", {"parent": name})
		frappe.db.delete("A2C Loan Status Stage", {"bank": cls.bank})
		frappe.cache().delete_keys("a2c_dashboard_chart")

	def setUp(self):
		frappe.cache().delete_keys("a2c_dashboard_chart")
		frappe.cache().delete_keys("rl:dashboard_chart")
		frappe.set_user("Guest")
		frappe.local.response = frappe._dict()

	def tearDown(self):
		frappe.set_user("Administrator")

	# -- fixtures -----------------------------------------------------------

	@classmethod
	def _insert(cls, values: dict) -> str:
		doc = frappe.get_doc(values).insert(ignore_permissions=True, ignore_mandatory=True)
		cls.created.append((doc.doctype, doc.name))
		return doc.name

	@classmethod
	def _consent(cls, status: str, delivered: int, fields: list[str]) -> str:
		return cls._insert(
			{
				"doctype": "A2C Consent Request",
				"farmer": f"FARMER-{cls.suffix}",
				"farmer_fayda_id": f"FAYDA{cls.suffix}",
				"status": status,
				"websub_delivered": delivered,
				"requested_data_fields": [
					{"field_name": f, "field_value": cls.pii_field_value} for f in fields
				],
			}
		)

	@classmethod
	def _profile(cls, woreda: str, consent: str) -> str:
		return cls._insert(
			{
				"doctype": "A2C Farmer Profile",
				"first_name": cls.pii_first_name,
				"last_name": "Dash",
				"phone_number": cls.pii_phone,
				"region": cls.region,
				"woreda": woreda,
				"consent_id": consent,
			}
		)

	@classmethod
	def _application(cls, profile, consent, status, stage_id, requested, approved) -> str:
		name = cls._insert(
			{
				"doctype": "A2C Loan Application",
				"application_source": "Agent",
				"farmer_profile": profile,
				"bank": cls.bank,
				"consent_id": consent,
				"requested_amount": requested,
				"loan_amount": requested,
				"loan_product_name": f"Dash Product {cls.suffix}",
				"first_name": cls.pii_first_name,
				"phone_number": cls.pii_phone,
				"status": "Active",
			}
		)
		# Straight to the target state: the workflow is not under test here.
		frappe.db.set_value(
			"A2C Loan Application",
			name,
			{"status": status, "stage_id": stage_id, "approved_amount": approved},
			update_modified=False,
		)
		return name

	def _chart(self, chart_id: str, **filters):
		frappe.local.response = frappe._dict()
		res = dashboard.get_chart(chart_id=chart_id, provider=self.bank, **filters)
		self.assertEqual(res.get("status"), "success", res)
		return res["data"]

	# -- tests --------------------------------------------------------------

	def test_kpis_bucket_by_archetype_and_entry_stage(self):
		kpis = self._chart("a2cKpis")[0]
		self.assertEqual(kpis["providers_total"], 1)
		self.assertEqual(kpis["providers_onboarded"], 1)
		# The private Active draft is never counted.
		self.assertEqual(kpis["applications_total"], 4)
		self.assertEqual(kpis["loans_approved"], 1)
		self.assertEqual(kpis["loans_declined"], 1)
		self.assertEqual(kpis["loans_pending"], 1)
		self.assertEqual(kpis["applications_in_progress"], 1)
		self.assertEqual(kpis["loan_value_approved"], 800)
		self.assertEqual(kpis["loan_value_requested"], 2000)
		# Consents reach the provider through its applications.
		self.assertEqual(kpis["consent_requests"], 3)
		self.assertEqual(kpis["consents_approved"], 2)
		self.assertEqual(kpis["consents_declined"], 1)
		self.assertEqual(kpis["data_shares_delivered"], 1)
		self.assertEqual(kpis["data_shares_pending"], 1)
		self.assertEqual(kpis["data_shares_failed"], 1)
		self.assertEqual(kpis["records_shared"], 2)
		self.assertEqual(kpis["farmers_enrolled"], 2)

	def test_application_status_order_and_values(self):
		rows = self._chart("a2cApplicationStatus")
		self.assertEqual([r["status"] for r in rows], ["APPROVED", "IN_PROGRESS", "PENDING", "DECLINED"])
		self.assertEqual(rows[0]["approved_value"], 800)

	def test_location_filters_match_names_case_insensitively(self):
		by_woreda = {r["woreda"]: r for r in self._chart("a2cLocationSummary")}
		self.assertEqual(by_woreda[self.woreda_a]["loan_value"], 800)
		self.assertEqual(by_woreda[self.woreda_b]["applications"], 2)

		narrowed = self._chart(
			"a2cKpis", region=self.region.upper(), woreda=f" {self.woreda_a.lower()} ,Nowhere"
		)[0]
		self.assertEqual(narrowed["applications_total"], 2)
		self.assertEqual(narrowed["farmers_enrolled"], 1)

		elsewhere = self._chart("a2cKpis", region=f"Nowhere {self.suffix}")[0]
		self.assertEqual(elsewhere["applications_total"], 0)

	def test_map_series_carries_value_in_farmers_column(self):
		regions = self._chart("a2cLoansByRegion")
		self.assertEqual(regions, [{"region": self.region, "farmers": 800.0}])
		woredas = {r["woreda"]: r["farmers"] for r in self._chart("a2cLoansByWoreda")}
		self.assertEqual(woredas, {self.woreda_a: 800.0, self.woreda_b: 0.0})

	def test_decline_reason_read_from_audit_trail(self):
		with patch.object(dashboard, "_MIN_REASON_APPLICATIONS", 1):
			rows = self._chart("a2cDeclineReasons")
		self.assertEqual(rows, [{"reason": self.reason, "applications": 1, "requested_value": 500.0}])

	def test_rare_decline_reason_is_not_published_verbatim(self):
		"""Free text about one application could identify the farmer."""
		rows = self._chart("a2cDeclineReasons")
		self.assertEqual(rows, [{"reason": "Other reasons", "applications": 1, "requested_value": 500.0}])

	def test_cache_key_length_is_bounded(self):
		scope = dashboard.Scope(None, "r" * 2000, ",".join(f"w{i}" for i in range(400)))
		self.assertLess(len(scope.cache_key("a2cKpis")), 120)

	def test_data_shares_by_requested_field(self):
		shares = {r["dataset"]: r for r in self._chart("a2cDataShares")}
		self.assertEqual(shares["Farmer Profile"]["delivered"], 1)
		self.assertEqual(shares["Farmer Profile"]["pending"], 1)
		self.assertEqual(shares["Land Holding"]["failed"], 1)
		faults = self._chart("a2cDataShareFaults")
		self.assertEqual(
			[(f["provider"], f["dataset"]) for f in faults], [(f"DASH_{self.suffix}", "Land Holding")]
		)

	def test_no_chart_returns_personal_data(self):
		payload = json.dumps({chart: self._chart(chart) for chart in dashboard.CHARTS}, default=str)
		for secret in (
			self.pii_first_name,
			self.pii_phone,
			self.pii_field_value,
			f"FAYDA{self.suffix}",
			self.reason,
		):
			self.assertNotIn(secret, payload)
		for record in (self.app_completed, self.profile_a, self.consent_delivered):
			self.assertNotIn(record, payload)

	def test_unknown_chart_is_not_found(self):
		res = dashboard.get_chart(chart_id=f"nope{self.suffix}")
		self.assertEqual(res["status"], "error")
		self.assertEqual(res["code"], "NOT_FOUND")

	def test_results_are_cached_per_filter_set(self):
		first = self._chart("a2cKpis")[0]["applications_total"]
		frappe.db.set_value("A2C Loan Application", self.app_private, "status", "In Transition")
		try:
			self.assertEqual(self._chart("a2cKpis")[0]["applications_total"], first)
		finally:
			frappe.db.set_value("A2C Loan Application", self.app_private, "status", "Active")

	def test_guest_can_call_the_rest_route(self):
		builder = EnvironBuilder(
			path="/v1/charts/a2cKpis", method="GET", query_string={"provider": self.bank}
		)
		req = Request(builder.get_environ())
		frappe.local.request = req
		response = dispatch_rest_request(req)
		self.assertEqual(response.status_code, 200)
		body = json.loads(response.get_data(as_text=True))
		self.assertEqual(body["status"], "success")
		self.assertEqual(body["data"][0]["applications_total"], 4)

	def test_route_is_public_in_the_spec(self):
		"""`security: []` is what exempts the path from the JWT middleware."""
		operation = _load_openapi_spec()["paths"]["/v1/charts/{chart_id}"]["get"]
		self.assertEqual(operation["security"], [])
		self.assertEqual(operation["x-legacy-rpc-method"], "oan_a2c.api.v1.dashboard.get_chart")

	def test_middleware_exempts_only_the_templated_chart_path(self):
		"""A `<param>` in an exempt path matches exactly one segment, nothing wider."""
		from oan_a2c.api.middleware import PUBLIC_EXEMPT_PATHS, _is_exempt

		self.assertTrue(_is_exempt("/api/v1/charts/a2cKpis", PUBLIC_EXEMPT_PATHS))
		self.assertTrue(_is_exempt("/v1/charts/a2cKpis/", PUBLIC_EXEMPT_PATHS))
		self.assertFalse(_is_exempt("/api/v1/charts/a2cKpis/extra", PUBLIC_EXEMPT_PATHS))
		self.assertFalse(_is_exempt("/api/v1/charts/", PUBLIC_EXEMPT_PATHS))
		self.assertFalse(_is_exempt("/api/v1/chartsX/a2cKpis", PUBLIC_EXEMPT_PATHS))
		self.assertFalse(_is_exempt("/api/v1/loan-applications/APP-1", PUBLIC_EXEMPT_PATHS))
