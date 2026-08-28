# Implementation: expose bank stages as the loan application status

Companion to `docs/loan-status-workflow-plan.md`. That doc holds the architecture and the
reasoning; this one is the change list.

**Goal.** The archetype (`Active` / `In Transition` / `Completed` / `Rejected` /
`Cancelled`) becomes backend-only. Every consumer-facing response and filter speaks the
bank's stage vocabulary. The only API that names an archetype is
`api/v1/seller/loan_stages.py`, where it is the subject matter.

**Ordering.** Step 1 must land first — everything else consumes its helpers. Steps 2–6 are
independent of each other and can be split across commits or people.

---

## Step 1 — helpers in `oan_a2c/a2c_marketplace/stages.py`

Keep `ARCHETYPE_STATES`, `resolve_bank_stage`, `get_initial_pipeline_stage` as they are.
Add:

```python
TERMINAL_ARCHETYPES = ("Completed", "Rejected", "Cancelled")
SUCCESSFUL_ARCHETYPES = ("Completed",)

_STAGE_MAP_CACHE_KEY = "a2c_stage_map"


def get_stage_map(bank: str) -> dict:
	"""{stage_id: {"label", "archetype_state", "sequence"}} for one bank.

	Cached; invalidated from the stage doctype's on_update/on_trash.
	"""


def status_payload(row, stage_map: dict | None = None) -> dict:
	"""The consumer-facing status block. Never contains an archetype name.

	{"status", "stage_id", "sequence", "is_terminal", "is_successful"}
	"""


def build_status_payloads(rows: list) -> list:
	"""status_payload over a page of rows, loading each distinct bank's map once."""


def resolve_status_filter(value, user: str | None = None) -> tuple[dict, list]:
	"""Turn a multi-value filter of stage labels / stage_ids / external_codes
	(plus the literal 'Active') into (filters, or_filters) for frappe.get_list.
	"""
```

Notes that matter:

- **`status` is `row.stage_label or row.status`.** This is the one place that fallback
  lives. It replaces the six ad-hoc coalesces at `loan_applications.py:403,966,995` and
  `stats_cache.py:151,366,382` — migrate those to call the helper as you touch each file.
- **`is_terminal` / `is_successful` derive from `row.status`**, which is already on the
  row. No extra query. `sequence` needs `get_stage_map`; it is `None` for `Active`.
- **`resolve_status_filter` reuses `parse_multi_value`** (`api/utils.py:256`) for the
  comma/JSON splitting — do not write another splitter.
- **Cross-bank matching.** Resolve each value against `stage_id`, `external_code` **and
  `label`**, unscoped by bank when the caller is bank-unbound, so a Development Agent
  filtering `Verified` matches every bank's `Verified` stage. Label matching is for
  filters only — do not extend it to the write path.
- **`"Active"`** resolves to `{"status": "Active"}`; everything else to
  `{"stage_id": ["in", [...]]}`.
- **Unresolvable value** → `frappe.throw(..., frappe.ValidationError)` listing the
  caller's valid stage names. This replaces the archetype-only error text today.
- **The mixed case.** `Active` + stage names needs an OR across two columns, and
  `get_list` allows one `or_filters` group — already spent on `search_query` in
  `get_all_loans`. Reject that specific combination with a clear 400. Reachable only by
  Development Agents/Admins, and only alongside a search.
- **`A2C Loan Status Stage` is in `BANK_SCOPED`** (`hooks.py:143`), so the AST guard in
  `tests/test_bank_scope_enforcement.py` will fail the build on any `get_all` here that
  lacks `bank_filters` or a `# bank-scope-exempt` comment. The existing calls in this file
  already carry the exempt comment; match that pattern and state the reason.

**Cache invalidation.** Add `on_update` and `on_trash` to
`a2c_marketplace/doctype/a2c_loan_status_stage/a2c_loan_status_stage.py` to clear the
bank's key. The file already has an `on_trash` guard blocking deletion of a stage with
live applications — extend it, do not replace it.

---

## Step 2 — read endpoints return the stage

Every list query must add `stage_id`, `stage_label` and `bank` to its `fields`, then map
rows through `build_status_payloads`. Single-doc endpoints use `status_payload(doc)`.

| File                            | Function                                  | Change                                                |
| ------------------------------- | ----------------------------------------- | ----------------------------------------------------- |
| `api/v1/loan_applications.py`   | `get_all_loans` (fields list ~`:559-571`) | add the three fields; map the page                    |
| `api/v1/loan_applications.py`   | `get_full_profile` (~`:340`)              | replace `"status": doc.status` with the payload block |
| `api/v1/farmer/applications.py` | `list_applications` (~`:64-80`)           | add fields; map the page                              |
| `api/v1/farmer/applications.py` | `get_application` (~`:132`)               | payload block                                         |
| `api/v1/farmer/dashboard.py`    | `get_dashboard_summary` (~`:53,66`)       | add fields; map `recent_applications`                 |
| `api/v1/loan_applications.py`   | `create_loan_application` (~`:898-901`)   | report the created status via the helper              |
| `api/v1/farmer/applications.py` | `create_application` (~`:238`)            | same                                                  |

**Leave alone.** These compare the archetype because they are internal machinery, not
display:

- `farmer/applications.py:169,264` — `doc.status != "Active"` edit/submit guards.
- `permissions.py:313` (`status != 'Active'` query gate) and `:395` (the single-doc
  mirror).
- `a2c_loan_application.py:90` (`after_insert`) and `:109` (the `Active →` branch).
- `loan_applications.py:886`, `farmer/applications.py:238` — the internal
  `status = "Active"` assignments themselves.
- `api/utils.py:739` — `target_status == "In Transition"` prerequisite hook.

---

## Step 3 — filters and validation

1. **Delete `GetAllLoansSchema.validate_statuses`** (`loan_applications.py:75-88`). The
   valid set is now per-caller tenant data, so a pydantic class validator cannot express
   it. Keep the `max_length` bound on the field.
2. **`get_all_loans`** (`:484-488`) — replace the `get_workflow_state_names` lookup and
   `filters["status"] = ["in", ...]` with `resolve_status_filter`. Merge its `or_filters`
   with the existing `search_query` group, or raise the documented 400 if both need one.
3. **`farmer/applications.py:53-54`** — replace `filters["status"] = kwargs["status"]`
   with the same call. This is the silent-zero-rows bug; add a test that would have caught
   it.

---

## Step 4 — `get_loan_metadata` returns the caller's stages

`loan_applications.py:428-443` currently reads the `status` Select options off the doctype
meta, so a bank sees the archetype list. Replace with the stages visible to the caller:

- bank-bound → their own bank's stages, ordered by `sequence`, plus `Active`;
- bank-unbound (`A2C Administrator`, `A2C Development Agent`) → union of distinct labels;
- farmer → union across the banks they hold applications with.

Return richer entries so one call drives both a dropdown and pipeline columns:

```python
{"statuses": [{"status": "Verified", "stage_id": "verified-7c1a92",
               "sequence": 3, "is_terminal": False}, ...]}
```

Reuse `is_bank_unbound` and `get_user_bank` from `a2c_marketplace/permissions.py`.

---

## Step 5 — summary and dashboard buckets

**`get_loan_summary`** (`loan_applications.py:368-425`):

- drop the `by_status` bucket and the `ARCHETYPE_STATES` seeding at `:393`;
- zero-fill `stages` from the caller's stage set (same scoping rule as Step 4), then
  overlay the grouped counts;
- keep `total` and `tab_counts` unchanged.

**`stats_cache.py`**: the stored `stage_counts` map is already keyed on
`stage_label or status`, so **the cached counters need no change**. Zero-fill at read time
in `get_dashboard_stats`, which is what `api/v1/seller/dashboard.py:8-14` returns.

Leave `_EXCLUDED_APPLICATION_STATUSES` (`:28`) and `_PENDING_APPLICATION_STATUS` (`:34`)
comparing the archetype — internal semantics, and `test_stats_cache.py:359,589` assert
they remain valid Select options.

---

## Step 6 — notifications

`openagrinet_access_to_credit/doctype/a2c_loan_application/a2c_loan_application.py:120-125`
— the fallback subject `f"Loan application {self.name} is now {self.status}"` sends the
archetype to bank users. Use the stage label.

While here: `on_update` returns early unless `has_value_changed("status")` (`:103`), so a
stage move _within_ one archetype sends no notification at all — and that is the common
case, since four of the six default stages are `In Transition`. Fire on a `stage_label`
change too. Keep the `Active →` branch at `:109-118` keyed on the archetype; it is
detecting first submission, not a display change.

---

## Step 7 — cleanup

- Remove the dead `status_has_tag` import at `loan_applications.py:19` and the unused
  import at `tests/test_loan_api.py:5`.
- `status_has_tag` (`api/utils.py:196`) and `workflow_state_has_tag` (`:184`) are
  leftovers from the abandoned tag design. Confirm no callers, then delete both.

---

## Verification

```bash
# from development/frappe-bench-16/
bench --site <site> migrate
bench --site <site> clear-cache          # hooks + the new stage map cache

bench run-tests --app oan_a2c --module oan_a2c.tests.test_loan_api
bench run-tests --app oan_a2c --module oan_a2c.tests.test_stats_cache
bench run-tests --app oan_a2c           # full suite
```

**Existing tests that will move:**

- `test_loan_api.py:188-208` `test_1a_loan_summary_exposes_every_archetype_bucket` —
  rewrite for the dropped `by_status`; assert `stages` is zero-filled instead.
- `test_loan_api.py:216` `get_all_loans(status="Active")` — must still pass.
- `test_loan_api.py:397` `assertEqual(res["data"]["status"], "Active")` — still `Active`,
  now via the helper.
- `test_loan_api.py:504-558` `test_7_rejected_loan_status_locked` — already asserts
  `doc.stage_label == "Rejected"`; should pass untouched.

**Existing tests that must pass unchanged** — these are the check that internal semantics
were not disturbed:

- `test_stats_cache.py:382` `test_archetype_states_match_the_doctype`
- `test_stats_cache.py:359,589` — the `_EXCLUDED` / `_PENDING` literal checks
- `test_bank_scope_enforcement_runtime.py:184-190` `test_bank_user_sees_no_draft`
- `test_bank_scope_enforcement.py` — the AST guard, which now covers the new stage lookups

**New tests:**

1. Farmer list + detail return `Submitted` (not `In Transition`) for a submitted
   application, and `Active` before submission.
2. `get_all_loans(status="Verified")` returns rows instead of 400.
3. Farmer `list_applications(status="Verified")` returns rows instead of silently empty.
4. A Development Agent filtering `status="Verified"` across two banks whose `Verified`
   stages have different `stage_id`s gets both banks' rows.
5. Renaming a stage through `sync_stages` changes what the farmer sees on an existing
   application.
6. **No consumer response contains the string `"In Transition"`** — assert over the full
   JSON of each read endpoint. This is the regression guard for the whole change.

**Manual smoke.** As a Bank Admin, rename `Verified` → `Field Check` via `sync_stages`,
then confirm `get_loan_metadata`, `get_loan_summary` and the farmer's application detail
all read `Field Check`, that `is_terminal` is `false` for it and `true` for `Disbursed`,
and that the seller dashboard's `stage_counts` still totals correctly.

---

## Out of scope

Real, but write-path concerns — this change is read-path only. Tracked in
`loan-status-workflow-plan.md` §5:

- `resolve_bank_stage` accepting a **label** as a write identifier.
- `sync_stages`: delete-by-omission, the raw SQL label fan-out, and the unreachable
  `hasattr`/dict branches.
