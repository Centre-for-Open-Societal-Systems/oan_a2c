# Loan Application Status — Two-Layer Architecture

**Scope:** `A2C Loan Application` only. The `A2C Lead` workflow is unchanged.

**Supersedes:** the earlier single-layer plan in this file, which renamed the workflow
states to a fixed seven-status list. That approach is dropped — see §2.

---

## 1. The problem

Status names were hardcoded in five places, so changing one meant editing Python and
shipping a deploy:

| Location                                                                                    | Hardcoded thing                                                             |
| ------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------- |
| `.../a2c_loan_application.json`                                                             | `status` Select options                                                     |
| `api/utils.py` `_WORKFLOW_TRANSITION_ACTIONS`                                               | `(state, target) -> action`, a duplicate of the workflow's transition table |
| `api/v1/loan_applications.py:101,431`                                                       | pydantic `Literal[...]` + `allowed_statuses` tuple                          |
| `loan_applications.py:353-357`, `stats_cache.py:12,79,186-219`                              | **semantic** comparisons — `== "Approved"` meaning "counts toward volume"   |
| `permissions.py:276,358`, `farmer/applications.py:167,268`, `a2c_loan_application.py:20,39` | `"Draft"` meaning "farmer's private stage, invisible to the bank"           |

The deeper problem, which the first plan missed: **banks do not agree on stage names.**
A single fixed list — however data-driven — still forces every bank onto one pipeline.

---

## 2. Two layers

### Layer 1 — the archetype (platform-owned, fixed)

Stays a Frappe Workflow on `A2C Loan Application`, so we keep `docstatus`,
role-gated transitions, `apply_workflow`, and the audit trail.

These five names are **constants**. Nobody can rename them, so platform code may
compare against them directly and safely.

| Archetype state | docstatus | Meaning                                               |
| --------------- | --------- | ----------------------------------------------------- |
| `Active`        | 0         | Farmer's private stage. Banks cannot see or touch it. |
| `In Transition` | 0         | In the bank's pipeline. All bank stages live here.    |
| `Completed`     | 1         | Terminal, success.                                    |
| `Rejected`      | 1         | Terminal, the bank declined.                          |
| `Cancelled`     | 2         | Admin/system escape hatch. Not a bank stage.          |

Transitions — three, plus cancel:

```
Active        → Submit   → In Transition   [A2C Farmer, A2C Development Agent]
In Transition → Complete → Completed       [A2C Bank Agent, A2C Bank Admin]
In Transition → Reject   → Rejected        [A2C Bank Agent, A2C Bank Admin]
(cancel to docstatus 2 — A2C Administrator only, not a workflow transition)
```

`Completed` is deliberately generic rather than `Disbursed`: the archetype should be
domain-neutral, and a bank that calls it "Disbursed" says so in its own label.

### Layer 2 — the bank pipeline (tenant-owned, free text)

**Cannot be a Frappe Workflow.** `get_workflow_name(doctype)`
(`frappe/model/workflow.py:35-41`) resolves exactly one active Workflow per doctype,
cached by doctype alone — there is no bank dimension. So this layer is our own doctype.

`A2C Loan Status Stage`, bank-scoped, one row per stage:

| Column            | Purpose                                    | Who sets it         |
| ----------------- | ------------------------------------------ | ------------------- |
| `stage_id`        | stable identifier, never changes           | system, on create   |
| `label`           | display name                               | **bank, renamable** |
| `archetype_state` | `In Transition` / `Completed` / `Rejected` | bank, constrained   |
| `sequence`        | pipeline order                             | bank                |
| `external_code`   | what the bank's system sends inbound       | bank                |

Many stages collapse to one archetype state. That is the point.

### Ownership

```
Active          platform-owned, banks cannot touch     (farmer's stage)
In Transition   bank defines N stages inside it        (their pipeline)
Completed       bank picks which stage triggers it     (terminal)
Rejected        bank picks which stage triggers it     (terminal)
Cancelled       admin only                             (escape hatch)
```

Bank Admin edits their own bank's stages; `A2C Administrator` edits any. Add
`A2C Loan Status Stage` to `BANK_SCOPED` in `hooks.py` — which means the AST guard in
`tests/test_bank_scope_enforcement.py` will require `bank_filters` on every `get_all`
against it.

### The identifier rule

**The renamable label must never be the identifier.** External integrations key on
`stage_id` / `external_code`; `label` is for humans only. If an integration keys on
`"Field Verification"` and the bank renames it to `"Site Check"`, everything breaks
silently. Same rule inbound: the webhook matches `external_code`, never the label.

The API returns both — a stable id integrations bind to, and a label they display.

### The exposure rule

**The archetype is internal. It never leaves the backend.**

Every consumer reads stages — not just banks. A farmer checking their application, a
Development Agent working the CRM pipeline, and a Bank Agent updating a loan all want to
see `Verified`, never `In Transition`. So the archetype is not a display vocabulary for
_anyone_; it is machinery for `docstatus`, workflow gating, permission gates, and volume
semantics.

| Layer                      | Where it may appear                                                                                                                                                     |
| -------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Archetype (`status`)       | Backend only: permission queries, workflow transitions, stats bucketing, guards.                                                                                        |
| Stage (`stage_label`)      | Every consumer-facing response and filter. This is what `status` means to an API caller.                                                                                |
| Archetype, named in an API | **One exception:** the stage-configuration endpoints in `api/v1/seller/loan_stages.py`, where the archetype is the actual subject matter. Bank Admin / Bank Agent only. |

Three consequences follow:

1. **`status` in an API response is the bank's stage label**, resolved as
   `stage_label or status`. The fallback exists only because `Active` has no stage row
   behind it (see below) — and `Active` is itself a name banks and farmers recognise, so
   nothing archetype-shaped leaks through it.

2. **`status` in an API filter is a stage label, `stage_id`, or `external_code`** — plus
   the literal `Active`. It is resolved per caller, because the valid set is tenant data,
   not a static list. Cross-bank callers (Development Agent, a farmer with applications at
   several banks) match on `label` as well, since two banks' `Verified` stages are
   different rows with different `stage_id`s. Label matching is acceptable for a _filter_,
   which is a search convenience; it is not acceptable for a _write_, which is a durable
   binding — the identifier rule above still governs that path.

3. **Terminality is exposed as a flag, not a name.** Consumers need to know when an
   application is finished, and telling them to hardcode "Disbursed and Rejected are
   terminal" would reintroduce exactly the coupling this design removes. So responses
   carry `is_terminal` and `is_successful` — derived from the archetype, without naming
   it — alongside the stage `sequence`.

### Why `Active` has no stage row

`Active` is the farmer's private pre-submission state. It is deliberately left as an
archetype with no backing stage: banks must not be able to rename or delete it, and no
bank pipeline begins before submission. The cost is a `stage_label or status` fallback,
which is confined to one helper in `a2c_marketplace/stages.py` rather than repeated at
each call site.

The one place this costs something real: a single request that filters on `Active` _and_
on stage names needs an OR across two columns, and Frappe's `get_list` allows only one
`or_filters` group — already spent on `search_query` in `get_all_loans`. That exact
combination is rejected with a 400. It is reachable only by Development Agents and Admins
(bank users cannot see `Active` at all, per `permissions.py`) and only alongside a text
search. Seeding `Active` as a locked, platform-owned stage row would remove the
limitation if it ever becomes a real complaint.

---

## 3. Why no separate `outcome` field

Considered and rejected. `docstatus` alone cannot separate `Completed` from `Rejected`
(both are 1), so a discriminator is needed — but the archetype _state name_ already is
one, and archetype names are platform-owned constants that code can read safely.

A separate `outcome` field would also have to be written on the same save that submits
the document (`before_submit`, not after), or there is a window where docstatus is 1
and outcome is still empty — and docstatus 1 blocks most further writes.

---

## 4. Frappe constraints that shaped this

- **One active Workflow per doctype.** Per-bank pipelines are impossible as Workflows.
- **docstatus is one-way**: `0→0` save, `0→1` submit, `1→1` update-after-submit,
  `1→2` cancel. `1→0` raises `DocstatusTransitionError` (`frappe/model/document.py:1113`).
  So an application cannot be un-finished; reversing means cancel plus amend into a
  **new** document. This is why `Cancelled` exists at all.
- **Frappe ships no status taxonomy.** `Workflow State` is just a free-text name plus
  `icon` and `style`. Every name in the system is our invention.
- **`workflow_state` is an auto-created Custom Field**, added when the Workflow is saved
  (`workflow/doctype/workflow/workflow.py:43`) — which is why it is not in the doctype
  JSON. `apply_status_transition` mirrors it onto `status` (`utils.py:618`).

---

## 5. Status of implementation

**The write path is two-layer and correct. Every read path is one-layer and shows the
wrong layer.**

Built and working:

- The archetype Workflow, its transitions, and the `workflow_state → status` mirror in
  `apply_status_transition` (`api/utils.py`).
- `A2C Loan Status Stage`, bank-scoped via `BANK_SCOPED` in `hooks.py`, with the default
  six-stage pipeline seeded per bank (`patches/seed_default_stages_for_existing_banks.py`).
- `update_loan_status` (`api/v1/loan_applications.py`) and the farmer's
  `submit_application` (`api/v1/farmer/applications.py`), both of which resolve and write
  `stage_id` / `stage_label` alongside the archetype transition.
- `get_stages` / `add_stage` / `sync_stages` (`api/v1/seller/loan_stages.py`).

Not yet built — the read path. Every consumer-facing endpoint returns the archetype and
omits the stage:

| Site                                                           | Problem                                                                                               |
| -------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------- |
| `farmer/applications.py` list + detail                         | returns `doc.status` — a farmer sees `In Transition`, never `Verified`                                |
| `farmer/dashboard.py` recent applications                      | same                                                                                                  |
| `loan_applications.py` `get_all_loans`                         | the CRM list for Dev Agents and bank users selects no stage fields at all                             |
| `loan_applications.py` `get_full_profile`                      | returns the archetype                                                                                 |
| `loan_applications.py` `get_loan_metadata`                     | the status dropdown is read from the archetype Select options                                         |
| `GetAllLoansSchema.validate_statuses` + `get_all_loans` filter | validate against `get_workflow_state_names()`, so `status=Verified` **400s**                          |
| `farmer/applications.py` status filter                         | passes the value straight at the archetype column — `status=Verified` returns **zero rows, silently** |
| `a2c_loan_application.py` `on_update`                          | notification subject reads `"… is now In Transition"`                                                 |

### The work

**1 — Shared helpers in `a2c_marketplace/stages.py`.** All display and filter logic lands
here so no endpoint reimplements it: `get_stage_map(bank)` (cached, invalidated from the
stage doctype's `on_update`/`on_trash`), `status_payload(row)` returning
`{status, stage_id, sequence, is_terminal, is_successful}`, a batched
`build_status_payloads(rows)` for list endpoints, and `resolve_status_filter(value, user)`
reusing `parse_multi_value` from `api/utils.py`. `status_payload` is the single
replacement for the six `stage_label or status` coalesces currently spread across
`loan_applications.py` and `stats_cache.py`.

**2 — Read endpoints return the stage.** Apply the helpers at each site above; every list
query adds `stage_id`, `stage_label`, `bank` to its `fields`. Internal guards
(`doc.status != "Active"` in the farmer endpoints, the permission gates in
`permissions.py`) keep comparing the archetype — they are backend machinery.

**3 — Filters and validation.** Delete `GetAllLoansSchema.validate_statuses`; the valid
set is per-caller tenant data, not a static list, so validation moves to
`resolve_status_filter` at request time. Same replacement in the farmer list endpoint,
which currently fails silently.

**4 — `get_loan_metadata` returns the caller's stages**, ordered by `sequence`, plus
`Active`: their own bank's for a bank user, the union of labels for a bank-unbound caller,
the union across their banks for a farmer. Returning `is_terminal` and `sequence` per entry
lets the frontend build both a dropdown and pipeline columns from one call.

**5 — Summary and dashboard buckets.** `get_loan_summary` drops `by_status` and zero-fills
`stages` from the caller's stage set, so kanban and pipeline views get complete columns.
`stats_cache`'s stored `stage_counts` map is already keyed on `stage_label or status` and
needs no change; zero-fill at read time in `get_dashboard_stats`. `_EXCLUDED_APPLICATION_STATUSES`
and `_PENDING_APPLICATION_STATUS` keep comparing the archetype — that is internal semantics,
and `test_stats_cache.py` asserts they stay valid Select options.

**6 — Notifications.** The `on_update` subject must use the stage label. It currently fires
on `has_value_changed("status")`, so a stage move _within_ one archetype — the common case,
since four of the six default stages are `In Transition` — sends nothing. Fire on a
`stage_label` change too.

### Known debt, deliberately not in this work

- `resolve_bank_stage` accepts a **label** as a write identifier, contradicting §2's
  identifier rule. Filters may match on label; writes should not.
- `sync_stages` does insert, update, rename, reorder and delete-by-omission in one call,
  plus a raw SQL label fan-out, plus unreachable `hasattr`/dict branches (its inputs are
  always pydantic models). Delete-by-omission is a footgun: a client that omits a stage
  triggers its deletion.
- `status_has_tag` / `workflow_state_has_tag` in `api/utils.py` are leftovers from the
  abandoned tag design below, with no live caller.

**Note:** `ruff check` passes on files that fail `py_compile`. It was verified passing
on two files containing Python 2 `except A, B:` syntax. Lint is not a syntax gate here.

### Historical: why the semantic-tag approach was dropped

An earlier iteration tagged workflow states with `is_pending` / `is_success` / etc. so
code could ask about meaning rather than name. Six tag keys were written into
`fixtures/workflow.json` with no custom fields backing them on `Workflow Document State`.
Frappe drops unknown keys on fixture import, so every tag would have read `False`, and
`permissions.py` would `return "1=0"` — **locking every bank user out of every loan
application**. It never surfaced because `bench migrate` could not run in that environment.

It is also unnecessary under this design: with five fixed, platform-owned archetype names,
code can compare names directly. The tags existed only because the state set was editable —
and under the two-layer model, the editable set is the stage layer, which platform code
never compares against by name.

---

## 6. Open questions — for review

1. **Stage deletion / remapping with live applications.** When a bank deletes or
   remaps a stage that applications are currently sitting on, what happens to them?
   Blocking the edit is safest; silently remapping is friendlier but rewrites history.
   This will come up fast in practice and is much cheaper to decide now than after the
   first support ticket. **Undecided.**

2. **Is withdrawal a real flow?** `Cancelled` is currently justified only as an admin
   escape hatch for reversing a wrong `Completed`. If farmers can withdraw, it needs a
   real transition and a role. **Undecided.**

3. **Pipeline granularity.** Per bank, or per bank _and_ loan product (microloan vs
   equipment finance running different stage sets)? Recommend building per-bank now but
   putting resolution behind one `resolve_pipeline(application)` function so a
   product-level override can be added later without touching call sites. **Undecided.**

4. **Which layer does the API's `status` field show?** ~~Likely both fields, always.~~
   **Resolved: the stage, everywhere.** The premise was wrong — platform consumers do
   _not_ need the archetype. The farmer app and the CRM want `Verified` just as much as a
   bank does; nobody outside the backend wants `In Transition`. Returning both would have
   left the archetype in every payload for a need that does not exist. What consumers
   actually need from the archetype is terminality, and that ships as `is_terminal` /
   `is_successful` flags instead. See §2, _The exposure rule_.

5. **Existing data migration.** **Resolved.** `Draft` / `Processing` / `Approved` /
   `Rejected` were mapped onto the archetype (`Draft→Active`, `Processing→In Transition`,
   `Approved→Completed`, `Rejected→Rejected`) with stage backfill, via the legacy-status
   map in `patches/create_lead_loan_workflows.py` and
   `patches/backfill_processing_loan_stage.py`. `Approved` and `Completed` are both
   docstatus 1, so no un-submit was needed.

6. **Cross-bank stage filtering.** **Resolved, with a caveat.** Bank A's `Verified` and
   Bank B's `Verified` are different rows with different `stage_id`s, so a Development
   Agent filtering across banks has no per-bank identifier to bind to. Filters therefore
   match on `label` in addition to `stage_id` and `external_code` — acceptable because a
   filter is a search convenience, not a durable binding. The alternative considered was
   promoting `external_code` to a platform-controlled cross-bank vocabulary; that is left
   for the CRM inbound direction it was designed for.

---

## 7. Reference: current visibility rules

The archetype must preserve these.

**`A2C Development Agent`** — bank-unbound but not a platform admin
(`permissions.py:235-241`, `:264-272`): sees **all banks** and **all statuses**
including `Draft` (the draft gate at `:276` is in the bank-user branch only), but is
restricted to **agent-sourced** applications by `AGENT_SOURCED_ONLY` (`:232`) —
`application_source != 'Self Service'`. Mirrored for single-doc reads at `:334-335`.

**Bank users** (`A2C Bank Admin`, `A2C Bank Agent`) — own bank only, and only past
`Draft` (`:276`, `:358`). Under the new model: only once the archetype state is
`In Transition` or later.

**`A2C Farmer`** — scoped by `farmer_profile`, not by bank or `owner` (`:252-260`); no
draft gate, since `Active` is the farmer's own working stage.

---

## 8. The fixtures gotcha

`fixtures/workflow.json` is the durable definition of the archetype. **Fixtures sync
_after_ patches on every `bench migrate` and overwrite the entire states/transitions
child table.** Documented the hard way in
`patches/update_loan_workflow_for_farmer.py:22-25`, where a transition added only by a
patch was wiped by the next migrate.

**Rule: edit the fixture file, not the Desk UI.** Desk edits are transient until
`bench export-fixtures` writes them back and they are committed. Worth a test asserting
the DB workflow matches the fixture, so a forgotten export fails CI rather than
vanishing on the next deploy.
