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
	"total_applicants",
	"pending_applications",
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

# Archetype states are platform constants (docs/loan-status-workflow-plan.md): a bank
# renames its own stage labels inside `In Transition`, never these four. `In
# Transition` is the whole of a bank pipeline, so it is what "awaiting the bank"
# means regardless of how that bank names its internal stages.
_PENDING_APPLICATION_STATUS = "In Transition"


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


def on_stage_moved(bank: str, before_stage: str | None, after_stage: str | None) -> None:
	"""Move `stage_counts` for a stage change that no document save can announce.

	The stage normally rides along on the save inside apply_status_transition, which
	fires on_application_change and updates the counter there. A submitted document
	(docstatus 1) cannot be saved again, so update_loan_status writes its stage with
	db_set -- which fires no doc_events -- and calls this to keep the cached
	breakdown honest. Nothing else should need it.
	"""
	if not bank or before_stage == after_stage:
		return
	if before_stage:
		_incr_stage(bank, before_stage, -1)
	if after_stage:
		_incr_stage(bank, after_stage, 1)


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
	pending_applications = sum(
		item.count for item in app_counts if item.status == _PENDING_APPLICATION_STATUS
	)

	# Distinct farmers, not applications: one farmer with three applications is one
	# applicant. This needs its own query -- the grouped counts above cannot yield a
	# distinct-across-groups figure. Rows with no farmer_profile are not a person we
	# can count, and DISTINCT drops the NULLs for us.
	applicant_rows = frappe.get_all(  # bank-scope-exempt: bank scoped explicitly via filters above
		"A2C Loan Application",
		filters={**app_filters, "farmer_profile": ["is", "set"]},
		fields=["farmer_profile"],
		distinct=True,
	)
	total_applicants = len(applicant_rows)

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
		"total_applicants": total_applicants,
		"pending_applications": pending_applications,
		"stage_counts": stage_counts,
	}


def compute_and_set(bank: str) -> dict:
	"""Compute stats from DB for a specific bank, write counters, return result."""
	stats = _compute_from_db(bank)
	for counter, value in stats.items():
		_set(bank, counter, value)
	return stats


# Counters whose platform total is NOT the sum of the per-bank values.
#
# Everything else in _SCALAR_COUNTERS counts rows, and rows belong to exactly one
# bank, so summing is correct. `total_applicants` counts DISTINCT farmers within a
# bank, and distinctness does not survive a sum: a farmer who applied to three
# banks is counted once in each, so the naive platform figure reports three
# applicants where there is one person -- and it drifts further from the truth the
# more the marketplace works as intended (farmers shopping across banks).
_NON_ADDITIVE_COUNTERS = frozenset({"total_applicants"})


def _platform_distinct_applicants() -> int:
	"""COUNT(DISTINCT farmer_profile) across every bank.

	The one figure in the all-banks view that cannot be derived from the per-bank
	caches, for the reason given on _NON_ADDITIVE_COUNTERS. Distinctness is not
	something a running total can carry, so the incr/decr hooks cannot maintain a
	platform-wide applicant counter without keeping the whole id set in cache.

	This is the single cross-bank aggregate in this module, and it is deliberate:
	it runs only on the admin all-banks path (bank=None), which is already looping
	over every bank, and it is one indexed COUNT against a search_index'd column.
	The per-bank `total_applicants` in `by_bank` stays cache-first and untouched.

	Applications with no farmer_profile are not a person we can count; DISTINCT
	drops the NULLs, and the ifnull guard drops empty strings too.
	"""
	conditions = ["ifnull(`farmer_profile`, '') != ''"]
	values: list = []
	if _EXCLUDED_APPLICATION_STATUSES:
		excluded = sorted(_EXCLUDED_APPLICATION_STATUSES)
		conditions.append("ifnull(`status`, '') not in ({})".format(", ".join(["%s"] * len(excluded))))
		values.extend(excluded)

	# bank-scope-exempt: this is the platform-wide aggregate for the all-banks admin
	# view, so spanning tenants is the entire point. Only reachable from
	# get_dashboard_stats(bank=None), which is gated on an unbound admin.
	row = frappe.db.sql(
		"SELECT COUNT(DISTINCT `farmer_profile`) FROM `tabA2C Loan Application` WHERE "
		+ " and ".join(conditions),
		values,
	)
	return int(row[0][0]) if row and row[0] and row[0][0] is not None else 0


def _all_banks_view() -> dict:
	"""Admin (all-banks) view: platform totals plus a per-bank breakdown.

	Additive scalar counters sum; `stage_counts` merges key-wise. Both are derived
	from the per-bank values, reusing the caches the incr/decr hooks already keep
	consistent — no separate 'all banks' key to maintain. A cold bank falls back to
	its own compute_and_set (a per-bank query), warming it as a side effect.

	The sole exception is `total_applicants`, which is non-additive and is computed
	with one cross-bank query; see _platform_distinct_applicants.
	"""
	totals = dict.fromkeys(_SCALAR_COUNTERS, 0)
	stage_totals: dict[str, int] = {}
	by_bank = []
	for bank in frappe.get_all("A2C Participating Bank", pluck="name"):
		stats = get_stats_for_bank(bank) or compute_and_set(bank)
		by_bank.append({"bank": bank, **stats})
		for counter in _SCALAR_COUNTERS:
			if counter in _NON_ADDITIVE_COUNTERS:
				continue
			totals[counter] += stats[counter]
		# Stage labels are per-bank by design (each bank names its own pipeline
		# stages), so the platform view is a key-wise merge, not a sum. Two banks
		# that happen to use the same label are added together; distinct labels
		# stay as separate buckets.
		for stage, count in (stats["stage_counts"] or {}).items():
			stage_totals[stage] = stage_totals.get(stage, 0) + count

	# Counted across banks, not summed from them: one human with applications at
	# three banks is one applicant on the platform view, while remaining one
	# applicant in each of the three `by_bank` rows.
	totals["total_applicants"] = _platform_distinct_applicants()

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


def _farmer_has_other_counted_application(bank: str, farmer_profile: str, exclude: str) -> bool:
	"""Is this farmer already represented in the bank's applicant count?

	Distinctness cannot be carried in a running total, so instead of guessing, the
	applicant counter asks the database whether the farmer still has another
	countable application before it moves. That is one indexed count, paid only on
	the writes where the farmer's countability actually changes -- not on reads,
	which is what keeps the dashboard cache worth having.
	"""
	return bool(
		frappe.db.count(  # bank-scope-exempt: bank scoped explicitly via the filters below
			"A2C Loan Application",
			{
				"bank": bank,
				"farmer_profile": farmer_profile,
				"name": ["!=", exclude],
				"status": ["not in", sorted(_EXCLUDED_APPLICATION_STATUSES)],
			},
		)
	)


def _apply_applicant_delta(doc, bank: str, amount: int) -> None:
	"""Move the applicant counter only when this farmer's representation flips."""
	if not doc.farmer_profile:
		return
	if _farmer_has_other_counted_application(bank, doc.farmer_profile, doc.name):
		return  # another application already represents this farmer either way
	_incr(bank, "total_applicants", amount)


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
			_apply_applicant_delta(doc, bank, 1)
			if doc.status == _PENDING_APPLICATION_STATUS:
				_incr(bank, "pending_applications")

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
			_apply_applicant_delta(doc, bank, -1)
		elif current_counted and not before_counted:
			_incr(bank, "total_applications")
			_apply_applicant_delta(doc, bank, 1)

		# Pending tracks the `In Transition` archetype, which a loan enters on submit
		# and leaves on completion -- both are plain status changes, so it moves here
		# rather than alongside the countability flip above.
		before_pending = before.status == _PENDING_APPLICATION_STATUS
		current_pending = doc.status == _PENDING_APPLICATION_STATUS
		if before_pending and not current_pending:
			_decr(bank, "pending_applications")
		elif current_pending and not before_pending:
			_incr(bank, "pending_applications")

		if before_counted != current_counted or before_stage != current_stage:
			if before_counted:
				_incr_stage(bank, before_stage, -1)
			if current_counted:
				_incr_stage(bank, current_stage, 1)

	elif event == "on_trash":
		if current_counted:
			_decr(bank, "total_applications")
			_incr_stage(bank, current_stage, -1)
			_apply_applicant_delta(doc, bank, -1)
			if doc.status == _PENDING_APPLICATION_STATUS:
				_decr(bank, "pending_applications")


def reconcile_all_banks() -> None:
	"""Hourly scheduled job: recompute counters for every bank from DB to correct drift."""
	banks = frappe.get_all("A2C Participating Bank", fields=["name"])
	for row in banks:
		compute_and_set(row.name)
