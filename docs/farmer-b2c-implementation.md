# Farmer B2C Flow — Implementation Specification

**Status:** Rev 2, decisions locked, two open questions (§2.2)
**Date:** 18 Aug 2026
**Repos:** `oan_a2c` (backend) · `OAN-Access-To-Credit-System` (frontend)
**Companion:** the reviewed plan is published at `claude.ai/code/artifact/a7ab5aa3-c4a4-4c92-8e2f-5d6dfd28d7a6`

---

## 1. Scope

A farmer signs in, browses the loan catalog, builds an application as a `Draft`, and submits it with
consent — at which point it becomes visible to the bank and the user account is permanently bound to
an `A2C Farmer Profile`.

**Authentication is OAuth against the Fayda farmer registry.** The registry asserts the farmer's
identity; the platform does not hold a farmer credential of its own. Until that integration lands,
development uses ordinary email + password through the existing `oan_a2c.api.auth.login`, and every
part of this document that mentions a farmer password is describing that stand-in.

A phone number is still collected and stored on the `User` — it is how the consent webhook matches an
account to an `A2C Farmer Profile` — but it is **not** an identity. Nothing derives a login from it.

```
register (email + password)     →  User only. No profile, no phone claim, no link.
login  (email + password)       →  existing oan_a2c.api.auth.login — DEV STAND-IN;
                                   production is OAuth against the Fayda registry
browse catalog (authenticated)  →  new farmer catalog endpoint, Active products, all banks
start application               →  hidden A2C Lead + A2C Credit Information + Draft application
complete draft                  →  farmer edits their own Draft
submit  ──► consent ──► binds user↔profile ──► Draft→Processing ──► bank notified
```

Everything before consent is deliberately unverified and low-stakes. Consent is the identity gate,
the data-protection record, and the moment the account becomes a farmer profile.

### 1.1 What is reused unchanged

| Component                             | Why it needs no change                                                |
| ------------------------------------- | --------------------------------------------------------------------- |
| `oan_a2c.api.auth.login`              | email + password works unchanged; it is the development stand-in only |
| `oan_a2c.api.auth.refresh` / `logout` | token lifecycle is role-agnostic                                      |
| `_classify_user_type`                 | already returns `"farmer"` for `FARMER_ROLE`                          |
| `src/features/auth/rbac.ts`           | `farmer` kind and its three routes already exist                      |
| `apply_status_transition`             | `("Draft", "Processing") → "Send for Review"` already mapped          |
| `notify_users()`                      | existing writer over Frappe's `Notification Log`                      |
| `api/v1/consent/*`                    | consent runs on its existing lead-anchored path                       |

---

## 2. Decisions

### 2.1 Locked

| #   | Decision                                                                                                                                                          | Consequence for the build                                                                                   |
| --- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------- |
| D1  | Discovery is **behind login**                                                                                                                                     | No guest endpoint, no middleware allowlist entry, no public route. `/discover-loans` stays as it is.        |
| D2  | `Draft` is the farmer's **working stage**; `Processing` is bank-visible                                                                                           | Farmer scope must **not** inherit the Draft gate. Workflow needs a farmer `Draft → Processing` transition.  |
| D3  | ~~Synthesised email, phone is the real identifier~~ **Superseded.** Identity comes from the Fayda registry over OAuth; email + password is a development stand-in | `email` is required for every role. No address is synthesised, and no login is derived from a phone number. |
| D4  | **Consent on every submission**                                                                                                                                   | No application reaches `Processing` without an approved consent record.                                     |
| D5  | **No SMS/email delivery exists**                                                                                                                                  | No self-service password reset. Ship an admin-side reset over the existing temporary-password handshake.    |

### 2.2 Open — these block BE-103

**D6 — Is B2C consent the real Fayda/OTP flow, or a first-party checkbox?**

The only consent flow that exists is Fayda + OTP via OpenG2P, and every schema is anchored on a
`lead_id` (`api/v1/consent/consent.py:27-49`). If consent is the real flow, binding a user to a
profile at that moment is safe because identity is verified. If it is a checkbox, binding on it lets
anyone who types a farmer's phone number inherit that farmer's Fayda-verified profile and every
application on it.

_Recommendation:_ keep Fayda + OTP **at the consent step only**. "Bypass Fayda and OTP" then means
"bypass them for account creation", which is the actual friction being removed.

**D7 — Should Bank Agents lose read access to loan applications?**

Bank Agent currently holds `read` on `A2C Loan Application` and can Approve/Reject from `Processing`
(`a2c_loan_application.json:396-403`, `fixtures/workflow.json:306-341`). Removing that is a
product-wide change, not a B2C one. If the intent was only "bank staff must not see a half-finished
application", D2's Draft gate already guarantees it and no permission change is needed.

---

## 3. Data model

### 3.1 `A2C Loan Application` — add `farmer_profile` _(fixes a live bug)_

`create_loan_application` sets `loan_app.farmer_profile = farmer_profile.name`
(`api/v1/loan_applications.py:866`) and returns it in the response (`:897`), but the field does not
exist on the doctype. Frappe stores an unknown attribute on the in-memory document only — it is never
persisted and reads back empty. The response looks correct because it is built from that same object.
`get_full_profile` works around it by going application → `lead_id` → `A2C Lead.farmer_profile`
(`:264`).

Add to `openagrinet_access_to_credit/doctype/a2c_loan_application/a2c_loan_application.json`, in
`links_section` immediately after `lead_id` in both `field_order` and `fields`:

```json
{
 "fieldname": "farmer_profile",
 "fieldtype": "Link",
 "label": "Farmer Profile",
 "options": "A2C Farmer Profile",
 "read_only": 1,
 "search_index": 1
}
```

`search_index` is required, not cosmetic: this column is the farmer's permission query condition and
is hit on every list read.

### 3.2 `A2C Farmer Profile` — add `user`

```json
{
 "fieldname": "user",
 "fieldtype": "Link",
 "label": "User Account",
 "options": "User",
 "read_only": 1,
 "unique": 1,
 "search_index": 1
}
```

`unique` matters — without it two accounts can bind to one profile. Note the doctype already has
`phone_number` with `"unique": 1` (`:83-89`), so match-or-create must handle that collision
explicitly rather than letting the insert throw.

### 3.3 `A2C Lead` — add a self-service source

`lead_source` is `reqd` with options `Missed Call\nIVR\nSMS\nAgent Entry` (`a2c_lead.json:65-70`).
A B2C lead has no valid value. Add `Self Service`:

```json
"options": "Missed Call\nIVR\nSMS\nAgent Entry\nSelf Service"
```

### 3.4 New doctype `A2C Saved Product`

Module: `a2c_marketplace`. **Not** added to `BANK_SCOPED` — it is user-owned, not bank-owned.

| Field          | Type                      | Notes                  |
| -------------- | ------------------------- | ---------------------- |
| `loan_product` | Link → `A2C Loan Product` | `reqd`, `search_index` |

`owner` is set by Frappe and is the scoping key; no separate `user` field. Permissions are a single
DocPerm row for `A2C Farmer` with `read`/`create`/`delete` and `"if_owner": 1`, which Frappe honours
natively in `db_query` — no permission hook needed.

`autoname: "hash"`, with a composite unique index added in the patch (§4.4).

---

## 4. Patches

`patches.txt` is **append-only** and marked `merge=union` in `.gitattributes`. Add each new line at
the end, never reorder, and keep the trailing newline.

```
oan_a2c.patches.add_farmer_role
oan_a2c.patches.backfill_loan_application_farmer_profile
oan_a2c.patches.update_loan_workflow_for_farmer
oan_a2c.patches.add_saved_product_unique_index
```

### 4.1 `add_farmer_role.py`

Fixtures load _after_ patches on a clean install, so the role must be created here as well as added
to the fixture list — the same reasoning as `create_lead_loan_workflows._ensure_roles`.

```python
import frappe

from oan_a2c.a2c_marketplace.roles import FARMER_ROLE


def execute():
	"""Create the A2C Farmer role with desk_access = 0.

	desk_access is the whole point: User.set_system_user() classifies any holder
	of a desk-access role as a System User, which would give every farmer desk
	access and consume a seat. With desk_access = 0 they are Website Users.
	"""
	if frappe.db.exists("Role", FARMER_ROLE):
		frappe.db.set_value("Role", FARMER_ROLE, "desk_access", 0)
		return

	frappe.get_doc(
		{
			"doctype": "Role",
			"role_name": FARMER_ROLE,
			"desk_access": 0,
		}
	).insert(ignore_permissions=True)
```

Also add `"A2C Farmer"` to the `Role` fixture filter in `hooks.py:271-287`.

### 4.2 `backfill_loan_application_farmer_profile.py`

```python
import frappe


def execute():
	"""Populate the new farmer_profile column on existing applications.

	Every pre-B2C application reached its profile through its lead, so the lead
	is the only source of truth for the backfill. Applications with no lead or
	no profile on the lead are left null and are reported, not guessed at.
	"""
	frappe.db.sql(
		"""
		UPDATE `tabA2C Loan Application` app
		INNER JOIN `tabA2C Lead` lead ON lead.name = app.lead_id
		SET app.farmer_profile = lead.farmer_profile
		WHERE app.farmer_profile IS NULL
		  AND lead.farmer_profile IS NOT NULL
		"""
	)  # bank-scope-exempt: migration over all tenants by design

	orphans = frappe.db.count("A2C Loan Application", {"farmer_profile": ["is", "not set"]})
	if orphans:
		frappe.logger().warning(
			f"backfill_loan_application_farmer_profile: {orphans} application(s) have no "
			"farmer_profile; they are invisible to farmer scoping until repaired."
		)
```

### 4.3 `update_loan_workflow_for_farmer.py`

`create_lead_loan_workflows` has already run on every existing site and will not re-run. Amend
`_create_loan_workflow()` **in that file** (so clean installs are correct) and add this patch to
re-apply it on sites that already migrated. `_upsert_workflow` deletes and recreates, so it is safe
to call again.

```python
from oan_a2c.patches.create_lead_loan_workflows import (
	_create_loan_workflow,
	_ensure_workflow_states,
)


def execute():
	"""Re-apply the loan workflow so it carries the farmer transition."""
	_ensure_workflow_states()
	_create_loan_workflow()
```

The amended `_create_loan_workflow()` in `create_lead_loan_workflows.py`:

```python
	states = [
		{"state": "Draft", "doc_status": "0", "allow_edit": "A2C Development Agent"},
		{"state": "Draft", "doc_status": "0", "allow_edit": FARMER_ROLE},
		{"state": "Processing", "doc_status": "0", "allow_edit": "A2C Bank Agent"},
		{"state": "Approved", "doc_status": "1", "allow_edit": "System Manager"},
		{"state": "Rejected", "doc_status": "1", "allow_edit": "System Manager"},
	]
	transitions = [
		{
			"state": "Draft",
			"action": "Send for Review",
			"next_state": "Processing",
			"allowed": "A2C Development Agent",
		},
		{
			"state": "Draft",
			"action": "Send for Review",
			"next_state": "Processing",
			"allowed": FARMER_ROLE,
		},
		{"state": "Processing", "action": "Approve", "next_state": "Approved", "allowed": "A2C Bank Agent"},
		{"state": "Processing", "action": "Reject", "next_state": "Rejected", "allowed": "A2C Bank Agent"},
	]
```

Add `FARMER_ROLE` to that module's `WORKFLOW_ROLES` import and tuple, and set `desk_access = 0` for
it in `_ensure_roles` (it currently hardcodes `1`).

`fixtures/workflow.json` must be re-exported after migrating, or hand-edited to match — it is the
exported site state and will otherwise reintroduce the old transitions on the next fixture sync.
`_WORKFLOW_TRANSITION_ACTIONS` in `api/utils.py:528-532` needs **no** change: the farmer uses the same
`("Draft", "Processing") → "Send for Review"` entry.

### 4.4 `add_saved_product_unique_index.py`

```python
import frappe


def execute():
	"""One bookmark per farmer per product, enforced by the database."""
	frappe.db.add_unique("A2C Saved Product", ["owner", "loan_product"], constraint_name="unique_owner_product")
```

---

## 5. Permissions

### 5.1 Do **not** add `FARMER_ROLE` to `BANK_UNBOUND_ROLES`

`BANK_UNBOUND_ROLES` lifts bank scoping on _every_ bank-scoped doctype the role has DocPerm for. Since
the farmer holds DocPerm on `A2C Loan Application`, making them unbound would let any farmer read
**every** application in the system. Farmers stay bank-bound; the catalog gets its own branch instead.

### 5.2 `a2c_marketplace/permissions.py` — new helpers

```python
from oan_a2c.a2c_marketplace.roles import BANK_UNBOUND_ROLES, FARMER_ROLE


def is_farmer(user=None):
	"""True if the user is a marketplace applicant (scoped by ownership, not bank)."""
	if not user:
		user = frappe.session.user
	return FARMER_ROLE in frappe.get_roles(user)


def get_user_farmer_profile(user=None):
	"""The A2C Farmer Profile bound to `user`, or None before consent binds one.

	A registered farmer who has never completed a consent has no profile. That is
	a normal state, not an error: it means they have nothing to see yet.
	"""
	if not user:
		user = frappe.session.user
	return frappe.db.get_value("A2C Farmer Profile", {"user": user}, "name")
```

### 5.3 `loan_application_scope_query` — farmer branch

The farmer branch runs **before** the bank branch, because a farmer has no bank binding and would
otherwise fall through to `1=0`. It deliberately omits the Draft gate: under D2, `Draft` is the
farmer's own working stage.

```python
def loan_application_scope_query(user=None):
	if not user:
		user = frappe.session.user

	# Farmers are scoped by ownership, not by bank. Checked first: a farmer has no
	# A2C Participating Bank binding, so bank_scope_query would fail them closed at
	# 1=0. No Draft gate here -- Draft is the farmer's own working stage (unlike a
	# bank user, for whom Draft is the Development Agent's private stage).
	if is_farmer(user) and not is_bank_unbound(user):
		profile = get_user_farmer_profile(user)
		if not profile:
			return "1=0"
		return f"`farmer_profile` = {frappe.db.escape(profile)}"

	base = bank_scope_query(user)
	if is_bank_unbound(user):
		return base

	draft_gate = "`status` != 'Draft'"
	return f"({base}) and {draft_gate}" if base else draft_gate
```

### 5.4 `loan_product_scope_query` — new hook for the catalog

Registered separately for `A2C Loan Product` only, for the same reason
`loan_application_scope_query` is separate: `status` does not exist on the other bank-scoped
doctypes, so the shared hook must stay column-agnostic.

```python
def loan_product_scope_query(user=None):
	"""permission_query_conditions for A2C Loan Product.

	A farmer browses the marketplace across every bank but only ever sees Active
	products -- Draft and Archived belong to the owning bank's catalog workspace.
	Everyone else keeps plain bank scoping.
	"""
	if not user:
		user = frappe.session.user

	if is_farmer(user) and not is_bank_unbound(user):
		return "`status` = 'Active'"

	return bank_scope_query(user)
```

### 5.5 `bank_scope_doc` — farmer branch

Frappe's `has_permission` controllers can only _deny_ (`frappe/permissions.py:484`), so this hook
returning `False` blocks a farmer from their own document unless it is handled here.

```python
def bank_scope_doc(doc, user=None):
	if not user:
		user = frappe.session.user

	if is_bank_unbound(user):
		return True

	# Mirror of the query hooks above, for single-doc reads (get_doc, has_permission).
	if is_farmer(user):
		if doc.doctype == "A2C Loan Product":
			return doc.get("status") == "Active"
		if doc.doctype == "A2C Loan Application":
			profile = get_user_farmer_profile(user)
			allowed = bool(profile) and doc.get("farmer_profile") == profile
			if not allowed:
				frappe.logger("bank_scope").warning(
					f"Denied cross-farmer access: user={user} profile={profile} "
					f"{doc.doctype}={doc.name} doc_profile={doc.get('farmer_profile')}"
				)
			return allowed
		return False

	... # existing bank logic unchanged
```

### 5.6 `A2C Farmer Profile` and `A2C Consent Request` scoping

Neither doctype is in `BANK_SCOPED`, so neither has any query hook today — a farmer with plain `read`
DocPerm would see **every** farmer profile in the system. Both need an owner-scoped hook registered in
`hooks.py`:

```python
def farmer_own_profile_query(user=None):
	"""permission_query_conditions for A2C Farmer Profile."""
	if not user:
		user = frappe.session.user
	if not is_farmer(user) or is_bank_unbound(user):
		return ""
	profile = get_user_farmer_profile(user)
	return f"`name` = {frappe.db.escape(profile)}" if profile else "1=0"


def farmer_own_consent_query(user=None):
	"""permission_query_conditions for A2C Consent Request."""
	if not user:
		user = frappe.session.user
	if not is_farmer(user) or is_bank_unbound(user):
		return ""
	profile = get_user_farmer_profile(user)
	return f"`farmer` = {frappe.db.escape(profile)}" if profile else "1=0"
```

> Confirm `A2C Consent Request.farmer` holds the profile name and not a Fayda ID before wiring this —
> the doctype carries both `farmer` and `farmer_fayda_id`.

### 5.7 `hooks.py` registrations

```python
permission_query_conditions = {d: "oan_a2c.a2c_marketplace.permissions.bank_scope_query" for d in BANK_SCOPED}
permission_query_conditions["A2C Loan Application"] = (
	"oan_a2c.a2c_marketplace.permissions.loan_application_scope_query"
)
# A2C Loan Product adds a catalog-visibility branch on top of bank scoping: a farmer
# browses across banks but only sees Active products. See permissions.py.
permission_query_conditions["A2C Loan Product"] = (
	"oan_a2c.a2c_marketplace.permissions.loan_product_scope_query"
)
# Neither of these is bank-scoped, so neither had a hook before farmers existed.
permission_query_conditions["A2C Farmer Profile"] = (
	"oan_a2c.a2c_marketplace.permissions.farmer_own_profile_query"
)
permission_query_conditions["A2C Consent Request"] = (
	"oan_a2c.a2c_marketplace.permissions.farmer_own_consent_query"
)
```

**Assign, never append.** Multiple hooks for one doctype are joined with `AND`
(`frappe/model/db_query.py:1157`), so registering a farmer hook _alongside_ the bank hook produces
`farmer_condition AND 1=0` — an empty list with no error.

### 5.8 DocPerms

| Doctype                | `A2C Farmer` grants                                                                                                                                             |
| ---------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `A2C Loan Application` | `read`, `create`, `write` — `if_owner` is **not** used; scoping is by `farmer_profile` via the hook, because an agent-created application is owned by the agent |
| `A2C Farmer Profile`   | `read`, `write`                                                                                                                                                 |
| `A2C Loan Product`     | `read` only                                                                                                                                                     |
| `A2C Consent Request`  | `read`, `create`                                                                                                                                                |
| `A2C Saved Product`    | `read`, `create`, `delete`, `if_owner: 1`                                                                                                                       |
| `A2C Lead`             | `read`, `create` — required for the hidden lead (§6.3)                                                                                                          |

The `A2C Lead` grant deserves a second look at review time: it is the one place a farmer touches a
doctype built for agents. Scope it with an owner-based hook if the review is uncomfortable with it.

---

## 6. API

New package `oan_a2c/api/v1/farmer/` — `__init__.py`, `catalog.py`, `applications.py`, `bookmarks.py`.

Decorator order is fixed by house rule, outermost first:
`@frappe.whitelist(...)` → `@validate_request(Schema)` → `@handle_api_errors` → `@bank_scoped` / `@require_bank_role`.

Every endpoint below is `allow_guest=False` (D1) and calls
`frappe.has_permission(..., throw=True)` **before** any `frappe.db.exists(...)` — reversed, the error
leaks whether a document exists, which on this surface means leaking whether a person has applied for
a loan.

### 6.1 `farmer/catalog.py`

```python
@frappe.whitelist(allow_guest=False)
@validate_request(ListCatalogSchema)
@handle_api_errors
def list_catalog(**kwargs):
	"""Active loan products across every bank, for a signed-in farmer.

	Reads through get_list so loan_product_scope_query applies: farmers get the
	Active-only, bank-unfiltered branch; anyone else keeps bank scoping. Modelled
	on api.v1.loan_applications.browse_products, which is the same shape for the
	Development Agent.
	"""
	frappe.has_permission("A2C Loan Product", "read", throw=True)
	...
```

Category and tag facets come from `A2C Term Relationship`, which **is** bank-scoped and has no farmer
branch — a farmer reading it through `get_list` gets nothing. Fetch them with `get_all` filtered to
the product names already returned by the permission-checked query above, and mark the call:

```python
	# bank-scope-exempt: product_names came from a get_list that already applied
	# loan_product_scope_query, so this only decorates rows the caller may see.
	cat_rows = frappe.get_all(
		"A2C Term Relationship",
		filters={"loan_product": ["in", product_names], "term_type": "Category"},
		fields=["loan_product", "term_category"],
	)
```

`get_catalog_product(product_id)` is the same shape for a single product, and must re-check
`frappe.has_permission("A2C Loan Product", "read", doc=product_id, throw=True)` so `bank_scope_doc`'s
Active gate applies to direct fetches.

### 6.2 `farmer/applications.py` — `get_my_applications`

A farmer must not reach `get_all_loans`: it exposes loan-officer tabs, phone-prefix search and
free-text filters across `first_name` / `last_name` / `phone_number`. Even correctly scoped that is
a needlessly large surface.

```python
@frappe.whitelist(allow_guest=False)
@validate_request(GetMyApplicationsSchema)
@handle_api_errors
def get_my_applications(**kwargs):
	"""The caller's own applications, in every state including Draft.

	Scoping is the permission hook's job (loan_application_scope_query matches on
	farmer_profile), not this function's -- so a farmer with no profile yet gets an
	empty list rather than an error, and an agent-created application shows up here
	the moment consent binds its profile to this account.
	"""
	frappe.has_permission("A2C Loan Application", "read", throw=True)

	filters = {}
	if kwargs.get("status"):
		filters["status"] = ["in", parse_multi_value(kwargs["status"], ("Draft", "Processing", "Approved", "Rejected"))]
	...
```

### 6.3 `farmer/applications.py` — `start_application`

Consent and `create_loan_application` are both anchored on a `lead_id`
(`api/v1/consent/consent.py:27-49`, `api/v1/loan_applications.py:791`). Rather than fork both, the
B2C flow creates a lightweight lead. This also makes agent-created and self-created applications the
same shape, which is what lets a farmer see everything filed for them (§7).

```python
@frappe.whitelist(allow_guest=False, methods=["POST"])
@validate_request(StartApplicationSchema)
@handle_api_errors
def start_application(**kwargs):
	"""Open a Draft application against a chosen product.

	Creates the A2C Lead + A2C Credit Information the existing pipeline expects, so
	consent and create_loan_application run on their normal paths. The lead is an
	implementation detail: it is never shown to the farmer and carries lead_source
	"Self Service" so it is distinguishable in agent queues and reporting.
	"""
	frappe.has_permission("A2C Loan Product", "read", doc=kwargs["loan_product"], throw=True)
	frappe.has_permission("A2C Loan Application", "create", throw=True)
	...
```

Order inside the endpoint:

1. Resolve the product; `bank` comes from it (`bank` is `reqd` on the application, so the product must
   be chosen before an application can exist).
2. Create `A2C Lead` — `phone_number` from the user's `mobile_no`, `lead_source = "Self Service"`,
   `status = "Active"`.
3. Create `A2C Credit Information` — `lead`, `loan_product`, `loan_type`, `loan_amount`,
   `purpose_message` are all `reqd`.
4. Call the existing `create_loan_application(lead_id=...)`, which requires
   `lead.farmer_profile` — **so on the first application the profile must exist**. See §6.4: for an
   unbound user, create the unverified profile here and bind it at consent, or block `start_application`
   until consent. This ordering is the single largest open detail in the flow and depends on D6.

### 6.4 `farmer/applications.py` — `submit_application`

```python
@frappe.whitelist(allow_guest=False, methods=["POST"])
@validate_request(SubmitApplicationSchema)
@handle_api_errors
def submit_application(**kwargs):
	"""Move the caller's Draft to Processing, gated on an approved consent.

	Consent is mandatory (D4) and is checked before the transition rather than
	after, so a failed consent leaves the application in Draft where the farmer can
	retry -- never in a half-submitted state a bank can already see.
	"""
	application_id = kwargs["application_id"]
	frappe.has_permission("A2C Loan Application", "write", doc=application_id, throw=True)

	doc = frappe.get_doc("A2C Loan Application", application_id)

	consent = frappe.db.get_value(
		"A2C Consent Request", {"lead": doc.lead_id, "status": "Approved"}, "name"
	)
	if not consent:
		frappe.throw(
			_("Consent is required before an application can be submitted."),
			frappe.ValidationError,
		)

	apply_status_transition(doc, "Processing")

	# Best-effort: the application is submitted whether or not the bank's bell rings.
	try:
		notify_users(
			get_bank_members(doc.bank, BANK_ROLES),
			subject=_("New loan application {0}").format(doc.name),
			doctype="A2C Loan Application",
			docname=doc.name,
		)
	except Exception:
		frappe.logger().warning(f"submit_application: notification failed for {doc.name}")

	return success_response(data={"application_id": doc.name, "status": doc.status})
```

`apply_status_transition` mirrors `workflow_state` onto `status` itself (`api/utils.py:571-573`), so
nothing else needs to touch `status` — and per house rule nothing may.

### 6.5 Profile binding at consent

Not whitelisted. Called from the consent completion path, next to the existing match-or-create in
`api/v1/webhook_consent_data.py:307-326`:

```python
def bind_user_to_profile(user, profile_name):
	"""Bind a self-registered account to its farmer profile, once, at consent.

	Consent is the only point in the B2C flow where identity is asserted, so it is
	the only safe place to make this link -- before it, the phone number is
	unverified and binding would hand over whatever profile already holds it.
	Idempotent: re-running a consent must not orphan an existing binding.
	"""
```

Rules:

- Match by `phone_number`; create only if absent.
- If the matched profile already has a different `user`, **stop and raise** — that is a takeover
  attempt or a data error, never something to silently overwrite.
- Set `A2C Lead.farmer_profile` at the same time, so `create_loan_application` keeps working.

### 6.6 `farmer/bookmarks.py`

`save_product`, `remove_saved_product`, `get_saved_products`. Scoping is entirely `if_owner` DocPerm
plus the unique index; no hook. `get_saved_products` resolves product details through the same
`get_list` path as the catalog so an archived product silently drops out of the list rather than
appearing as a dead card.

### 6.7 Registration and auth changes

`api/v1/auth.py`:

```python
SELF_REGISTERABLE_ROLES = {BANK_ADMIN_ROLE, DEVELOPMENT_AGENT_ROLE, FARMER_ROLE}
```

- `email` is **required** for every role, farmers included — there is no identifier to derive one
  from. Note that `SafeEmail` is `Annotated[str | None, ...]`, so the schema validates format but
  cannot enforce presence: `validate_request` always supplies the signature default, so a missing
  address reaches the function as a legitimate `None`. The presence check in `register_user` is the
  only thing standing between a farmer and an account they can never log in to.
- `phone_number` stays required and stays on the `User`, because the consent webhook binds a profile
  by `mobile_no`. It is not an identity — nothing may derive a login from it.
- `_resolve_login_id` (`api/auth.py:35`) is **left alone**. It predates this work, serves every role,
  and the frontend exercises it for the bank-agent temporary-password flow
  (`authApi.test.ts:52`). Removing it would break staff login, not farmer login.
- **Neutral duplicate responses** (`:92-104`): registration currently returns a soft
  "you already have an account" for a known email but _throws_ for a known phone. Phone-first, that is
  a "is this number registered?" oracle. Return the same soft response on both paths.
- **Rate limits**: `login` is 10/60s and `register_user` 5/60s, both keyed on `request_ip`
  (`api/auth.py:245`, `api/v1/auth.py:87`). Behind carrier NAT that locks out villages. Add a
  phone-keyed bucket and widen the IP bucket for these two endpoints.
- No `doc_events` hook on `User` — binding happens at consent (§6.5), so the original plan's
  `after_insert` handler is not built at all.

### 6.8 Admin password reset (D5)

With no delivery channel, a farmer who forgets their password is locked out permanently. Mirror
`seller.onboarding.reset_member_password`: an admin sets a temporary password with
`a2c_must_change_password = 1`, and the farmer rotates it through the existing
`api.auth.set_initial_password`. Both halves of that handshake already exist and are tested — this is
a new caller, not a new mechanism.

---

## 7. Sequence — first application

```
1. register            User created. No profile. No phone claim.
2. login               JWT, user_type "farmer". Email + password in development;
                       an OAuth exchange with the Fayda registry in production —
                       steps 3-8 are identical either way, they only read the JWT.
3. list_catalog        Active products, all banks (loan_product_scope_query).
4. start_application   Lead (Self Service) + Credit Information + Draft application.
5. …farmer edits Draft…  allow_edit on Draft includes A2C Farmer.
6. consent             Fayda/OTP per D6 → profile matched-or-created → bound to user.
7. submit_application  Consent verified → Draft→Processing → status mirrored → bank notified.
8. get_my_applications Now also returns any application an agent filed against that profile.
```

Step 8 is the payoff of binding at consent: the farmer's view is anchored on the _profile_, not the
account, so agent-created and self-created applications converge the moment identity is asserted.

---

## 8. Frontend

The farmer screens already exist and render entirely from
`src/features/(farmer-application)/discover-loans/data/mockLoans.ts`. Nothing calls the API. The work
is wiring, not building.

| Path                                             | Change                                                                                                                                                                                                                                                      |
| ------------------------------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `src/features/(farmer-application)/index.ts`     | **create** — the boundaries rule imports features through a barrel; this feature has none                                                                                                                                                                   |
| `src/features/(farmer-application)/api/`         | **create** — per-feature service object, matching the `seller` and `leads` features                                                                                                                                                                         |
| `…/discover-loans/data/mockLoans.ts`             | delete once the catalog is wired                                                                                                                                                                                                                            |
| `src/app/(portal-account)/login/farmer/page.tsx` | email + password; drop the `farmerId` field and the simulated delay. Build it so the credential form is a swappable block — production replaces it with an OAuth redirect to the Fayda registry, and everything downstream only reads the resulting session |
| `src/app/(portal-account)/signup/farmer/`        | **create** — no signup route exists                                                                                                                                                                                                                         |
| `src/app/api/auth/register/route.ts`             | optional; registration sets no cookies, so `/api/proxy/api/method/oan_a2c.api.v1.auth.register_user` is sufficient                                                                                                                                          |
| `src/features/auth/rbac.ts`                      | no change — `farmer` and its three routes already exist and D1 keeps them protected                                                                                                                                                                         |

Two corrections to the original plan: there is no `/api/proxy/auth/login` — login **must** use the
cookie-setting BFF route `/api/auth/login`, since the generic proxy does not set the httpOnly cookies.
And keep `OtpVerificationPopup`: if D6 lands on real consent, the OTP step reappears at submission
time and that component renders it.

---

## 9. Tests

`tests/test_api_decorator_enforcement.py` AST-scans `api/` and fails any `@frappe.whitelist` function
missing `@handle_api_errors`. Every endpoint below needs at least one success-path and one error-path
test. Fixtures must be created per-run with a `frappe.generate_hash()` suffix and torn down in
`tearDownClass` — the `if not frappe.db.exists(...)` reuse pattern passes locally on leftover data and
fails on clean CI (`docs/refactor-test-isolation.md`).

| File                                        | Cases                                                                                                                                                                                                                                           |
| ------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `test_bank_scope_enforcement.py` _(extend)_ | farmer sees own application incl. Draft; sees **zero** of another farmer's; bank user still sees no Draft; farmer with no profile gets an empty list, not an error                                                                              |
| `test_farmer_catalog.py`                    | Active products across banks; Archived/Draft excluded; direct fetch of a non-Active product denied                                                                                                                                              |
| `test_farmer_applications.py`               | start → draft → submit; submit without consent rejected; submit on another farmer's application denied                                                                                                                                          |
| `test_farmer_auth.py`                       | registration without an email is rejected; no address is ever synthesised; duplicate phone and duplicate email return the _same_ response; a staff account can still resolve by phone, so the farmer change did not regress `_resolve_login_id` |
| `test_saved_products.py`                    | save, list, delete; duplicate save rejected by the unique index; one farmer cannot see another's                                                                                                                                                |
| `test_workflow_farmer.py`                   | farmer Draft→Processing succeeds; farmer Processing→Approved denied                                                                                                                                                                             |

The first row is the one that matters most: it is the test that would have caught the `1=0` trap.

---

## 10. Rollout order

1. **BE-100** — doctype fields (§3.1–3.3), role patch, backfill patch, DocPerms. Nothing else compiles
   against a missing `farmer_profile` column.
2. **BE-101** — registration and auth (§6.7), admin reset (§6.8).
3. **BE-102** — permission hooks (§5) + the farmer-isolation test. Land before any endpoint, so no
   endpoint is ever written against unscoped reads.
4. **BE-104** — catalog endpoint and `get_my_applications` (§6.1–6.2).
5. **BE-103** — workflow patch, `start_application`, `submit_application`, consent binding
   (§4.3, §6.3–6.5). **Blocked on D6.**
6. **BE-105** — saved products (§3.4, §4.4, §6.6) + the `CLAUDE.md` rule edit (§11).
7. **BE-106** — bank notification (§6.4) — folds into BE-103 if they land together.
8. **FE-201 → FE-202 → FE-203** — each after its backend counterpart.

Run `bench --site development.localhost migrate` after step 1 and again after step 5, and
`bench --site development.localhost run-tests --app oan_a2c` before each merge. The repo's `pre-push`
hook targets `testsite.localhost`, which does not exist in this bench — run against
`development.localhost`.

---

## 11. Documentation to update

- **`CLAUDE.md`** — the multi-tenancy rule reads "a new bank-owned doctype **must** be added to
  `BANK_SCOPED` or it leaks across tenants". Saved products are user-owned, so the rule does not apply,
  but as written it invites someone to apply it anyway and break the feature. Amend it to distinguish
  bank-owned (`BANK_SCOPED`) from user-owned (`if_owner` DocPerms plus owner scoping), and note that
  user-owned is now a real category in this codebase.
- **`docs/multi_tenancy.md`** — add the farmer branch: ownership scoping sits _beside_ bank scoping,
  and the two are mutually exclusive per role.
- **`fixtures/workflow.json`** — re-export after the workflow patch, or it reintroduces the old
  transitions on the next sync.

---

## Appendix A — adjacent bug

`a2c_marketplace/stats_cache.py:12` defines `_PENDING_STATUSES = {"Submitted", "Under Review"}`, but
`A2C Loan Application.status` only ever holds `Draft`, `Processing`, `Approved` or `Rejected`
(`a2c_loan_application.json:85`). `pending_applications` on the bank dashboard is therefore always
zero. Invisible at today's volume; conspicuous the moment farmers start submitting into `Processing`
themselves. Fix is one line — `{"Processing"}` — plus a `test_stats_cache.py` case.

## Appendix B — traps verified in the source

| Trap                                                                       | Evidence                                                                   |
| -------------------------------------------------------------------------- | -------------------------------------------------------------------------- |
| Two query hooks on one doctype are `AND`-ed, so a second hook yields `1=0` | `frappe/model/db_query.py:1157`                                            |
| `has_permission` controllers can only deny, never grant                    | `frappe/permissions.py:484`                                                |
| A desk-access role makes every holder a System User                        | `frappe/core/doctype/user/user.py:403-414`                                 |
| `farmer_profile` is assigned but never persisted                           | `api/v1/loan_applications.py:866`, absent from `a2c_loan_application.json` |
| `Draft → Processing` is Development-Agent-only                             | `fixtures/workflow.json:295`                                               |
| Consent is Fayda/OTP and lead-anchored                                     | `api/v1/consent/consent.py:27-49`                                          |
| Notification infrastructure already exists                                 | `api/v1/notifications.py:17`                                               |
| `lead_source` has no self-service option                                   | `a2c_lead.json:65-70`                                                      |
