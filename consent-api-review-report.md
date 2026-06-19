# REVIEW REPORT — `api/v1/consent/*` (2026-06-19)

Reviewed as the Frappe Backend Code Review Agent (`.gemini/review-agent`).

## Context-file availability

The agent's designated primary reference, `../api-flow-backend.md`, is **not present** in `.gemini/` (only `api-flow-frontend.md`, `gemini.md`, and a prior `review-report.md` exist). Domain-correctness findings below are derived from the doctype JSON (`a2c_consent_request.json`) and code, not from the authoritative backend contract. Treat `[DC]` findings as high-confidence-but-unconfirmed against the missing spec.

## Files in scope

- `api/v1/consent/consent.py` — 7 endpoints
- `api/v1/consent/openg2p_client.py` — external HTTP client
- `api/v1/consent/utils.py` — receipt + WebSub
- `api/v1/webhook_consent_data.py` — inbound receiver
- `api/utils.py` — shared envelope/decorators

---

## Executive Summary

The consent flow is well-structured at the envelope/validation layer (Pydantic schemas, a centralized error→envelope decorator, `allow_guest=False` on every endpoint including the webhook receiver), and the two background jobs are among the most correct code in the app. The defects cluster in two themes: **(1) the external-HTTP boundary to OpenG2P/Odoo is unhardened** — no timeouts on the RPC path, synchronous multi-call chains holding gunicorn workers, no idempotency, and no rate limiting on an OTP/SMS-triggering endpoint; and **(2) state-machine integrity is weak** — status values written outside the doctype's allowed Select options, a verified-status the response claims but never persists, and read-then-write transitions with no atomic guard. Shippable only after the Critical timeout gap and the Major external-resilience/idempotency findings are addressed.

---

## Critical Findings (🔴)

```
[CR-1] Outbound HTTP to OpenG2P has no timeout on the primary RPC path
Location: openg2p_client.py:88 (_authenticate), openg2p_client.py:127 (_call_rpc session.send)
Rule: 18.2 — Every outbound HTTP call must set an explicit connect+read timeout
Finding: _call_rpc() builds and sends every OpenG2P request via `session.send(prepared)`
  with no `timeout=` argument. _authenticate() calls `session.post(url, json=payload)`
  with no timeout. These two methods underlie ALL seven consent endpoints
  (search_farmer, request_otp, verify_otp, submit_consent, get_consent_reasons,
  get_consent_allowed_fields, get_partner_allowed_data_field_ids). Only the
  best-effort "prime" call (line 216, timeout=5) and deliver_websub_payload
  (utils.py:82,95) set timeouts.
Consequence: If OpenG2P/Odoo hangs (slow query, network black-hole, TCP accept but no
  response), the gunicorn worker handling the consent request blocks indefinitely. The
  RequestException handler at line 147 never fires because the socket never errors. With
  4 workers, four hung Fayda calls take the entire OAN API offline — not just the consent
  flow. This is the single highest-severity issue in the file.
```

---

## Major Findings (🟡)

```
[MJ-1] Synchronous multi-step external call chain inside whitelisted handlers
Location: consent.py:302,324,344 (submit_consent); consent.py:203,216 (request_otp)
Rule: 18.1 / 18.4 — sequential external calls that must all succeed should be enqueued
Finding: submit_consent makes at least three sequential blocking OpenG2P calls in one
  request: get_farmer_by_fayda_id → get_consent_allowed_fields → submit_consent (plus
  the WebSub enqueue). request_otp authenticates (portal login), primes, then requests
  the OTP — every call re-authenticates via OpenG2PConsentClient().__init__.
Consequence: Each consent submission holds one worker for the sum of 3+ external
  round-trips. Under concurrent loan officers this saturates the worker pool. There is
  also no compensating rollback: if submit_consent (line 344) fails after the consent
  details were already persisted and committed (line 340), the A2C Consent Request is left
  with requested_data_fields written but status flipped to "Failed".
```
```
[MJ-2] No rate limiting on request_otp — OTP/SMS abuse and cost vector
Location: consent.py:185 (request_otp)
Rule: 10.1 — write/guest endpoints must have per-user Redis rate limiting
Finding: request_otp triggers a real Fayda OTP (SMS) on every authenticated call. There
  is no per-user counter, no 429 path, no Retry-After. It also creates a new A2C Consent
  Request doc (line 207) per call.
Consequence: A single authenticated client can issue unlimited OTP requests, generating
  SMS cost and OTP traffic against Fayda, and littering the table with orphan "Pending OTP"
  records. submit_consent and verify_otp are likewise unthrottled.
```
```
[MJ-3] No idempotency on request_otp / submit_consent
Location: consent.py:207 (new_doc per request_otp), consent.py:344 (submit_consent)
Rule: 16.4 — consent submission endpoints without idempotency are a Major finding
Finding: Neither endpoint accepts or checks an idempotency key. request_otp inserts a
  fresh A2C Consent Request on every call; submit_consent forwards to OpenG2P every time.
Consequence: A UI double-submit or client retry creates duplicate consent records and
  duplicate consent submissions at OpenG2P for the same farmer — a real-world consent
  artifact created twice.
```
```
[MJ-4] submit_consent has no expected-status guard — re-submit / TOCTOU on status
Location: consent.py:294 (_get_consent_request_and_client called with check_verified=True,
  no expected_status), consent.py:355 / verify_otp consent.py:259
Rule: 19.1 — read-then-write status updates are a TOCTOU race
Finding: submit_consent only checks `otp_verified_at` is set; it does NOT assert the
  request is still "Pending OTP"/un-approved. An already-"Approved" (or "Failed") request
  that retains otp_verified_at can be submitted again. Status is then written via
  frappe.db.set_value with no atomic WHERE-on-current-status guard. Two concurrent
  submit_consent calls for the same request both pass the verified check.
Consequence: Duplicate OpenG2P submission and double status transition for one consent
  request. Compounds MJ-3.
```
```
[MJ-5] [DC] Status field written with values outside its Select options; verify_otp
       reports a status it never persists
Location: consent.py:392 + webhook_consent_data.py:101 (status="Failed");
  consent.py:259/267 (verify_otp sets otp_verified_at but returns status "OTP Verified")
Rule: Domain correctness vs a2c_consent_request.json (Select options = Draft / Pending OTP
  / Approved)
Finding: The doctype's `status` Select allows only {Draft, Pending OTP, Approved}. Code
  writes "Failed" via set_value (which bypasses Select validation) in two places — a value
  the doctype does not define. Separately, verify_otp persists only otp_verified_at; the
  doc status stays "Pending OTP", yet the API response returns `"status": "OTP Verified"`.
Consequence: Desk filters/reports keyed on the Select option set will never surface
  "Failed" requests as a known state; any downstream code doing `== "Failed"` against a
  doc loaded through the ORM is comparing against an out-of-contract value. The verify_otp
  response/state divergence will mislead the frontend and any reconciliation logic.
```
```
[MJ-6] PII and OTP material written to logs
Location: openg2p_client.py:120,126,130-131 (_call_rpc debug logs full request/response),
  openg2p_client.py:246 (verify_otp logs params incl. otp_code), consent.py:160/164
Rule: 14.2 — PII (names, phone, national IDs) and secrets must never appear in log output
Finding: _call_rpc logs prepared.headers (carrying X-Openerp-Session-Id), the full
  response body (farmer name, mobile, region), and verify_otp logs the OTP code. These are
  debug-level but still emitted to whatever handler is configured.
Consequence: Fayda IDs, farmer phone numbers, session tokens, and live OTP codes land in
  application logs — a compliance violation if debug logging is enabled in any environment
  that ships logs to an aggregator.
```
```
[MJ-7] search_farmer exposes farmer PII to any authenticated user with no authorization
Location: consent.py:147-166
Rule: 1.x / 11 — authorization, not just authentication, on PII-returning endpoints
Finding: search_farmer takes a raw fayda_id and returns name/mobile/phone for any farmer.
  allow_guest=False gates anonymous access, but there is no role check, no ownership/lead
  linkage, and no rate limit (see MJ-2).
Consequence: Any authenticated account can enumerate the OpenG2P population by national ID
  and harvest names + phone numbers. Authentication is present; authorization is absent.
```
```
[MJ-8] Undocumented ignore_permissions=True on writes
Location: consent.py:120 (_save_farmer_data_to_lead lead_doc.save(ignore_permissions=True)),
  webhook_consent_data.py:85/87/90 (farmer_profile save/insert, lead_doc.db_set)
Rule: 1.5 / 17.3 — ignore_permissions / db_set bypass needs a comment naming the invariant
  and approver
Finding: Lead and Farmer Profile are written with permissions bypassed and no justifying
  comment. Note the inconsistency: submit_consent's own lead.save (line 384) correctly uses
  ignore_permissions=False, while the helper it calls uses True.
Consequence: A request that fails the row-level permission check on the lead can still have
  farmer PII written onto it through the bypassed path.
```
```
[MJ-9] Best-effort "prime" swallows all failures silently
Location: openg2p_client.py:215-218 (try/except Exception: pass, no logging)
Rule: 8.1 — silent except with no logging is an observability failure
Finding: The Odoo-17 session-prime POST is wrapped in `except Exception: pass`. A genuine
  connectivity failure here is indistinguishable from the expected dummy-transaction error.
Consequence: When OpenG2P is unreachable, the failure is masked at the prime step and only
  surfaces later as a less specific error, slowing incident diagnosis. (rules.md labels bare
  except-pass Critical; consequence here is degraded observability rather than an incident,
  hence Major.)
```
```
[MJ-10] No size bound on consent_form_base64
Location: consent.py:310-313 (base64.b64decode of unbounded input)
Rule: 3 — input bounds; uploads must be capped
Finding: consent_form_base64 is validated only for min_length=1. There is no max size; the
  full payload is base64-decoded into memory and re-encoded.
Consequence: A large payload is decoded and held in worker memory with no ceiling — a
  memory-pressure/DoS vector on the submit_consent worker.
```
```
[MJ-11] Status transitions go through frappe.db.set_value (hook bypass), aggregated
Location: consent.py:219, 259, 355-358, 364, 392; utils.py:101-102;
  webhook_consent_data.py:33,101
Rule: 17.1 — set_value bypasses validate/before_save/on_update on a status field
Finding: Every status/transaction-field mutation uses set_value, bypassing the controller
  lifecycle. The controller (a2c_consent_request.py) is currently empty (`pass`), so impact
  is latent — but the moment any transition guard or validate hook is added, these paths
  silently skip it.
Consequence: No server-side state machine is enforceable on A2C Consent Request as written;
  any → any transition is possible through these calls.
```

---

## Minor Findings (🔵)

```
[MN-1] get_partner_allowed_data_field_ids swallows exceptions and returns []
Location: openg2p_client.py:191-193
Finding: A failed fields lookup returns an empty list rather than surfacing the error;
  callers cannot distinguish "no fields" from "OpenG2P down."
```
```
[MN-2] frappe.get_all bypasses permissions in the webhook resolver
Location: webhook_consent_data.py:124
Finding: get_all (ignore_permissions by default, Rule 1.6) is used to resolve the consent
  request; acceptable because enforce_permission re-checks at line 143, but the intent
  should be documented since it precedes the permission check.
```
```
[MN-3] [DC] Consent receipt always records partner=None
Location: utils.py:20 (consent.partner) vs consent.py — `partner` is never populated on
  the A2C Consent Request anywhere in the create/submit flow
Finding: generate_consent_receipt signs partner into the HMAC payload, but no code path
  sets the partner field, so every signed receipt carries partner=null.
```
```
[MN-4] verify_otp response shape diverges from persisted state (see MJ-5) and trailing
       whitespace
Location: consent.py:252 (`farmer_db_id = cr_doc.farmer ` trailing space), 267
Finding: Cosmetic, but the "OTP Verified" literal here is the source of the contract
  mismatch in MJ-5.
```
```
[MN-5] Inline imports inside hot paths
Location: consent.py:82, 223, 307-308 (json, requests, base64, save_file imported per-call)
Finding: Repeated function-body imports; move to module scope for clarity and a marginal
  per-request cost.
```

---

## Positive Highlights

```
[PH-1] Centralized response envelope + exhaustive exception→code mapping
Location: api/utils.py:192-284 (handle_api_errors)
Why: Every endpoint returns the Rule-5 envelope; Permission/Authentication/DoesNotExist/
  Validation map to correct HTTP codes; the catch-all logs structured context and returns a
  generic message without leaking tracebacks (Rule 8.2). This is the correct pattern.
```
```
[PH-2] Background jobs are correctly contextualized
Location: utils.py:51-107 (deliver_websub_payload), webhook_consent_data.py:16-105
  (process_consent_data)
Why: Both call frappe.set_user before DB work, frappe.db.commit on success, frappe.db.rollback
  + frappe.log_error on failure, and transition status to "Failed" — satisfying Rules 9.3/9.4
  and 8.1. The WebSub external calls carry explicit timeouts (Rule 18.2).
```
```
[PH-3] Webhook receiver is authenticated and permission-checked
Location: webhook_consent_data.py:158-177
Why: receive_consent_data uses allow_guest=False and enforces write permission via
  validate_and_enqueue_consent(enforce_permission=True), returning 202 for async accept.
  This closes the gap the OAN checklist flags for legacy receive_consent_data.
```
```
[PH-4] Pydantic-first validation with shared decorators
Location: consent.py:13-43, api/utils.py:12-53
Why: Type-casting and required-field enforcement happen before the handler body, satisfying
  Rule 3 input-validation intent in a reusable, testable way.
```

---

## Summary Table

| ID | Severity | Title | Location | Rule |
|----|----------|-------|----------|------|
| CR-1 | 🔴 Critical | No timeout on OpenG2P RPC path | openg2p_client.py:88,127 | 18.2 |
| MJ-1 | 🟡 Major | Sync multi-call external chain in handlers | consent.py:302,324,344 | 18.1/18.4 |
| MJ-2 | 🟡 Major | No rate limit on OTP-triggering endpoint | consent.py:185 | 10.1 |
| MJ-3 | 🟡 Major | No idempotency on otp/submit | consent.py:207,344 | 16.4 |
| MJ-4 | 🟡 Major | submit_consent re-submit / status TOCTOU | consent.py:294,355 | 19.1 |
| MJ-5 | 🟡 Major | [DC] Out-of-contract status values | consent.py:392,267; webhook:101 | DC |
| MJ-6 | 🟡 Major | PII + OTP logged | openg2p_client.py:126,130,246 | 14.2 |
| MJ-7 | 🟡 Major | search_farmer PII, no authorization | consent.py:147 | 1/11 |
| MJ-8 | 🟡 Major | Undocumented ignore_permissions writes | consent.py:120; webhook:85-90 | 1.5/17.3 |
| MJ-9 | 🟡 Major | prime except: pass swallows failures | openg2p_client.py:215 | 8.1 |
| MJ-10 | 🟡 Major | Unbounded base64 upload | consent.py:310 | 3 |
| MJ-11 | 🟡 Major | Status transitions bypass hooks | consent.py:219,355,…; utils.py:101 | 17.1 |
| MN-1 | 🔵 Minor | Silent [] on fields lookup failure | openg2p_client.py:191 | — |
| MN-2 | 🔵 Minor | get_all before permission check | webhook:124 | 1.6 |
| MN-3 | 🔵 Minor | [DC] Receipt partner always null | utils.py:20 | — |
| MN-4 | 🔵 Minor | verify_otp state/response divergence | consent.py:267 | — |
| MN-5 | 🔵 Minor | Per-call inline imports | consent.py:82,223,307 | — |

**Total: 1 Critical / 11 Major / 5 Minor**

---

## Shippability Verdict

**BLOCKED** — CR-1 (no timeout on the OpenG2P RPC path) can take the entire API offline on a single upstream hang and must be resolved and re-reviewed before merge. Once CR-1 is fixed, the verdict moves to **CONDITIONAL**: the Major cluster — external-call resilience (MJ-1), idempotency/rate-limiting on the OTP and submit endpoints (MJ-2, MJ-3, MJ-4), the out-of-contract status values (MJ-5), and PII/OTP logging (MJ-6) — must be cleared before the next production deploy.

Process note: this review was performed without the authoritative `api-flow-backend.md`. The `[DC]` findings (MJ-5, MN-3) should be confirmed against that contract once it is restored, since it is the agent's designated ground truth for response shapes and state transitions.
