import frappe

_COUNTERS = (
	"total_products",
	"active_products",
	"total_applications",
	"pending_applications",
	"approved_applications",
	"total_approved_amount",
)
_PENDING_STATUSES = {"Submitted", "Under Review"}


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


def get_stats_for_bank(bank: str) -> dict | None:
	"""Return cached stats dict, or None if cache is cold (any counter missing)."""
	result = {}
	for counter in _COUNTERS:
		val = _get(bank, counter)
		if val is None:
			return None
		result[counter] = val
	return result


def _compute_from_db(bank: str | None) -> dict:
	"""Query DB for stats. bank=None means no bank filter (admin/unscoped path)."""
	filters = {"bank": bank} if bank is not None else {}

	product_counts = frappe.get_all(  # bank-scope-exempt: bank scoped explicitly via filters above
		"A2C Loan Product",
		filters=filters,
		fields=["status", {"COUNT": "name", "as": "count"}],
		group_by="status",
	)
	total_products = sum(item.count for item in product_counts)
	active_products = sum(item.count for item in product_counts if item.status == "Active")

	app_counts = frappe.get_all(  # bank-scope-exempt: bank scoped explicitly via filters above
		"A2C Loan Application",
		filters=filters,
		fields=["status", {"COUNT": "name", "as": "count"}, {"SUM": "approved_amount", "as": "total_amount"}],
		group_by="status",
	)
	total_applications = sum(item.count for item in app_counts)
	pending_applications = sum(item.count for item in app_counts if item.status in _PENDING_STATUSES)
	approved_applications = sum(item.count for item in app_counts if item.status == "Approved")
	total_approved_amount = float(
		sum((item.total_amount or 0) for item in app_counts if item.status == "Approved")
	)

	return {
		"total_products": total_products,
		"active_products": active_products,
		"total_applications": total_applications,
		"pending_applications": pending_applications,
		"approved_applications": approved_applications,
		"total_approved_amount": total_approved_amount,
	}


def compute_and_set(bank: str) -> dict:
	"""Compute stats from DB for a specific bank, write counters, return result."""
	stats = _compute_from_db(bank)
	for counter, value in stats.items():
		_set(bank, counter, value)
	return stats


def on_product_change(doc, event: str) -> None:
	bank = doc.bank
	if not bank:
		return

	if event == "after_insert":
		_incr(bank, "total_products")
		if doc.status == "Active":
			_incr(bank, "active_products")

	elif event == "on_update":
		before = doc.get_doc_before_save()
		if before is None:
			return  # after_insert already handled this
		if before.status != doc.status:
			if before.status == "Active":
				_decr(bank, "active_products")
			if doc.status == "Active":
				_incr(bank, "active_products")

	elif event == "on_trash":
		_decr(bank, "total_products")
		if doc.status == "Active":
			_decr(bank, "active_products")


def on_application_change(doc, event: str) -> None:
	bank = doc.bank
	if not bank:
		return

	if event == "after_insert":
		_incr(bank, "total_applications")
		if doc.status in _PENDING_STATUSES:
			_incr(bank, "pending_applications")
		elif doc.status == "Approved":
			_incr(bank, "approved_applications")
			_incr(bank, "total_approved_amount", float(doc.approved_amount or 0))

	elif event == "on_update":
		before = doc.get_doc_before_save()
		if before is None:
			return  # after_insert already handled this

		status_changed = before.status != doc.status
		if status_changed:
			if before.status in _PENDING_STATUSES:
				_decr(bank, "pending_applications")
			elif before.status == "Approved":
				_decr(bank, "approved_applications")
				_decr(bank, "total_approved_amount", float(before.approved_amount or 0))

			if doc.status in _PENDING_STATUSES:
				_incr(bank, "pending_applications")
			elif doc.status == "Approved":
				_incr(bank, "approved_applications")
				_incr(bank, "total_approved_amount", float(doc.approved_amount or 0))

		elif doc.status == "Approved":
			# Status unchanged but approved_amount may have changed
			amount_delta = float(doc.approved_amount or 0) - float(before.approved_amount or 0)
			if amount_delta:
				_incr(bank, "total_approved_amount", amount_delta)

	elif event == "on_trash":
		_decr(bank, "total_applications")
		if doc.status in _PENDING_STATUSES:
			_decr(bank, "pending_applications")
		elif doc.status == "Approved":
			_decr(bank, "approved_applications")
			_decr(bank, "total_approved_amount", float(doc.approved_amount or 0))


def reconcile_all_banks() -> None:
	"""Hourly scheduled job: recompute counters for every bank from DB to correct drift."""
	banks = frappe.get_all("A2C Participating Bank", fields=["name"])
	for row in banks:
		compute_and_set(row.name)
