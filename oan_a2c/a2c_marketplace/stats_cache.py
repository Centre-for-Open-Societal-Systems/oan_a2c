import frappe

# Counters are split by value shape because the all-banks view aggregates them
# differently: scalars sum, maps merge key-wise. Adding a counter to the wrong
# tuple is a TypeError in _all_banks_view rather than a silently wrong number.
_SCALAR_COUNTERS = (
	"total_products",
	"active_products",
	"pending_products",
	"rejected_products",
	"archived_products",
	"total_applications",
)

_MAP_COUNTERS = ("stage_counts",)

_COUNTERS = _SCALAR_COUNTERS + _MAP_COUNTERS

_EXCLUDED_PRODUCT_STATUSES = set()

# `Active` is the farmer's own pre-submission stage. loan_application_scope_query
# hides it from bank users, so counting it would put a number on the dashboard
# card that the list beneath it can never show -- and would disclose how many
# hidden rows exist to exactly the users the scope query excludes. Kept out of
# every application counter so the card and the list reconcile.
_EXCLUDED_APPLICATION_STATUSES = {"Active"}


def _key(bank: str, counter: str) -> str:
	return f"dashboard_stats:{bank}:{counter}"


def _get(bank: str, counter: str):
	return frappe.cache().get_value(_key(bank, counter))


def _set(bank: str, counter: str, value) -> None:
	frappe.cache().set_value(_key(bank, counter), value)


def _incr(bank: str, counter: str, amount=1) -> None:
	"""Increment counter only if cache is warm. If cold, skip — next read falls back to DB."""
	key = _key(bank, counter)
	current = frappe.cache().get_value(key)
	if current is None:
		return
	frappe.cache().set_value(key, current + amount)


def _decr(bank: str, counter: str, amount=1) -> None:
	_incr(bank, counter, -amount)


def _incr_stage(bank: str, stage: str, amount=1) -> None:
	"""Increment a specific stage count inside the cached `stage_counts` dictionary."""
	key = _key(bank, "stage_counts")
	counts = frappe.cache().get_value(key)
	if counts is None:
		return
	counts[stage] = counts.get(stage, 0) + amount
	if counts[stage] <= 0:
		counts.pop(stage, None)
	frappe.cache().set_value(key, counts)


def get_stats_for_bank(bank: str) -> dict | None:
	"""Return cached stats dict, or None if cache is cold (any counter missing)."""
	result = {}
	for counter in _COUNTERS:
		val = _get(bank, counter)
		if val is None:
			return None
		result[counter] = val
	return result


def _compute_from_db(bank: str) -> dict:
	"""Query DB for a single bank's stats. The all-banks view sums these per bank."""
	filters = {"bank": bank}

	product_counts = frappe.get_all(  # bank-scope-exempt: bank scoped explicitly via filters above
		"A2C Loan Product",
		filters=filters,
		fields=["status", {"COUNT": "name", "as": "count"}],
		group_by="status",
	)
	total_products = sum(
		item.count for item in product_counts if item.status not in _EXCLUDED_PRODUCT_STATUSES
	)
	active_products = sum(item.count for item in product_counts if item.status == "Active")
	pending_products = sum(item.count for item in product_counts if item.status == "Pending Approval")
	rejected_products = sum(item.count for item in product_counts if item.status == "Rejected")
	archived_products = sum(item.count for item in product_counts if item.status == "Archived")

	app_filters = dict(filters)
	if _EXCLUDED_APPLICATION_STATUSES:
		app_filters["status"] = ["not in", sorted(_EXCLUDED_APPLICATION_STATUSES)]

	app_counts = frappe.get_all(  # bank-scope-exempt: bank scoped explicitly via filters above
		"A2C Loan Application",
		filters=app_filters,
		fields=["status", "stage_label", {"COUNT": "name", "as": "count"}],
		group_by="status, stage_label",
	)

	total_applications = sum(item.count for item in app_counts)
	stage_counts = {}

	for item in app_counts:
		stage = item.stage_label or item.status
		if stage not in stage_counts:
			stage_counts[stage] = 0
		stage_counts[stage] += item.count

	return {
		"total_products": total_products,
		"active_products": active_products,
		"pending_products": pending_products,
		"rejected_products": rejected_products,
		"archived_products": archived_products,
		"total_applications": total_applications,
		"stage_counts": stage_counts,
	}


def compute_and_set(bank: str) -> dict:
	"""Compute stats from DB for a specific bank, write counters, return result."""
	stats = _compute_from_db(bank)
	for counter, value in stats.items():
		_set(bank, counter, value)
	return stats


def _all_banks_view() -> dict:
	"""Admin (all-banks) view: platform totals plus a per-bank breakdown.

	Scalar counters sum; `stage_counts` merges key-wise. Either way the platform
	total is derived from the per-bank values, reusing the caches the incr/decr
	hooks already keep consistent — no separate 'all banks' key to maintain. A cold
	bank falls back to its own compute_and_set (a per-bank query), warming it as a
	side effect. We deliberately never issue one cross-bank aggregate here.
	"""
	totals = dict.fromkeys(_SCALAR_COUNTERS, 0)
	stage_totals: dict[str, int] = {}
	by_bank = []
	for bank in frappe.get_all("A2C Participating Bank", pluck="name"):
		stats = get_stats_for_bank(bank) or compute_and_set(bank)
		by_bank.append({"bank": bank, **stats})
		for counter in _SCALAR_COUNTERS:
			totals[counter] += stats[counter]
		# Stage labels are per-bank by design (each bank names its own pipeline
		# stages), so the platform view is a key-wise merge, not a sum. Two banks
		# that happen to use the same label are added together; distinct labels
		# stay as separate buckets.
		for stage, count in (stats["stage_counts"] or {}).items():
			stage_totals[stage] = stage_totals.get(stage, 0) + count
	return {"stats": {**totals, "stage_counts": stage_totals}, "by_bank": by_bank}


def get_dashboard_stats(bank: str | None) -> dict:
	"""Resolve the dashboard payload for a caller.

	bank=None   => unbound admin: {"stats": <platform totals>, "by_bank": [...]}.
	bank=<code> => that bank, cache-first: {"stats": <that bank's stats>}.
	"""
	if bank is None:
		return _all_banks_view()
	return {"stats": get_stats_for_bank(bank) or compute_and_set(bank)}


def on_product_change(doc, event: str) -> None:
	bank = doc.bank
	if not bank:
		return

	if event == "after_insert":
		if doc.status not in _EXCLUDED_PRODUCT_STATUSES:
			_incr(bank, "total_products")
		if doc.status == "Active":
			_incr(bank, "active_products")
		elif doc.status == "Pending Approval":
			_incr(bank, "pending_products")
		elif doc.status == "Rejected":
			_incr(bank, "rejected_products")
		elif doc.status == "Archived":
			_incr(bank, "archived_products")

	elif event == "on_update":
		before = doc.get_doc_before_save()
		if before is None:
			return  # after_insert already handled this
		if before.status != doc.status:
			was_excluded = before.status in _EXCLUDED_PRODUCT_STATUSES
			now_excluded = doc.status in _EXCLUDED_PRODUCT_STATUSES
			if not was_excluded and now_excluded:
				_decr(bank, "total_products")
			elif was_excluded and not now_excluded:
				_incr(bank, "total_products")

			if before.status == "Active":
				_decr(bank, "active_products")
			elif before.status == "Pending Approval":
				_decr(bank, "pending_products")
			elif before.status == "Rejected":
				_decr(bank, "rejected_products")
			elif before.status == "Archived":
				_decr(bank, "archived_products")

			if doc.status == "Active":
				_incr(bank, "active_products")
			elif doc.status == "Pending Approval":
				_incr(bank, "pending_products")
			elif doc.status == "Rejected":
				_incr(bank, "rejected_products")
			elif doc.status == "Archived":
				_incr(bank, "archived_products")

	elif event == "on_trash":
		if doc.status not in _EXCLUDED_PRODUCT_STATUSES:
			_decr(bank, "total_products")
		if doc.status == "Active":
			_decr(bank, "active_products")
		elif doc.status == "Pending Approval":
			_decr(bank, "pending_products")
		elif doc.status == "Rejected":
			_decr(bank, "rejected_products")
		elif doc.status == "Archived":
			_decr(bank, "archived_products")


def on_application_change(doc, event: str) -> None:
	bank = doc.bank
	if not bank:
		return

	# Determine the effective stage name for this document
	current_stage = doc.stage_label or doc.status
	current_counted = doc.status not in _EXCLUDED_APPLICATION_STATUSES

	if event == "after_insert":
		if current_counted:
			_incr(bank, "total_applications")
			_incr_stage(bank, current_stage, 1)

	elif event == "on_update":
		before = doc.get_doc_before_save()
		if before is None:
			return  # after_insert already handled this

		before_stage = before.stage_label or before.status
		before_counted = before.status not in _EXCLUDED_APPLICATION_STATUSES

		# Leaving `Active` (the farmer submits) is the moment an application first
		# becomes countable; returning to it takes it back out. Without this the
		# total would stay stuck at whatever it was when the row was inserted.
		if before_counted and not current_counted:
			_decr(bank, "total_applications")
		elif current_counted and not before_counted:
			_incr(bank, "total_applications")

		if before_counted != current_counted or before_stage != current_stage:
			if before_counted:
				_incr_stage(bank, before_stage, -1)
			if current_counted:
				_incr_stage(bank, current_stage, 1)

	elif event == "on_trash":
		if current_counted:
			_decr(bank, "total_applications")
			_incr_stage(bank, current_stage, -1)


def reconcile_all_banks() -> None:
	"""Hourly scheduled job: recompute counters for every bank from DB to correct drift."""
	banks = frappe.get_all("A2C Participating Bank", fields=["name"])
	for row in banks:
		compute_and_set(row.name)
