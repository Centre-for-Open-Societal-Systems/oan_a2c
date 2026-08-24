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

Codex implemented steps 1–6 of the _previous_ plan. Where that leaves us:

| Previous step                                     | State                                                                      |
| ------------------------------------------------- | -------------------------------------------------------------------------- |
| 1. Transitions read from the Workflow doc, cached | **Keep.** Still correct and needed.                                        |
| 2. 403 vs 400 error split                         | **Keep.** Independent of layering.                                         |
| 3. Role gate on `update_loan_status`              | **Keep.** Independent of layering.                                         |
| 4. Semantic tags (`is_pending`, `is_success`, …)  | **Drop.** Broken _and_ superseded — see below.                             |
| 5. Auto-generated Select options                  | **Rework.** Depends which layer `status` shows.                            |
| 6. Runtime pydantic validator                     | **Rework.** Must validate against the bank's stages, not archetype states. |

**Step 4 was broken on delivery.** Six tag keys were written into
`fixtures/workflow.json` with no custom fields backing them on
`Workflow Document State`. Frappe drops unknown keys on fixture import, so every tag
would read `False`, and `permissions.py` would `return "1=0"` — **locking every bank
user out of every loan application**. It never surfaced because `bench migrate` could
not run in this environment.

It is also unnecessary under the new design: with five fixed, platform-owned archetype
names, code can compare names directly. The tags existed only because the state set was
editable.

**Note:** `ruff check` passes on files that fail `py_compile`. It was verified passing
on two files containing Python 2 `except A, B:` syntax. Lint is not a syntax gate here.

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

4. **Which layer does the API's `status` field show?** The bank label is what external
   callers will treat as "the real status", but platform consumers (farmer app,
   dashboards) need the archetype. Likely both fields, always. **Leaning: return both.**

5. **Existing data migration.** Current rows hold `Draft` / `Processing` / `Approved` /
   `Rejected`. Mapping to the archetype is straightforward
   (`Draft→Active`, `Processing→In Transition`, `Approved→Completed`,
   `Rejected→Rejected`), but `Approved` rows are currently docstatus 1 and
   `Completed` is also docstatus 1 — so no un-submit is needed. **Verify** no row
   relies on the old `Approved`-is-not-terminal assumption before migrating.

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
