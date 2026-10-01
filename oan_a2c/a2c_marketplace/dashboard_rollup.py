"""Dashboard rollup: the only reader of A2C's transactional tables for the dashboards.

The public dashboard charts (api/v1/dashboard.py) never aggregate loan
applications, consents or farmer profiles on a request. The scheduler folds
them into A2C Dashboard Snapshot every 15 minutes and the charts are built from
that table alone:

- Today's rows are replaced on every refresh; earlier days stay as they were at
  midnight, the only record of past figures.
- A2C has no immutable event stream to count by day -- an application's outcome
  moves until it is decided, and trends are cohorts by the month it was lodged --
  so every refresh rebuilds the whole snapshot. At A2C's volume that is a
  handful of grouped reads.
- Only counts, sums and maxima are stored, by bank, place and the other
  dimensions the charts slice on, so any filter combination stays exact. Figures
  that are distinct counts (farmers, consents, shares) do not add up across
  banks, so those families also carry a programme-wide row with an empty bank.

This is the same shape as the grievance service's rollups (oan_grievance_service
services/dashboard_rollup.py): scheduled refresh, database lock, as_of.
"""

import json
from collections import defaultdict

import frappe
from frappe.query_builder import DocType
from frappe.query_builder.functions import Coalesce, Count, Sum
from frappe.utils import getdate, now_datetime
from pypika.functions import NullIf, Trim
from pypika.terms import Function

from oan_a2c.a2c_marketplace.stages import get_stage_map

SNAPSHOT = "A2C Dashboard Snapshot"
AS_OF_DEFAULT = "a2c_dashboard_as_of"
LOCK_KEY = "a2c_dashboard_rollup"

APPROVED = "APPROVED"
IN_PROGRESS = "IN_PROGRESS"
PENDING = "PENDING"
DECLINED = "DECLINED"
CANCELLED = "CANCELLED"
DELIVERED = "DELIVERED"
FAILED = "FAILED"

UNSPECIFIED = "Unspecified"
REASON_NOT_RECORDED = "Reason not recorded"
MAX_REASON_LENGTH = 120

# The dashboards bucket applications into four outcomes. They are derived from
# the archetype state, never from a stage label (tenant-defined free text; see
# stages.py). `In Transition` is split at the bank's entry stage: an application
# still sitting in it has not been picked up yet (PENDING), one past it is being
# assessed (IN_PROGRESS).
_ARCHETYPE_OUTCOME = {"Completed": APPROVED, "Rejected": DECLINED, "Cancelled": CANCELLED}

# The farmer's private pre-submission stage; excluded exactly as stats_cache and
# loan_application_scope_query exclude it for bank users.
PRIVATE_APPLICATION_STATUS = "Active"

CONSENT_OUTCOME = {
	"Approved": APPROVED,
	"Draft": PENDING,
	"Pending OTP": PENDING,
	"OTP Verified": PENDING,
	"Rejected": DECLINED,
	"Failed": DECLINED,
}

LoanApplication = DocType("A2C Loan Application")
FarmerProfile = DocType("A2C Farmer Profile")
ConsentRequest = DocType("A2C Consent Request")
ConsentData = DocType("A2C Consent Data")
AuditEvent = DocType("A2C Loan Application Audit Event")

FIELDS = (
	"family",
	"bank",
	"first_bank",
	"region",
	"woreda",
	"region_key",
	"woreda_key",
	"outcome",
	"product",
	"month",
	"dataset",
	"reason",
	"total",
	"records",
	"requested_value",
	"approved_value",
	"last_seen",
	"details",
)

_REASON_PREFIX = "Reason:"


def norm(value) -> str:
	"""How a place name is matched: lower-cased, whitespace collapsed."""
	return " ".join(str(value or "").split()).lower()


def label(value) -> str:
	return " ".join(str(value or "").split()) or UNSPECIFIED


def refresh():
	"""Rebuild today's snapshot. Returns what it did, or None if a refresh was already running."""
	# A database named lock: held by this connection, so a worker that dies
	# mid-run releases it with its connection instead of blocking later runs.
	lock_name = f"{frappe.local.site}:{LOCK_KEY}"
	if not frappe.db.sql("SELECT GET_LOCK(%s, 0)", lock_name)[0][0]:
		frappe.logger().info("A2C dashboard rollup skipped: another refresh is running")
		return None
	try:
		now = now_datetime()
		today = getdate(now)
		# Consents feed two families; read them once.
		consents = _consent_facts()
		rows = [
			*_application_rows(),
			*_farmer_rows(),
			*_consent_rows(consents),
			*_share_rows(consents),
			*_decline_rows(),
			*_provider_rows(),
		]
		frappe.db.delete(SNAPSHOT, {"snapshot_date": today})
		_insert(today, now, rows)
		frappe.db.set_default(AS_OF_DEFAULT, str(now))
		return {"as_of": now, "rows": len(rows)}
	finally:
		frappe.db.sql("SELECT RELEASE_LOCK(%s)", lock_name)


def as_of():
	"""When the snapshot was last refreshed, or None if it never has been."""
	value = frappe.db.get_default(AS_OF_DEFAULT)
	return frappe.utils.get_datetime(value) if value else None


def ensure_built():
	"""after_migrate: queue a first build on a site that has none yet."""
	if frappe.flags.in_test or frappe.db.exists(SNAPSHOT):
		return
	frappe.enqueue(
		"oan_a2c.a2c_marketplace.dashboard_rollup.refresh",
		queue="long",
		job_id=LOCK_KEY,
		deduplicate=True,
		enqueue_after_commit=True,
	)


def _row(family, **values):
	row = dict.fromkeys(FIELDS)
	row.update(family=family, **values)
	for text in ("bank", "first_bank", "region", "woreda", "region_key", "woreda_key"):
		row[text] = row[text] or ""
	for number in ("total", "records"):
		row[number] = int(row[number] or 0)
	for number in ("requested_value", "approved_value"):
		row[number] = float(row[number] or 0)
	return row


def _place(region, woreda) -> dict:
	region = " ".join(str(region or "").split())
	woreda = " ".join(str(woreda or "").split())
	return {"region": region, "woreda": woreda, "region_key": norm(region), "woreda_key": norm(woreda)}


def _insert(today, now, rows):
	if not rows:
		return
	frappe.db.bulk_insert(
		SNAPSHOT,
		("name", "creation", "modified", "owner", "modified_by", "snapshot_date", *FIELDS),
		[
			(
				frappe.generate_hash(length=12),
				now,
				now,
				"Administrator",
				"Administrator",
				today,
				*(row[f] for f in FIELDS),
			)
			for row in rows
		],
	)


def _chunks(items: list, size: int = 500):
	for i in range(0, len(items), size):
		yield items[i : i + size]


# Families
# --------
# Every read below runs unscoped and with ignore_permissions: a programme-wide
# total has to span every bank. Bank scoping stops one tenant reading another's
# records; no record leaves this module, only the aggregates it writes.


def _geo(field_on_application, field_on_profile):
	"""The application's own location, falling back to its farmer's profile."""
	return Coalesce(NullIf(Trim(field_on_application), ""), NullIf(Trim(field_on_profile), ""))


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


def _application_rows() -> list[dict]:
	"""Applications by bank, outcome, place, product and the month they were lodged."""
	region = _geo(LoanApplication.region, FarmerProfile.region)
	woreda = _geo(LoanApplication.woreda, FarmerProfile.woreda)
	month = Function("DATE_FORMAT", LoanApplication.creation, "%Y-%m-01")
	product = Coalesce(
		NullIf(Trim(LoanApplication.loan_product_name), ""), NullIf(Trim(LoanApplication.loan_type), "")
	)
	rows = (
		frappe.qb.from_(LoanApplication)
		.left_join(FarmerProfile)
		.on(FarmerProfile.name == LoanApplication.farmer_profile)
		.where(LoanApplication.status != PRIVATE_APPLICATION_STATUS)
		.select(
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

	entry_stages: dict = {}
	merged: dict[tuple, dict] = {}
	for row in rows:
		outcome = _outcome(row, entry_stages)
		key = (row.bank or "", outcome, row.region or "", row.woreda or "", row.product or "", str(row.month))
		target = merged.setdefault(
			key,
			_row(
				"application",
				bank=row.bank,
				outcome=outcome,
				product=row.product or "",
				month=row.month,
				**_place(row.region, row.woreda),
			),
		)
		target["total"] += int(row.applications)
		target["requested_value"] += float(row.requested or 0)
		if outcome == APPROVED:
			target["approved_value"] += float(row.approved or 0)
	return list(merged.values())


def _farmer_rows() -> list[dict]:
	"""Enrolled farmers per place, programme-wide and per bank they applied to."""
	places = {
		p.name: _place(p.region, p.woreda)
		for p in frappe.get_all(
			"A2C Farmer Profile", fields=["name", "region", "woreda"], ignore_permissions=True
		)
	}
	counts: dict[tuple, int] = defaultdict(int)
	for place in places.values():
		counts[("", place["region_key"], place["woreda_key"])] += 1
	applied = (
		frappe.qb.from_(LoanApplication)
		.select(LoanApplication.bank, LoanApplication.farmer_profile)
		.where(LoanApplication.status != PRIVATE_APPLICATION_STATUS)
		.where(LoanApplication.farmer_profile.isnotnull())
		.distinct()
		.run(as_dict=True)
	)
	for a in applied:
		place = places.get(a.farmer_profile)
		if place and a.bank:
			counts[(a.bank, place["region_key"], place["woreda_key"])] += 1

	# A key is a place's normalised name; show the first spelling seen for it.
	shown = {}
	for place in places.values():
		shown.setdefault((place["region_key"], place["woreda_key"]), place)
	return [_row("farmer", bank=bank, total=n, **shown[(rk, wk)]) for (bank, rk, wk), n in counts.items()]


def _consent_facts() -> list[dict]:
	"""One row per consent: status, place (its farmer profile's), fields, and the banks that used it."""
	consents = frappe.get_all(
		"A2C Consent Request",
		fields=["name", "status", "websub_delivered", "modified"],
		ignore_permissions=True,
	)
	if not consents:
		return []
	names = [c.name for c in consents]

	places: dict[str, tuple] = {}
	fields: dict[str, list[str]] = defaultdict(list)
	banks: dict[str, list[str]] = defaultdict(list)
	for chunk in _chunks(names):
		for p in frappe.get_all(
			"A2C Farmer Profile",
			filters={"consent_id": ["in", chunk]},
			fields=["consent_id", "region", "woreda"],
			order_by="creation asc",
			ignore_permissions=True,
		):
			places.setdefault(p.consent_id, (p.region, p.woreda))
		for d in frappe.get_all(
			"A2C Consent Data",
			filters={"parent": ["in", chunk], "parenttype": "A2C Consent Request"},
			fields=["parent", "field_name"],
			ignore_permissions=True,
		):
			fields[d.parent].append(label(d.field_name))
		for a in frappe.get_all(  # bank-scope-exempt: programme-wide rollup, writes bank ids and counts only
			"A2C Loan Application",
			filters={"consent_id": ["in", chunk]},
			fields=["consent_id", "bank"],
			order_by="creation asc",
			ignore_permissions=True,
		):
			if a.bank and a.bank not in banks[a.consent_id]:
				banks[a.consent_id].append(a.bank)

	for c in consents:
		c["place"] = _place(*places.get(c.name, (None, None)))
		c["fields"] = fields.get(c.name, [])
		c["banks"] = banks.get(c.name, [])
	return consents


def _consent_rows(consents: list[dict]) -> list[dict]:
	"""Consent requests by outcome and place, programme-wide and per bank that used them."""
	counts: dict[tuple, int] = defaultdict(int)
	places: dict[tuple, dict] = {}
	for c in consents:
		outcome = CONSENT_OUTCOME.get(c.status, PENDING)
		for bank in ("", *c["banks"]):
			key = (bank, outcome, c["place"]["region_key"], c["place"]["woreda_key"])
			counts[key] += 1
			places.setdefault(key, c["place"])
	return [
		_row("consent", bank=bank, outcome=outcome, total=n, **places[(bank, outcome, rk, wk)])
		for (bank, outcome, rk, wk), n in counts.items()
	]


def _share_rows(consents: list[dict]) -> list[dict]:
	"""Registry data shares: approved consents are share requests, failed ones failed shares.

	`share` rows count shares and the fields they carried; `share_dataset` rows
	split them by requested field (its name only, never a value). `first_bank` is
	the bank of the first application that used the consent, which a fault is
	attributed to.
	"""
	shares: dict[tuple, dict] = {}
	datasets: dict[tuple, dict] = {}
	for c in consents:
		if c.status not in ("Approved", "Failed"):
			continue
		outcome = FAILED if c.status == "Failed" else DELIVERED if c.websub_delivered else PENDING
		first_bank = c["banks"][0] if c["banks"] else ""
		seen = getdate(c.modified)
		place = c["place"]
		for bank in ("", *c["banks"]):
			key = (bank, outcome, first_bank, place["region_key"], place["woreda_key"])
			s = shares.setdefault(
				key, _row("share", bank=bank, outcome=outcome, first_bank=first_bank, **place)
			)
			s["total"] += 1
			s["records"] += len(c["fields"])
			for dataset in c["fields"] or [UNSPECIFIED]:
				d = datasets.setdefault(
					(*key, dataset),
					_row(
						"share_dataset",
						bank=bank,
						outcome=outcome,
						first_bank=first_bank,
						dataset=dataset,
						**place,
					),
				)
				d["total"] += 1
				if not d["last_seen"] or seen > d["last_seen"]:
					d["last_seen"] = seen
	return [*shares.values(), *datasets.values()]


def _decline_rows() -> list[dict]:
	"""Declined applications by bank, place and the reason the bank gave.

	Reasons come from the audit trail: update_loan_status writes the reason into the
	Rejected transition's event ("...\\nReason: <text>"); the latest rejection wins.
	The reason is stored verbatim here and only published by the chart once enough
	applications share it (api/v1/dashboard.py).
	"""
	region = _geo(LoanApplication.region, FarmerProfile.region)
	woreda = _geo(LoanApplication.woreda, FarmerProfile.woreda)
	declined = (
		frappe.qb.from_(LoanApplication)
		.left_join(FarmerProfile)
		.on(FarmerProfile.name == LoanApplication.farmer_profile)
		.where(LoanApplication.status == "Rejected")
		.select(
			LoanApplication.name.as_("name"),
			LoanApplication.bank.as_("bank"),
			region.as_("region"),
			woreda.as_("woreda"),
			Coalesce(LoanApplication.requested_amount, LoanApplication.loan_amount, 0).as_("requested"),
		)
		.run(as_dict=True)
	)
	if not declined:
		return []

	reason_by_app: dict[str, str] = {}
	for chunk in _chunks([d.name for d in declined]):
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
			reason = _reason(event.event_description)
			if reason:
				reason_by_app[event.loan_application] = reason

	merged: dict[tuple, dict] = {}
	for app in declined:
		reason = reason_by_app.get(app.name) or REASON_NOT_RECORDED
		place = _place(app.region, app.woreda)
		key = (app.bank or "", reason, place["region_key"], place["woreda_key"])
		r = merged.setdefault(key, _row("decline", bank=app.bank, reason=reason, **place))
		r["total"] += 1
		r["requested_value"] += float(app.requested or 0)
	return list(merged.values())


def _reason(description) -> str | None:
	for line in str(description or "").splitlines():
		if line.startswith(_REASON_PREFIX):
			text = line[len(_REASON_PREFIX) :].strip()
			if text:
				return text[:MAX_REASON_LENGTH]
	return None


def _provider_rows() -> list[dict]:
	"""Every participating bank, with the display fields the provider panels show."""
	return [
		_row(
			"provider",
			bank=p.name,
			details=json.dumps(
				{
					"bank_name": p.bank_name,
					"bank_code": p.bank_code,
					"brand_name": p.brand_name,
					"entity_type": p.entity_type,
					"status": p.status,
					"onboarded_on": str(getdate(p.creation)),
				}
			),
		)
		for p in frappe.get_all(
			"A2C Participating Bank",
			fields=["name", "bank_name", "bank_code", "brand_name", "entity_type", "status", "creation"],
			ignore_permissions=True,
		)
	]
