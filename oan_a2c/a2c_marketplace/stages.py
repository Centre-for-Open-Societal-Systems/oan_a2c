import frappe
from frappe import _

# The archetype states, in lifecycle order. Platform constants: a bank names the
# stages *inside* `In Transition`, it never renames these. Anything reporting on
# loan state should bucket by these rather than by a stage label, which is
# tenant-defined free text and differs between banks.
#
# `Rejected` is a first-class archetype, deliberately separate from `Completed`:
# the workflow carries In Transition -> Rejected transitions for both Bank Agent
# and Bank Admin, and the legacy-status backfill maps a declined loan to
# `Rejected` rather than folding it into `Completed`. Keep them apart -- a
# disbursed loan and a declined one are not the same outcome, and collapsing them
# is one-way: nothing downstream can recover the distinction afterwards.
#
# `Cancelled` has no transition into it yet; the state exists so the vocabulary is
# complete, but nothing can currently reach it.
ARCHETYPE_STATES = ("Active", "In Transition", "Completed", "Rejected", "Cancelled")
TERMINAL_ARCHETYPES = ("Completed", "Rejected", "Cancelled")
SUCCESSFUL_ARCHETYPES = ("Completed",)

_STAGE_MAP_CACHE_KEY = "a2c_stage_map"
# Belt-and-braces against a missed invalidation. on_update/on_trash clear the key
# inside the writing transaction, so a concurrent reader can re-cache the pre-commit
# row and leave the entry stale with nothing to evict it. An hour bounds that.
_STAGE_MAP_CACHE_TTL = 3600


def get_stage_map(bank: str) -> dict:
	"""{stage_id: {"label": label, "archetype_state": archetype_state, "sequence": sequence}} for one bank.

	Cached; invalidated from the stage doctype's on_update/on_trash.
	"""
	if not bank:
		return {}

	cache = frappe.cache()
	key = f"{_STAGE_MAP_CACHE_KEY}:{bank}"
	cached = cache.get_value(key)
	if cached is not None:
		return cached

	stages = frappe.get_all(  # bank-scope-exempt: per-bank cached stage map lookup
		"A2C Loan Status Stage",
		filters={"bank": bank},
		fields=["stage_id", "label", "archetype_state", "sequence"],
		order_by="sequence asc, creation asc",
	)

	stage_map = {
		s.stage_id: {
			"label": s.label,
			"archetype_state": s.archetype_state,
			"sequence": s.sequence,
		}
		for s in stages
	}

	cache.set_value(key, stage_map, expires_in_sec=_STAGE_MAP_CACHE_TTL)
	return stage_map


def invalidate_stage_map_cache(bank: str | None = None) -> None:
	"""Invalidates the cached stage map for a bank, or for all banks if bank is None."""
	cache = frappe.cache()
	if bank:
		cache.delete_value(f"{_STAGE_MAP_CACHE_KEY}:{bank}")
	else:
		# delete_keys, not get_keys + delete_value: get_keys returns keys that are
		# already site-namespaced, and delete_value namespaces again by default, so
		# the pair silently deletes nothing.
		cache.delete_keys(_STAGE_MAP_CACHE_KEY)


def _entry_stage_label(stage_map: dict | None) -> str | None:
	"""The label of the bank's first `In Transition` stage, or None if it has none.

	The read-path twin of get_initial_pipeline_stage, which picks the same stage by
	the same ordering off a fresh query -- stage_map is already sorted by
	`sequence asc, creation asc`, so iteration order matches. Keep the two in step.

	No invented default: a bank with no In Transition stage has no name for this and
	saying "Submitted" would just be guessing that it kept the seeded pipeline.
	"""
	for stage in (stage_map or {}).values():
		if stage.get("archetype_state") == "In Transition" and stage.get("label"):
			return stage["label"]
	return None


def _row_field(row, fieldname: str):
	"""Reads `fieldname` off a dict row or a Document alike."""
	if isinstance(row, dict):
		return row.get(fieldname)
	return getattr(row, fieldname, None)


def status_payload(row, stage_map: dict | None = None) -> dict:
	"""The consumer-facing status block. Never contains an archetype name.

	{"status", "stage_id", "sequence", "is_terminal", "is_successful"}
	"""
	raw_status = _row_field(row, "status")
	stage_label = _row_field(row, "stage_label")
	stage_id = _row_field(row, "stage_id")
	bank = _row_field(row, "bank")

	if raw_status == "Active":
		return {
			"status": "Active",
			"stage_id": None,
			"sequence": None,
			"is_terminal": False,
			"is_successful": False,
		}

	if stage_map is None and bank:
		stage_map = get_stage_map(bank)

	resolved_label = None
	sequence = None
	if stage_id and stage_map and stage_id in stage_map:
		resolved_label = stage_map[stage_id].get("label")
		sequence = stage_map[stage_id].get("sequence")

	status = resolved_label or stage_label or raw_status
	if status == "In Transition":
		# A row that reached In Transition without a stage stamped on it -- legacy
		# data from before the pipeline existed; submit_application sets both fields
		# now. Name it after the bank's entry stage so no consumer ever sees the
		# archetype, but leave `sequence` null: the row is not actually parked on
		# that stage, and reporting its position would be a lie the client can't
		# distinguish from a real one.
		#
		# Entry stage means lowest-sequence *In Transition* stage, not lowest overall
		# -- `sequence` defaults to 1 and isn't required, so a Completed or Rejected
		# stage can sort first and would otherwise label a freshly submitted loan
		# "Disbursed".
		#
		# Temporary fallback for older legacy/test records where the bank has no In Transition
		# stage configured so status is never null; will be removed later.
		status = _entry_stage_label(stage_map) or "Submitted"

	# Fallback for older legacy records where status is null; will be removed later.
	if not status:
		status = "Active"

	is_terminal = raw_status in TERMINAL_ARCHETYPES
	is_successful = raw_status in SUCCESSFUL_ARCHETYPES

	return {
		"status": status,
		"stage_id": stage_id,
		"sequence": sequence,
		"is_terminal": is_terminal,
		"is_successful": is_successful,
	}


def build_status_payloads(rows: list) -> list:
	"""status_payload over a page of rows, loading each distinct bank's map once."""
	if not rows:
		return rows

	banks = {_row_field(r, "bank") for r in rows}
	banks.discard(None)

	stage_maps = {b: get_stage_map(b) for b in banks}

	for r in rows:
		payload = status_payload(r, stage_maps.get(_row_field(r, "bank")))
		# frappe.get_list rows are _dict. Anything else would silently keep its raw
		# archetype status, which is exactly what this function exists to prevent.
		if not isinstance(r, dict):
			frappe.throw(_("build_status_payloads expects dict rows, got {0}").format(type(r).__name__))
		r.update(payload)

	return rows


def resolve_status_filter(value, user: str | None = None) -> dict:
	"""Turn a multi-value filter of stage labels / stage_ids / external_codes
	(plus the literal 'Active') into a `filters` dict for frappe.get_list.

	Every branch resolves to plain AND filters -- `Active` and pipeline stages are
	mutually exclusive (they live on different columns and are rejected together
	above), so nothing here ever needs or_filters.
	"""
	from oan_a2c.a2c_marketplace.permissions import get_user_bank, is_bank_unbound, is_farmer
	from oan_a2c.api.utils import parse_multi_value

	if not user:
		user = frappe.session.user

	tokens = parse_multi_value(value)
	if not tokens:
		return {}

	# Check for bank scoping
	stage_filters = {}
	if not is_bank_unbound(user) and not is_farmer(user):
		user_bank = get_user_bank(user)
		if user_bank:
			stage_filters["bank"] = user_bank

	all_stages = frappe.get_all(  # bank-scope-exempt: filter resolution lookup across stages
		"A2C Loan Status Stage",
		filters=stage_filters or None,
		fields=["stage_id", "external_code", "label", "bank"],
	)

	# Build lookup map: label -> [stage_ids], stage_id -> [stage_id], external_code -> [stage_ids]
	val_to_ids: dict[str, set[str]] = {}
	valid_names = set()
	for s in all_stages:
		if s.label:
			val_to_ids.setdefault(s.label, set()).add(s.stage_id)
			valid_names.add(s.label)
		if s.stage_id:
			val_to_ids.setdefault(s.stage_id, set()).add(s.stage_id)
			valid_names.add(s.stage_id)
		if s.external_code:
			val_to_ids.setdefault(s.external_code, set()).add(s.stage_id)
			valid_names.add(s.external_code)

	valid_names.add("Active")

	has_active = False
	matched_stage_ids: set[str] = set()

	for token in tokens:
		if token == "Active":
			has_active = True
		elif token in val_to_ids:
			matched_stage_ids.update(val_to_ids[token])
		else:
			sorted_valid = sorted(valid_names)
			frappe.throw(
				_("Invalid status '{0}'. Allowed values: {1}").format(token, ", ".join(sorted_valid)),
				frappe.ValidationError,
			)

	if has_active and matched_stage_ids:
		frappe.throw(
			_("Filtering by both 'Active' and bank pipeline stages simultaneously is not supported."),
			frappe.ValidationError,
		)

	if has_active:
		return {"status": "Active"}

	if matched_stage_ids:
		return {"stage_id": ["in", sorted(matched_stage_ids)]}

	return {}


def resolve_bank_stage(bank, status_or_stage):
	"""
	Given a string that might be an archetype state or a bank stage (by id, external code, or label),
	return a dict with 'archetype_state' and 'stage_id' (if resolved to a specific stage).
	"""
	# Direct match for archetype states
	if status_or_stage in ARCHETYPE_STATES:
		return {"archetype_state": status_or_stage, "stage_id": None, "stage_label": status_or_stage}

	# Otherwise try to match a bank stage
	stage = frappe.get_all(  # bank-scope-exempt: bank explicitly filtered
		"A2C Loan Status Stage",
		filters={"bank": bank},
		or_filters={"stage_id": status_or_stage, "external_code": status_or_stage, "label": status_or_stage},
		fields=["stage_id", "label", "archetype_state"],
		limit=1,
	)

	if stage:
		return {
			"archetype_state": stage[0].archetype_state,
			"stage_id": stage[0].stage_id,
			"stage_label": stage[0].label,
		}

	# Invalid stage
	frappe.throw(
		_("Invalid status or stage '{0}' for bank {1}").format(status_or_stage, bank), frappe.ValidationError
	)


def get_initial_pipeline_stage(bank):
	"""Returns the first stage for 'In Transition' for the given bank, or None."""
	stage = frappe.get_all(  # bank-scope-exempt: bank explicitly filtered
		"A2C Loan Status Stage",
		filters={"bank": bank, "archetype_state": "In Transition"},
		fields=["stage_id", "label", "archetype_state"],
		order_by="sequence asc, creation asc",
		limit=1,
	)
	return stage[0] if stage else None


def get_visible_stages(user: str | None = None) -> list[dict]:
	"""The pipeline stages `user` is allowed to see, ordered and deduplicated by label.

	Bank-scoped callers get their own bank's pipeline. Bank-unbound callers span
	every bank, and a farmer spans the banks they actually hold applications with --
	both of which can surface the same label from two banks at different sequences,
	so the dedupe keeps the first occurrence and the caller gets one entry per label.

	Ordering is done here rather than in SQL: `SELECT DISTINCT label ... ORDER BY
	sequence` is nondeterministic on MariaDB and an outright error on MySQL, because
	`sequence` isn't in the projection.
	"""
	from oan_a2c.a2c_marketplace.permissions import (
		get_user_bank,
		is_bank_unbound,
		is_farmer,
	)

	if not user:
		user = frappe.session.user

	fields = ["label", "stage_id", "sequence", "archetype_state"]
	filters = None

	if is_bank_unbound(user) or is_farmer(user):
		filters = None
	else:
		user_bank = get_user_bank(user)
		if not user_bank:
			return []
		filters = {"bank": user_bank}

	stages = frappe.get_all(  # bank-scope-exempt: scoped by the branch above
		"A2C Loan Status Stage",
		filters=filters,
		fields=fields,
	)

	# `sequence` is nullable, so sort None last rather than blowing up on the compare.
	stages.sort(key=lambda s: (s.sequence is None, s.sequence, s.label or ""))

	seen = set()
	visible = []
	for s in stages:
		if not s.label or s.label in seen:
			continue
		seen.add(s.label)
		visible.append(
			{
				"label": s.label,
				"stage_id": s.stage_id,
				"sequence": s.sequence,
				"archetype_state": s.archetype_state,
			}
		)

	return visible
