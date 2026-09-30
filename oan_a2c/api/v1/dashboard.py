"""Public, aggregate-only chart data for the OAN programme dashboards.

Serves the dashboard service contract the OAN dashboards use for every data
source: `GET /api/v1/charts/<chart_id>` returns a list of aggregate rows. The
rows are built for the A2C dashboard's panels, so column names follow that
dashboard rather than A2C's own field names.

Every chart is built from A2C Dashboard Snapshot, which the scheduler rebuilds
every 15 minutes (a2c_marketplace/dashboard_rollup.py). No request aggregates a
transactional table, and each response reports the snapshot's time in
`meta.as_of`.

Guest access is deliberate at this layer: the dashboards hold no A2C user, and
once the Kong gateway enforces authorization it is the gateway that checks the
dashboards' API key (see the spec's DashboardKeyAuth). What keeps the route safe
here is what it can return:

- Rows are counts and sums only. No farmer name, phone, ID number, consent
  field value or document ever reaches the snapshot, let alone a response.
- A decline reason is free text a bank officer typed; it is published only once
  enough applications share it (_MIN_REASON_APPLICATIONS).
- Results are cached per chart, filter set and snapshot, and calls are rate
  limited per IP.

Location: A2C stores region and woreda as the names the farmer registry sent,
not P-codes, and has no zone. Rows therefore carry names; the dashboards map
them onto their boundary codes. Filters are names too, and both `region` and
`woreda` accept a comma-separated list: one boundary unit can match several
spellings, and a zone selection is sent as its woredas.
"""

import hashlib
import json
from collections import defaultdict

import frappe
from frappe import _
from frappe.query_builder import DocType
from frappe.utils import getdate
from pydantic import BaseModel, Field, field_validator

from oan_a2c.a2c_marketplace import dashboard_rollup as rollup
from oan_a2c.a2c_marketplace.dashboard_rollup import (
	APPROVED,
	DECLINED,
	DELIVERED,
	FAILED,
	IN_PROGRESS,
	PENDING,
	REASON_NOT_RECORDED,
	UNSPECIFIED,
	label,
	norm,
)
from oan_a2c.api.utils import check_rate_limit, handle_api_errors, success_response, validate_request

CACHE_TTL_SECONDS = 900
# Per client IP. The dashboards' BFF calls on behalf of every viewer from one
# address, so this bounds abuse rather than normal use; the cache above absorbs
# repeated calls for the same chart and filters.
RATE_LIMIT_PER_MINUTE = 600
_CACHE_PREFIX = "a2c_dashboard_chart"
_MAX_NAMES = 500
_OTHER_REASONS = "Other reasons"
# A decline reason is free text a bank officer typed, and can name the farmer or
# their circumstances. It is published only once that many applications share it
# word for word, which a reason about one person never reaches; rarer reasons are
# counted under _OTHER_REASONS.
_MIN_REASON_APPLICATIONS = 5

_OUTCOME_ORDER = {APPROVED: 1, IN_PROGRESS: 2, PENDING: 3, DECLINED: 4, rollup.CANCELLED: 5}
_CONSENT_ORDER = {APPROVED: 1, PENDING: 2, DECLINED: 3}
_PROVIDER_STATUS = {"Active": "ACTIVE", "In Review": "ONBOARDING", "Suspended": "SUSPENDED"}
_SHARE_FAULT = "Registry consent failed"
_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")

# Families whose figures are distinct counts. Their programme-wide rows carry an
# empty bank; with a provider selected, that provider's own rows are read.
_DISTINCT_FAMILIES = {"farmer", "consent", "share", "share_dataset"}

Snapshot = DocType(rollup.SNAPSHOT)


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
		self._snapshot_date = None

	def cache_key(self, chart_id: str, as_of=None) -> str:
		# Hashed so a caller-supplied filter cannot make an arbitrarily long key.
		# The snapshot time is part of it, so a refresh is a clean cut-over.
		selection = "|".join(
			(self.provider or "", ",".join(self.regions), ",".join(self.woredas), str(as_of or ""))
		)
		return f"{_CACHE_PREFIX}:{chart_id}:{hashlib.sha256(selection.encode()).hexdigest()}"

	@property
	def snapshot_date(self):
		if self._snapshot_date is None:
			latest = (
				frappe.qb.from_(Snapshot)
				.select(Snapshot.snapshot_date)
				.orderby(Snapshot.snapshot_date, order=frappe.qb.desc)
				.limit(1)
				.run()
			)
			self._snapshot_date = latest[0][0] if latest else False
		return self._snapshot_date


UNSCOPED = (None, None, None)


def _names(value: str | None) -> list[str]:
	names = sorted({norm(n) for n in (value or "").split(",") if n.strip()})
	if len(names) > _MAX_NAMES:
		frappe.throw(_("Too many locations in one filter."), frappe.ValidationError)
	return names


def _num(value) -> float:
	return float(value or 0)


# ---------------------------------------------------------------------------
# Reading the snapshot
# ---------------------------------------------------------------------------


def _rows(family: str, scope: Scope, by_provider: bool = True) -> list[dict]:
	"""Snapshot rows of one family, narrowed by the scope's provider and places."""
	if not scope.snapshot_date:
		return []
	query = (
		frappe.qb.from_(Snapshot)
		.select("*")
		.where(Snapshot.snapshot_date == scope.snapshot_date)
		.where(Snapshot.family == family)
	)
	if family in _DISTINCT_FAMILIES:
		query = query.where(Snapshot.bank == ((scope.provider or "") if by_provider else ""))
	elif scope.provider and by_provider:
		query = query.where(Snapshot.bank == scope.provider)
	if scope.regions:
		query = query.where(Snapshot.region_key.isin(scope.regions))
	if scope.woredas:
		query = query.where(Snapshot.woreda_key.isin(scope.woredas))
	return query.run(as_dict=True)


def _application_facts(scope: Scope) -> list[dict]:
	return _rows("application", scope)


def _consent_status_counts(scope: Scope) -> dict[str, int]:
	counts: dict[str, int] = defaultdict(int)
	for row in _rows("consent", scope):
		counts[row.outcome] += int(row.total)
	return counts


def _farmer_counts(scope: Scope) -> dict[tuple[str, str], int]:
	"""Enrolled farmers per (region, woreda) name."""
	counts: dict[tuple[str, str], int] = defaultdict(int)
	for row in _rows("farmer", scope):
		counts[(row.region, row.woreda)] += int(row.total)
	return counts


def _providers(scope: Scope) -> list[dict]:
	providers = []
	for row in _rows("provider", Scope(*UNSCOPED) if not scope.provider else _provider_only(scope)):
		details = json.loads(row.details or "{}")
		providers.append(frappe._dict(name=row.bank, **details))
	return sorted(providers, key=lambda p: p.bank_name or "")


def _provider_only(scope: Scope) -> Scope:
	"""Provider rows have no place; narrow them by provider alone."""
	only = Scope(scope.provider, None, None)
	only._snapshot_date = scope.snapshot_date
	return only


def _share_counts(scope: Scope) -> dict[str, dict]:
	"""Per share outcome: number of shares and the fields they carried."""
	counts: dict[str, dict] = defaultdict(lambda: {"shares": 0, "fields": 0})
	for row in _rows("share", scope):
		counts[row.outcome]["shares"] += int(row.total)
		counts[row.outcome]["fields"] += int(row.records)
	return counts


# ---------------------------------------------------------------------------
# Charts
# ---------------------------------------------------------------------------


def chart_kpis(scope: Scope) -> list[dict]:
	providers = _providers(scope)
	apps = _application_facts(scope)
	consents = _consent_status_counts(scope)
	shares = _share_counts(scope)

	def apps_where(outcome):
		return sum(int(r.total) for r in apps if r.outcome == outcome)

	return [
		{
			"providers_onboarded": sum(1 for p in providers if p.status == "Active"),
			"providers_onboarding": sum(1 for p in providers if p.status == "In Review"),
			"providers_total": len(providers),
			"consent_requests": sum(consents.values()),
			"consents_approved": consents.get(APPROVED, 0),
			"consents_pending": consents.get(PENDING, 0),
			"consents_declined": consents.get(DECLINED, 0),
			"applications_total": sum(int(r.total) for r in apps),
			"applications_in_progress": apps_where(IN_PROGRESS),
			"loans_approved": apps_where(APPROVED),
			"loans_declined": apps_where(DECLINED),
			"loans_pending": apps_where(PENDING),
			"data_shares_total": sum(c["shares"] for c in shares.values()),
			"data_shares_delivered": shares[DELIVERED]["shares"],
			"data_shares_failed": shares[FAILED]["shares"],
			"data_shares_pending": shares[PENDING]["shares"],
			"records_shared": shares[DELIVERED]["fields"],
			"loan_value_approved": sum(_num(r.approved_value) for r in apps),
			"loan_value_requested": sum(_num(r.requested_value) for r in apps),
			"farmers_enrolled": sum(_farmer_counts(scope).values()),
		}
	]


def chart_providers(scope: Scope) -> list[dict]:
	stats: dict[str, dict] = defaultdict(lambda: {"applications": 0, "loans_approved": 0, "loan_value": 0.0})
	for row in _application_facts(scope):
		entry = stats[row.bank or ""]
		entry["applications"] += int(row.total)
		if row.outcome == APPROVED:
			entry["loans_approved"] += int(row.total)
			entry["loan_value"] += _num(row.approved_value)

	# Per-bank consent rows: every consent a bank's applications used.
	consents: dict[str, dict] = defaultdict(lambda: {"requests": 0, "approved": 0})
	providers = _providers(scope)
	for provider in providers:
		for row in _rows("consent", _for_bank(scope, provider.name)):
			consents[provider.name]["requests"] += int(row.total)
			if row.outcome == APPROVED:
				consents[provider.name]["approved"] += int(row.total)

	# A failed share is attributed to the bank of the first application that used it.
	faults: dict[str, int] = defaultdict(int)
	for row in _rows("share", scope):
		if row.outcome == FAILED and row.first_bank:
			faults[row.first_bank] += int(row.total)

	rows = []
	for provider in providers:
		app_stats = stats.get(provider.name, {"applications": 0, "loans_approved": 0, "loan_value": 0.0})
		rows.append(
			{
				"short_name": provider.bank_code or provider.brand_name or provider.bank_name,
				"name": provider.bank_name,
				"provider_type": provider.entity_type or "Bank",
				"status": _PROVIDER_STATUS.get(provider.status, "ONBOARDING"),
				"integration": "",
				"onboarded_on": provider.onboarded_on,
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


def _for_bank(scope: Scope, bank: str) -> Scope:
	"""The same places, one bank."""
	narrowed = Scope(bank, None, None)
	narrowed.regions, narrowed.woredas = scope.regions, scope.woredas
	narrowed._snapshot_date = scope.snapshot_date
	return narrowed


def _by_place(scope: Scope, level: str) -> list[dict]:
	"""Approved loan value per region or per (region, woreda), including places with
	enrolled farmers but no loans yet. The map reads its metric from `farmers`."""
	values: dict[tuple, float] = defaultdict(float)
	for (region, woreda), _n in _farmer_counts(scope).items():
		values[(region,) if level == "region" else (region, woreda)] += 0.0
	for row in _application_facts(scope):
		key = (row.region or "",) if level == "region" else (row.region or "", row.woreda or "")
		values[key] += _num(row.approved_value)

	rows = []
	for key, value in values.items():
		if not key[-1]:
			continue
		row = {"region": key[0], "farmers": value}
		if level == "woreda":
			row["woreda"] = key[1]
		rows.append(row)
	rows.sort(key=lambda r: (-r["farmers"], r["region"], r.get("woreda", "")))
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
		e["applications"] += int(row.total)
		if row.outcome in outcome_column:
			e[outcome_column[row.outcome]] += int(row.total)
		e["loan_value"] += _num(row.approved_value)
	return sorted(summary.values(), key=lambda r: (-r["farmers"], -r["applications"]))


def chart_application_status(scope: Scope) -> list[dict]:
	buckets: dict[str, dict] = {}
	for row in _application_facts(scope):
		b = buckets.setdefault(
			row.outcome,
			{"status": row.outcome, "applications": 0, "requested_value": 0.0, "approved_value": 0.0},
		)
		b["applications"] += int(row.total)
		b["requested_value"] += _num(row.requested_value)
		b["approved_value"] += _num(row.approved_value)
	return sorted(buckets.values(), key=lambda r: _OUTCOME_ORDER.get(r["status"], 9))


def chart_consent_status(scope: Scope) -> list[dict]:
	counts = _consent_status_counts(scope)
	rows = [{"status": status, "requests": n} for status, n in counts.items() if n]
	return sorted(rows, key=lambda r: _CONSENT_ORDER.get(r["status"], 9))


def chart_loan_products(scope: Scope) -> list[dict]:
	products: dict[str, dict] = {}
	for row in _application_facts(scope):
		name = label(row.product)
		p = products.setdefault(
			name, {"product": name, "applications": 0, "loans_approved": 0, "loan_value": 0.0}
		)
		p["applications"] += int(row.total)
		if row.outcome == APPROVED:
			p["loans_approved"] += int(row.total)
			p["loan_value"] += _num(row.approved_value)
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
		m["applications"] += int(row.total)
		if row.outcome == APPROVED:
			m["loans_approved"] += int(row.total)
			m["loan_value"] += _num(row.approved_value)
	return [months[k] for k in sorted(months)]


def chart_data_shares(scope: Scope) -> list[dict]:
	rows: dict[str, dict] = {}
	for share in _rows("share_dataset", scope):
		r = rows.setdefault(
			share.dataset,
			{"dataset": share.dataset, "shares": 0, "delivered": 0, "failed": 0, "pending": 0, "records": 0},
		)
		n = int(share.total)
		r["shares"] += n
		r[share.outcome.lower()] += n
		if share.outcome == DELIVERED:
			r["records"] += n
	return sorted(rows.values(), key=lambda r: (-r["shares"], r["dataset"]))


def chart_data_share_faults(scope: Scope) -> list[dict]:
	failed = [s for s in _rows("share_dataset", scope) if s.outcome == FAILED]
	if not failed:
		return []
	codes = {p.name: p.bank_code or p.bank_name for p in _providers(Scope(*UNSCOPED))}

	faults: dict[tuple, dict] = {}
	for share in failed:
		provider = codes.get(share.first_bank, UNSPECIFIED)
		f = faults.setdefault(
			(provider, share.dataset),
			{
				"fault": _SHARE_FAULT,
				"provider": provider,
				"dataset": share.dataset,
				"records": 0,
				"last_seen": None,
			},
		)
		f["records"] += int(share.total)
		seen = str(share.last_seen) if share.last_seen else None
		if seen and (not f["last_seen"] or seen > f["last_seen"]):
			f["last_seen"] = seen
	return sorted(faults.values(), key=lambda r: (-r["records"], r["provider"], r["dataset"]))


def chart_decline_reasons(scope: Scope) -> list[dict]:
	"""Only a reason shared by _MIN_REASON_APPLICATIONS applications is published
	verbatim; the count is taken over the current selection."""
	declined = _rows("decline", scope)
	shared_by: dict[str, int] = defaultdict(int)
	for row in declined:
		shared_by[row.reason] += int(row.total)

	reasons: dict[str, dict] = {}
	for row in declined:
		reason = row.reason or REASON_NOT_RECORDED
		if reason != REASON_NOT_RECORDED and shared_by[row.reason] < _MIN_REASON_APPLICATIONS:
			reason = _OTHER_REASONS
		r = reasons.setdefault(reason, {"reason": reason, "applications": 0, "requested_value": 0.0})
		r["applications"] += int(row.total)
		r["requested_value"] += _num(row.requested_value)
	return sorted(reasons.values(), key=lambda r: (-r["applications"], r["reason"]))


def chart_filter_providers(scope: Scope) -> list[dict]:
	"""Every provider, unscoped: the dropdown must keep offering every choice."""
	unscoped = Scope(*UNSCOPED)
	unscoped._snapshot_date = scope.snapshot_date
	counts: dict[str, int] = defaultdict(int)
	for row in _rows("application", unscoped):
		counts[row.bank] += int(row.total)
	order = {"ACTIVE": 1, "ONBOARDING": 2}
	rows = [
		{
			"id": p.name,
			"short_name": p.bank_code or p.bank_name,
			"name": p.bank_name,
			"status": _PROVIDER_STATUS.get(p.status, "ONBOARDING"),
			"applications": counts.get(p.name, 0),
		}
		for p in _providers(unscoped)
	]
	return sorted(rows, key=lambda r: (order.get(r["status"], 3), r["name"] or ""))


def chart_filter_locations(scope: Scope) -> list[dict]:
	"""Every place A2C reaches, unscoped, as (region, woreda) names."""
	unscoped = Scope(*UNSCOPED)
	unscoped._snapshot_date = scope.snapshot_date
	rows = [
		{"region_name": region, "woreda_name": woreda, "farmers": n}
		for (region, woreda), n in _farmer_counts(unscoped).items()
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
	as_of = rollup.as_of()
	cache = frappe.cache()
	key = scope.cache_key(chart_id, as_of)
	rows = cache.get_value(key)
	if rows is None:
		rows = build(scope)
		cache.set_value(key, rows, expires_in_sec=CACHE_TTL_SECONDS)
	return success_response(data=rows, meta={"as_of": str(as_of) if as_of else None})
