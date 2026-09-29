"""Public, aggregate-only chart data for the OAN programme dashboards.

Serves the dashboard service contract the OAN dashboards use for every data
source: `GET /api/v1/charts/<chart_id>` returns a list of aggregate rows. The
rows are built for the A2C dashboard's panels, so column names follow that
dashboard rather than A2C's own field names.

Guest access is deliberate: the dashboards are public and hold no A2C
credentials. What keeps that safe is what the endpoint can return, not who may
call it:

- Rows are counts and sums only. No farmer name, phone, ID number, consent
  field value or document ever leaves this module.
- Reads run with `ignore_permissions=True` because a programme-wide total has
  to span every bank; bank scoping exists to stop one tenant reading another's
  records, and no record is returned here. The same tenant rule that hides a
  farmer's pre-submission `Active` draft from banks keeps it out of every figure.
- Results are cached per chart and filter set, and calls are rate limited per
  IP, so the public path cannot become a load path onto the transactional DB.

Location: A2C stores region and woreda as the names the farmer registry sent,
not P-codes, and has no zone. Rows therefore carry names; the dashboards map
them onto their boundary codes. Filters are names too, and both `region` and
`woreda` accept a comma-separated list: one boundary unit can match several
spellings, and a zone selection is sent as its woredas.
"""

import re
from collections import defaultdict

import frappe
from frappe import _
from frappe.query_builder import DocType
from frappe.query_builder.functions import Coalesce, Count, Sum
from frappe.utils import getdate
from pydantic import BaseModel, Field, field_validator
from pypika.functions import Lower, NullIf, Trim
from pypika.terms import Function

from oan_a2c.a2c_marketplace.stages import get_stage_map
from oan_a2c.api.utils import check_rate_limit, handle_api_errors, success_response, validate_request

CACHE_TTL_SECONDS = 900
# Per client IP. The dashboards' BFF calls on behalf of every viewer from one
# address, so this bounds abuse rather than normal use; the cache above absorbs
# repeated calls for the same chart and filters.
RATE_LIMIT_PER_MINUTE = 600
_CACHE_PREFIX = "a2c_dashboard_chart"
_MAX_NAMES = 500
_UNSPECIFIED = "Unspecified"
_REASON_NOT_RECORDED = "Reason not recorded"
_MAX_REASON_LENGTH = 120

# The dashboards bucket applications into four outcomes. They are derived from
# the archetype state, never from a stage label (tenant-defined free text; see
# stages.py). `In Transition` is split at the bank's entry stage: an application
# still sitting in it has not been picked up yet (PENDING), one past it is being
# assessed (IN_PROGRESS).
APPROVED = "APPROVED"
IN_PROGRESS = "IN_PROGRESS"
PENDING = "PENDING"
DECLINED = "DECLINED"
CANCELLED = "CANCELLED"
_OUTCOME_ORDER = {APPROVED: 1, IN_PROGRESS: 2, PENDING: 3, DECLINED: 4, CANCELLED: 5}
_ARCHETYPE_OUTCOME = {"Completed": APPROVED, "Rejected": DECLINED, "Cancelled": CANCELLED}

# The farmer's private pre-submission stage; excluded exactly as stats_cache and
# loan_application_scope_query exclude it for bank users.
_PRIVATE_APPLICATION_STATUS = "Active"

_CONSENT_OUTCOME = {
	"Approved": APPROVED,
	"Draft": PENDING,
	"Pending OTP": PENDING,
	"OTP Verified": PENDING,
	"Rejected": DECLINED,
	"Failed": DECLINED,
}
_CONSENT_ORDER = {APPROVED: 1, PENDING: 2, DECLINED: 3}

_PROVIDER_STATUS = {"Active": "ACTIVE", "In Review": "ONBOARDING", "Suspended": "SUSPENDED"}

# Registry data reaches A2C through the consent flow: an approved consent is a
# share request, the registry's websub delivery completes it, and a consent the
# registry could not fulfil is a failed share. The datasets are the fields the
# consent requested -- their names only, never their values.
DELIVERED = "DELIVERED"
FAILED = "FAILED"
_SHARE_FAULT = "Registry consent failed"

_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
_REASON_RE = re.compile(r"^Reason:\s*(.+)$", re.MULTILINE)

LoanApplication = DocType("A2C Loan Application")
FarmerProfile = DocType("A2C Farmer Profile")
ConsentRequest = DocType("A2C Consent Request")
ConsentData = DocType("A2C Consent Data")
AuditEvent = DocType("A2C Loan Application Audit Event")


class ChartFilterSchema(BaseModel):
	chart_id: str = Field(..., min_length=1, max_length=64)
	provider: str | None = Field(None, max_length=140)
	region: str | None = Field(None, max_length=2000)
	woreda: str | None = Field(None, max_length=8000)

	@field_validator("provider", "region", "woreda", mode="before")
	@classmethod
	def blank_means_all(cls, value):
		if value is None:
			return None
		value = str(value).strip()
		return None if value == "" or value.lower() == "all" else value


class Scope:
	"""The provider and location selection every panel is narrowed by."""

	def __init__(self, provider: str | None, region: str | None, woreda: str | None):
		self.provider = provider
		self.regions = _names(region)
		self.woredas = _names(woreda)

	def cache_key(self, chart_id: str) -> str:
		return ":".join(
			(_CACHE_PREFIX, chart_id, self.provider or "", ",".join(self.regions), ",".join(self.woredas))
		)


def _names(value: str | None) -> list[str]:
	names = sorted({_norm(n) for n in (value or "").split(",") if n.strip()})
	if len(names) > _MAX_NAMES:
		frappe.throw(_("Too many locations in one filter."), frappe.ValidationError)
	return names


def _norm(value) -> str:
	return " ".join(str(value or "").split()).lower()


def _label(value) -> str:
	return " ".join(str(value or "").split()) or _UNSPECIFIED


def _num(value) -> float:
	return float(value or 0)


# ---------------------------------------------------------------------------
# Scoped reads
# ---------------------------------------------------------------------------


def _geo(field_on_application, field_on_profile):
	"""The application's own location, falling back to its farmer's profile."""
	return Coalesce(NullIf(Trim(field_on_application), ""), NullIf(Trim(field_on_profile), ""))


def _apply_geo(query, region_term, woreda_term, scope: Scope):
	if scope.regions:
		query = query.where(Lower(Trim(region_term)).isin(scope.regions))
	if scope.woredas:
		query = query.where(Lower(Trim(woreda_term)).isin(scope.woredas))
	return query


def _application_base(scope: Scope):
	region = _geo(LoanApplication.region, FarmerProfile.region)
	woreda = _geo(LoanApplication.woreda, FarmerProfile.woreda)
	query = (
		frappe.qb.from_(LoanApplication)
		.left_join(FarmerProfile)
		.on(FarmerProfile.name == LoanApplication.farmer_profile)
		.where(LoanApplication.status != _PRIVATE_APPLICATION_STATUS)
	)
	if scope.provider:
		query = query.where(LoanApplication.bank == scope.provider)
	return _apply_geo(query, region, woreda, scope), region, woreda


def _application_facts(scope: Scope) -> list[dict]:
	"""Applications grouped by every dimension a panel slices on.

	One grouped read serves all application panels; the Python roll-ups below
	only ever see a few rows per bank, stage, place, product and month.
	"""
	query, region, woreda = _application_base(scope)
	month = Function("DATE_FORMAT", LoanApplication.creation, "%Y-%m-01")
	product = Coalesce(
		NullIf(Trim(LoanApplication.loan_product_name), ""), NullIf(Trim(LoanApplication.loan_type), "")
	)
	rows = (
		query.select(
			LoanApplication.bank.as_("bank"),
			LoanApplication.status.as_("status"),
			LoanApplication.stage_id.as_("stage_id"),
			region.as_("region"),
			woreda.as_("woreda"),
			product.as_("product"),
			month.as_("month"),
			Count("*").as_("applications"),
			Sum(Coalesce(LoanApplication.requested_amount, LoanApplication.loan_amount, 0)).as_("requested"),
			Sum(Coalesce(LoanApplication.approved_amount, 0)).as_("approved"),
		)
		.groupby(
			LoanApplication.bank,
			LoanApplication.status,
			LoanApplication.stage_id,
			region,
			woreda,
			product,
			month,
		)
		.run(as_dict=True)
	)
	entry_stages: dict[str, str | None] = {}
	for row in rows:
		row["outcome"] = _outcome(row, entry_stages)
		row["approved_value"] = _num(row.approved) if row["outcome"] == APPROVED else 0.0
	return rows


def _outcome(row, entry_stages: dict) -> str:
	if row.status in _ARCHETYPE_OUTCOME:
		return _ARCHETYPE_OUTCOME[row.status]
	bank = row.bank or ""
	if bank not in entry_stages:
		stage_map = get_stage_map(bank)
		entry_stages[bank] = next(
			(sid for sid, s in stage_map.items() if s.get("archetype_state") == "In Transition"), None
		)
	entry = entry_stages[bank]
	if not row.stage_id or (entry and row.stage_id == entry):
		return PENDING
	return IN_PROGRESS


def _consent_scope_query(scope: Scope):
	"""Consents narrowed by provider (through the applications that used them) and by
	the location of the farmer profile they were raised for."""
	query = (
		frappe.qb.from_(ConsentRequest)
		.left_join(FarmerProfile)
		.on(FarmerProfile.consent_id == ConsentRequest.name)
	)
	if scope.provider:
		used_by_provider = (
			frappe.qb.from_(LoanApplication)
			.select(LoanApplication.consent_id)
			.where(LoanApplication.bank == scope.provider)
			.where(LoanApplication.consent_id.isnotnull())
		)
		query = query.where(ConsentRequest.name.isin(used_by_provider))
	return _apply_geo(query, FarmerProfile.region, FarmerProfile.woreda, scope)


def _consent_status_counts(scope: Scope) -> dict[str, int]:
	rows = (
		_consent_scope_query(scope)
		.select(ConsentRequest.status.as_("status"), Count(ConsentRequest.name).distinct().as_("n"))
		.groupby(ConsentRequest.status)
		.run(as_dict=True)
	)
	counts: dict[str, int] = defaultdict(int)
	for row in rows:
		counts[_CONSENT_OUTCOME.get(row.status, PENDING)] += int(row.n)
	return counts


def _share_facts(scope: Scope) -> list[dict]:
	"""Share outcome per consent: delivered, pending or failed, with its field count."""
	fields = (
		frappe.qb.from_(ConsentData)
		.select(Count("*"))
		.where(ConsentData.parent == ConsentRequest.name)
		.where(ConsentData.parenttype == "A2C Consent Request")
	)
	rows = (
		_consent_scope_query(scope)
		.select(
			ConsentRequest.name.as_("consent"),
			ConsentRequest.status.as_("status"),
			ConsentRequest.websub_delivered.as_("delivered"),
			ConsentRequest.modified.as_("modified"),
			fields.as_("fields"),
		)
		.where(ConsentRequest.status.isin(["Approved", "Failed"]))
		.groupby(
			ConsentRequest.name,
			ConsentRequest.status,
			ConsentRequest.websub_delivered,
			ConsentRequest.modified,
		)
		.run(as_dict=True)
	)
	for row in rows:
		if row.status == "Failed":
			row["share"] = FAILED
		elif row.delivered:
			row["share"] = DELIVERED
		else:
			row["share"] = PENDING
	return rows


def _share_datasets(consents: list[str]) -> dict[str, list[str]]:
	"""consent -> names of the fields it requested. Values are never read."""
	datasets: dict[str, list[str]] = defaultdict(list)
	for chunk in _chunks(consents, 500):
		for row in frappe.get_all(
			"A2C Consent Data",
			filters={"parent": ["in", chunk], "parenttype": "A2C Consent Request"},
			fields=["parent", "field_name"],
			ignore_permissions=True,
		):
			datasets[row.parent].append(_label(row.field_name))
	return datasets


def _chunks(items: list, size: int):
	for i in range(0, len(items), size):
		yield items[i : i + size]


def _consent_banks(consents: list[str]) -> dict[str, str]:
	"""consent -> the bank of the first application that used it."""
	banks: dict[str, str] = {}
	for chunk in _chunks(consents, 500):
		for row in frappe.get_all(  # bank-scope-exempt: public cross-bank aggregate, returns bank ids only
			"A2C Loan Application",
			filters={"consent_id": ["in", chunk]},
			fields=["consent_id", "bank"],
			order_by="creation asc",
			ignore_permissions=True,
		):
			banks.setdefault(row.consent_id, row.bank)
	return banks


def _farmer_counts(scope: Scope) -> dict[tuple[str, str], int]:
	"""Enrolled farmers per (region, woreda) name."""
	query = frappe.qb.from_(FarmerProfile)
	if scope.provider:
		applied_to_provider = (
			frappe.qb.from_(LoanApplication)
			.select(LoanApplication.farmer_profile)
			.where(LoanApplication.bank == scope.provider)
			.where(LoanApplication.status != _PRIVATE_APPLICATION_STATUS)
			.where(LoanApplication.farmer_profile.isnotnull())
		)
		query = query.where(FarmerProfile.name.isin(applied_to_provider))
	query = _apply_geo(query, FarmerProfile.region, FarmerProfile.woreda, scope)
	region = NullIf(Trim(FarmerProfile.region), "")
	woreda = NullIf(Trim(FarmerProfile.woreda), "")
	rows = (
		query.select(region.as_("region"), woreda.as_("woreda"), Count("*").as_("n"))
		.groupby(region, woreda)
		.run(as_dict=True)
	)
	return {(row.region or "", row.woreda or ""): int(row.n) for row in rows}


def _providers(scope: Scope) -> list[dict]:
	filters = {"name": scope.provider} if scope.provider else {}
	return frappe.get_all(
		"A2C Participating Bank",
		filters=filters,
		fields=["name", "bank_name", "bank_code", "brand_name", "entity_type", "status", "creation"],
		order_by="bank_name asc",
		ignore_permissions=True,
	)


# ---------------------------------------------------------------------------
# Charts
# ---------------------------------------------------------------------------


def chart_kpis(scope: Scope) -> list[dict]:
	providers = _providers(scope)
	apps = _application_facts(scope)
	consents = _consent_status_counts(scope)
	shares = _share_facts(scope)
	delivered = [s for s in shares if s["share"] == DELIVERED]

	def apps_where(outcome):
		return sum(int(r.applications) for r in apps if r["outcome"] == outcome)

	return [
		{
			"providers_onboarded": sum(1 for p in providers if p.status == "Active"),
			"providers_onboarding": sum(1 for p in providers if p.status == "In Review"),
			"providers_total": len(providers),
			"consent_requests": sum(consents.values()),
			"consents_approved": consents.get(APPROVED, 0),
			"consents_pending": consents.get(PENDING, 0),
			"consents_declined": consents.get(DECLINED, 0),
			"applications_total": sum(int(r.applications) for r in apps),
			"applications_in_progress": apps_where(IN_PROGRESS),
			"loans_approved": apps_where(APPROVED),
			"loans_declined": apps_where(DECLINED),
			"loans_pending": apps_where(PENDING),
			"data_shares_total": len(shares),
			"data_shares_delivered": len(delivered),
			"data_shares_failed": sum(1 for s in shares if s["share"] == FAILED),
			"data_shares_pending": sum(1 for s in shares if s["share"] == PENDING),
			"records_shared": sum(int(s.fields or 0) for s in delivered),
			"loan_value_approved": sum(r["approved_value"] for r in apps),
			"loan_value_requested": sum(_num(r.requested) for r in apps),
			"farmers_enrolled": sum(_farmer_counts(scope).values()),
		}
	]


def chart_providers(scope: Scope) -> list[dict]:
	stats: dict[str, dict] = defaultdict(lambda: {"applications": 0, "loans_approved": 0, "loan_value": 0.0})
	for row in _application_facts(scope):
		entry = stats[row.bank or ""]
		entry["applications"] += int(row.applications)
		if row["outcome"] == APPROVED:
			entry["loans_approved"] += int(row.applications)
			entry["loan_value"] += row["approved_value"]

	shares = _share_facts(scope)
	consent_bank = _consent_banks([s.consent for s in shares]) if shares else {}
	consent_query = (
		_consent_scope_query(scope)
		.join(LoanApplication)
		.on(LoanApplication.consent_id == ConsentRequest.name)
	)
	if scope.provider:
		consent_query = consent_query.where(LoanApplication.bank == scope.provider)
	consent_rows = (
		consent_query.select(
			LoanApplication.bank.as_("bank"),
			ConsentRequest.status.as_("status"),
			Count(ConsentRequest.name).distinct().as_("n"),
		)
		.groupby(LoanApplication.bank, ConsentRequest.status)
		.run(as_dict=True)
	)
	consents: dict[str, dict] = defaultdict(lambda: {"requests": 0, "approved": 0})
	for row in consent_rows:
		consents[row.bank]["requests"] += int(row.n)
		if row.status == "Approved":
			consents[row.bank]["approved"] += int(row.n)
	faults: dict[str, int] = defaultdict(int)
	for share in shares:
		if share["share"] == FAILED and share.consent in consent_bank:
			faults[consent_bank[share.consent]] += 1

	rows = []
	for provider in _providers(scope):
		app_stats = stats.get(provider.name, {"applications": 0, "loans_approved": 0, "loan_value": 0.0})
		rows.append(
			{
				"short_name": provider.bank_code or provider.brand_name or provider.bank_name,
				"name": provider.bank_name,
				"provider_type": provider.entity_type or "Bank",
				"status": _PROVIDER_STATUS.get(provider.status, "ONBOARDING"),
				"integration": "",
				"onboarded_on": str(getdate(provider.creation)),
				"consent_requests": consents[provider.name]["requests"],
				"consents_approved": consents[provider.name]["approved"],
				"applications": app_stats["applications"],
				"loans_approved": app_stats["loans_approved"],
				"loan_value": app_stats["loan_value"],
				"share_faults": faults.get(provider.name, 0),
			}
		)
	rows.sort(key=lambda r: (-r["loan_value"], -r["applications"], r["name"] or ""))
	return rows


def _by_place(scope: Scope, level: str) -> list[dict]:
	"""Approved loan value per region or per (region, woreda), including places with
	enrolled farmers but no loans yet. The map reads its metric from `farmers`."""
	values: dict[tuple, float] = defaultdict(float)
	for (region, woreda), _n in _farmer_counts(scope).items():
		values[(region,) if level == "region" else (region, woreda)] += 0.0
	for row in _application_facts(scope):
		key = (row.region or "",) if level == "region" else (row.region or "", row.woreda or "")
		values[key] += row["approved_value"]

	rows = []
	for key, value in values.items():
		if not key[-1]:
			continue
		row = {"region": key[0], "farmers": value}
		if level == "woreda":
			row["woreda"] = key[1]
		rows.append(row)
	rows.sort(key=lambda r: -r["farmers"])
	return rows


def chart_loans_by_region(scope: Scope) -> list[dict]:
	return _by_place(scope, "region")


def chart_loans_by_woreda(scope: Scope) -> list[dict]:
	return _by_place(scope, "woreda")


def chart_location_summary(scope: Scope) -> list[dict]:
	summary: dict[tuple, dict] = {}

	def entry(region, woreda):
		return summary.setdefault(
			(region, woreda),
			{
				"region": region,
				"woreda": woreda,
				"farmers": 0,
				"applications": 0,
				"loans_approved": 0,
				"applications_in_progress": 0,
				"loans_pending": 0,
				"loans_declined": 0,
				"loan_value": 0.0,
			},
		)

	for (region, woreda), n in _farmer_counts(scope).items():
		if woreda:
			entry(region, woreda)["farmers"] += n
	outcome_column = {
		APPROVED: "loans_approved",
		IN_PROGRESS: "applications_in_progress",
		PENDING: "loans_pending",
		DECLINED: "loans_declined",
	}
	for row in _application_facts(scope):
		if not row.woreda:
			continue
		e = entry(row.region or "", row.woreda)
		e["applications"] += int(row.applications)
		if row["outcome"] in outcome_column:
			e[outcome_column[row["outcome"]]] += int(row.applications)
		e["loan_value"] += row["approved_value"]
	return sorted(summary.values(), key=lambda r: (-r["farmers"], -r["applications"]))


def chart_application_status(scope: Scope) -> list[dict]:
	buckets: dict[str, dict] = {}
	for row in _application_facts(scope):
		b = buckets.setdefault(
			row["outcome"],
			{"status": row["outcome"], "applications": 0, "requested_value": 0.0, "approved_value": 0.0},
		)
		b["applications"] += int(row.applications)
		b["requested_value"] += _num(row.requested)
		b["approved_value"] += row["approved_value"]
	return sorted(buckets.values(), key=lambda r: _OUTCOME_ORDER.get(r["status"], 9))


def chart_consent_status(scope: Scope) -> list[dict]:
	counts = _consent_status_counts(scope)
	rows = [{"status": status, "requests": n} for status, n in counts.items() if n]
	return sorted(rows, key=lambda r: _CONSENT_ORDER.get(r["status"], 9))


def chart_loan_products(scope: Scope) -> list[dict]:
	products: dict[str, dict] = {}
	for row in _application_facts(scope):
		name = _label(row.product)
		p = products.setdefault(
			name, {"product": name, "applications": 0, "loans_approved": 0, "loan_value": 0.0}
		)
		p["applications"] += int(row.applications)
		if row["outcome"] == APPROVED:
			p["loans_approved"] += int(row.applications)
			p["loan_value"] += row["approved_value"]
	return sorted(products.values(), key=lambda r: (-r["loan_value"], -r["applications"], r["product"]))


def chart_loan_trend(scope: Scope) -> list[dict]:
	months: dict[str, dict] = {}
	for row in _application_facts(scope):
		start = str(row.month)
		m = months.setdefault(
			start,
			{
				"month": _MONTHS[getdate(start).month - 1],
				"month_start": start,
				"applications": 0,
				"loans_approved": 0,
				"loan_value": 0.0,
			},
		)
		m["applications"] += int(row.applications)
		if row["outcome"] == APPROVED:
			m["loans_approved"] += int(row.applications)
			m["loan_value"] += row["approved_value"]
	return [months[k] for k in sorted(months)]


def chart_data_shares(scope: Scope) -> list[dict]:
	shares = _share_facts(scope)
	datasets = _share_datasets([s.consent for s in shares]) if shares else {}
	rows: dict[str, dict] = {}
	for share in shares:
		for dataset in datasets.get(share.consent) or [_UNSPECIFIED]:
			r = rows.setdefault(
				dataset,
				{"dataset": dataset, "shares": 0, "delivered": 0, "failed": 0, "pending": 0, "records": 0},
			)
			r["shares"] += 1
			r[share["share"].lower()] += 1
			if share["share"] == DELIVERED:
				r["records"] += 1
	return sorted(rows.values(), key=lambda r: (-r["shares"], r["dataset"]))


def chart_data_share_faults(scope: Scope) -> list[dict]:
	failed = [s for s in _share_facts(scope) if s["share"] == FAILED]
	if not failed:
		return []
	names = [s.consent for s in failed]
	datasets = _share_datasets(names)
	consent_bank = _consent_banks(names)
	codes = (
		{
			p.name: p.bank_code or p.bank_name
			for p in frappe.get_all(
				"A2C Participating Bank",
				filters={"name": ["in", list(set(consent_bank.values()))]},
				fields=["name", "bank_code", "bank_name"],
				ignore_permissions=True,
			)
		}
		if consent_bank
		else {}
	)

	faults: dict[tuple, dict] = {}
	for share in failed:
		provider = codes.get(consent_bank.get(share.consent, ""), _UNSPECIFIED)
		for dataset in datasets.get(share.consent) or [_UNSPECIFIED]:
			f = faults.setdefault(
				(provider, dataset),
				{
					"fault": _SHARE_FAULT,
					"provider": provider,
					"dataset": dataset,
					"records": 0,
					"last_seen": None,
				},
			)
			f["records"] += 1
			seen = str(getdate(share.modified))
			if not f["last_seen"] or seen > f["last_seen"]:
				f["last_seen"] = seen
	return sorted(faults.values(), key=lambda r: (-r["records"], r["provider"], r["dataset"]))


def chart_decline_reasons(scope: Scope) -> list[dict]:
	"""Reasons come from the audit trail: update_loan_status writes the reason a bank
	gave into the Rejected transition's event ("...\\nReason: <text>")."""
	query, _region, _woreda = _application_base(scope)
	declined = (
		query.select(
			LoanApplication.name.as_("name"),
			Coalesce(LoanApplication.requested_amount, LoanApplication.loan_amount, 0).as_("requested"),
		)
		.where(LoanApplication.status == "Rejected")
		.run(as_dict=True)
	)
	if not declined:
		return []

	reason_by_app: dict[str, str] = {}
	for chunk in _chunks([d.name for d in declined], 500):
		events = (
			frappe.qb.from_(AuditEvent)
			.select(AuditEvent.loan_application, AuditEvent.event_description, AuditEvent.creation)
			.where(AuditEvent.loan_application.isin(chunk))
			.where(AuditEvent.event_type == "Status Changed")
			.where(AuditEvent.event_description.like("%(Rejected)%"))
			.orderby(AuditEvent.creation)
			.run(as_dict=True)
		)
		for event in events:
			match = _REASON_RE.search(event.event_description or "")
			if match:
				# Latest rejection wins; events are read oldest first.
				reason_by_app[event.loan_application] = match.group(1).strip()[:_MAX_REASON_LENGTH]

	reasons: dict[str, dict] = {}
	for app in declined:
		reason = reason_by_app.get(app.name) or _REASON_NOT_RECORDED
		r = reasons.setdefault(reason, {"reason": reason, "applications": 0, "requested_value": 0.0})
		r["applications"] += 1
		r["requested_value"] += _num(app.requested)
	return sorted(reasons.values(), key=lambda r: (-r["applications"], r["reason"]))


def chart_filter_providers(_scope: Scope) -> list[dict]:
	"""Every provider, unscoped: the dropdown must keep offering every choice."""
	counts = {
		row.bank: int(row.n)
		for row in frappe.qb.from_(LoanApplication)
		.select(LoanApplication.bank.as_("bank"), Count("*").as_("n"))
		.where(LoanApplication.status != _PRIVATE_APPLICATION_STATUS)
		.groupby(LoanApplication.bank)
		.run(as_dict=True)
	}
	order = {"ACTIVE": 1, "ONBOARDING": 2}
	rows = [
		{
			"id": p.name,
			"short_name": p.bank_code or p.bank_name,
			"name": p.bank_name,
			"status": _PROVIDER_STATUS.get(p.status, "ONBOARDING"),
			"applications": counts.get(p.name, 0),
		}
		for p in _providers(Scope(None, None, None))
	]
	return sorted(rows, key=lambda r: (order.get(r["status"], 3), r["name"] or ""))


def chart_filter_locations(_scope: Scope) -> list[dict]:
	"""Every place A2C reaches, unscoped, as (region, woreda) names."""
	rows = [
		{"region_name": region, "woreda_name": woreda, "farmers": n}
		for (region, woreda), n in _farmer_counts(Scope(None, None, None)).items()
		if region
	]
	return sorted(rows, key=lambda r: (r["region_name"], r["woreda_name"]))


CHARTS = {
	"a2cKpis": chart_kpis,
	"a2cProviders": chart_providers,
	"a2cLoansByRegion": chart_loans_by_region,
	"a2cLoansByWoreda": chart_loans_by_woreda,
	"a2cLocationSummary": chart_location_summary,
	"a2cApplicationStatus": chart_application_status,
	"a2cConsentStatus": chart_consent_status,
	"a2cLoanProducts": chart_loan_products,
	"a2cLoanTrend": chart_loan_trend,
	"a2cDataShares": chart_data_shares,
	"a2cDataShareFaults": chart_data_share_faults,
	"a2cDeclineReasons": chart_decline_reasons,
	"a2cFilterProviders": chart_filter_providers,
	"a2cFilterLocations": chart_filter_locations,
}


@validate_request(ChartFilterSchema)
@handle_api_errors
def get_chart(**kwargs):
	"""Aggregate rows for one dashboard chart, narrowed by provider and location."""
	check_rate_limit(
		f"rl:dashboard_chart:{getattr(frappe.local, 'request_ip', 'guest')}",
		limit=RATE_LIMIT_PER_MINUTE,
		window=60,
	)
	chart_id = kwargs.get("chart_id")
	build = CHARTS.get(chart_id)
	if build is None:
		frappe.throw(_("Unknown chart: {0}").format(chart_id), frappe.DoesNotExistError)

	scope = Scope(kwargs.get("provider"), kwargs.get("region"), kwargs.get("woreda"))
	cache = frappe.cache()
	key = scope.cache_key(chart_id)
	rows = cache.get_value(key)
	if rows is None:
		rows = build(scope)
		cache.set_value(key, rows, expires_in_sec=CACHE_TTL_SECONDS)
	return success_response(data=rows)
