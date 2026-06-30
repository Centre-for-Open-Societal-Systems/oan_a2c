# api-flow-backend.md — OAN A2C Backend API Contract
_Derived from direct source code analysis — `apps/oan_a2c/oan_a2c/api/` — 2026-06-14_
_Last updated: 2026-06-14 (full reread of all source files)_

> **Source of truth:** This document reflects what the backend **actually implements**, derived from reading every Python file. Cross-reference `api-flow-frontend.md` to identify discrepancies. When the two conflict, this file wins.

---

## 1. Authentication Architecture

### 1.1 JWT Scheme

All endpoints under `/api/method/oan_a2c.*` require a Bearer JWT token unless explicitly listed in the exempt paths below.

**Token spec:**
- Algorithm: HS256
- Secret: `frappe.conf.encryption_key`
- Access Token Payload: `{ sub: email, iss: "oan_a2c_identity_gateway", iat, exp (now + 15 min), roles: [] }`
- Header: `Authorization: Bearer <token>`

**Refresh Token spec:**
- Database-backed (`A2C User Refresh Token` DocType) using SHA-256 hash.
- Expiration: 30 days if "Remember Me" is enabled, 1 day if disabled.
- Rotation (RTR): The refresh token is rotated (invalidated and re-issued) upon every usage.

**Middleware** (`api/middleware.py`, registered as `auth_hooks` in `hooks.py`):
- Validates token cryptographically
- Calls `frappe.set_user(payload.sub)` to wire Frappe RBAC for the request
- Preserves `frappe.local.form_dict` across the `set_user()` call

**JWT-exempt paths (no Bearer token required):**
```
/api/method/oan_a2c.api.auth.login
/api/method/oan_a2c.api.auth.forgot_password
/api/method/oan_a2c.api.auth.reset_password
/api/method/oan_a2c.api.auth.refresh
/api/method/oan_a2c.api.auth.logout
/api/method/oan_a2c.api.v1.webhook_consent_data.receive_consent_data
/api/method/oan_a2c.api.v1.webhooks.lead_inbound
```

**`lead_inbound` auth note:** Exempt from JWT middleware but protected by `frappe.has_permission("A2C Lead", "create", throw=True)` — requires a valid Frappe session or API key/secret pair (`Authorization: token apikey:apisecret`).

---

### 1.2 Middleware Error Responses

Middleware errors are thrown before `handle_api_errors` runs — they are NOT in the standard envelope. Frappe serializes them as:

```json
{ "exc_type": "AuthenticationError", "exception": "...", "_server_messages": "..." }
```

| Condition | HTTP | Message |
|-----------|------|---------|
| Missing `Authorization` header | 401 | `"Missing Authorization Header"` |
| Token expired | 401 | `"Token has expired"` |
| Token signature invalid | 401 | `"Invalid token"` |
| `encryption_key` missing in site config | 401 | `"System encryption key missing"` |

---

## 2. Response Envelope

**All endpoints** (auth, leads, loans, webhooks, consent) now use `@handle_api_errors` from `api/utils.py`. Every response goes through one of two shapes:

### 2.1 Success Envelope
```json
{
  "status": "success",
  "message": "Human-readable string",
  "data": null | {} | [],
  "meta": null | {},
  "pagination": null | { "page": 1, "limit": 20, "total": 100, "total_pages": 5, "has_next": true }
}
```
`pagination` key is **omitted entirely** when the endpoint does not paginate.

### 2.2 Error Envelope
```json
{
  "status": "error",
  "message": "Human-readable description",
  "code": "MACHINE_READABLE_CODE",
  "details": {}
}
```

### 2.3 Error Code Reference

| HTTP | `code` | Frappe exception | When it fires |
|------|--------|-----------------|---------------|
| 401 | `AUTHENTICATION_ERROR` | `frappe.AuthenticationError` | Bad credentials, expired token (via `frappe.throw`) |
| 403 | `PERMISSION_DENIED` | `frappe.PermissionError` | Missing role or document permission |
| 404 | `NOT_FOUND` | `frappe.DoesNotExistError` | Document not found |
| 400 | `VALIDATION_ERROR` | `frappe.ValidationError` | Invalid input, business rule violation |
| 500 | `INTERNAL_ERROR` | Any other `Exception` | Unexpected server error (logged to Error Log) |

> **Critical frontend note — `validate_lead()` quirk:** Endpoints that call `validate_lead(lead_id)` internally (`get_basic_profile`, `update_basic_profile`, `create_loan_application`) return a **malformed** error response when `lead_id` is missing or the lead doesn't exist. The HTTP status code is set correctly (400/404) but the envelope **incorrectly says `"status": "success"`** with `data: { "error": { "code": "...", "message": "..." } }`. Frontend must check HTTP status code first, not only the `status` field, for these endpoints.

```json
// Malformed error from validate_lead() — HTTP 404, but envelope says success
{
  "status": "success",
  "message": "Success",
  "data": {
    "error": {
      "code": "LEAD_NOT_FOUND",
      "message": "A2C Lead LEAD-001 not found"
    }
  }
}
```

**Affected endpoints:** `get_basic_profile`, `update_basic_profile`, `create_loan_application`.

---

## 3. DocType Reference

| DocType | Key Fields |
|---------|-----------|
| A2C Lead | `phone_number`, `first_name`, `last_name`, `email`, `lead_source`, `status`, `assigned_to`, `assigned_date`, `external_id`, `call_notes`, `farmer_profile` |
| A2C Lead Audit Event | `lead`, `event_type`, `event_title`, `event_description`, `creation`, `owner` |
| A2C Farmer Profile | `first_name`, `last_name`, `phone_number`, `email`, `location`, `farmer_id`, `lead_id`, `consent_id`, `date_of_birth`, `gender`, `marital_status`, `family_size`, `education_level`, land fields, `soil_fertility_minerals`, `moisture_levels`, `certification_id`, `certification_photo_url` |
| A2C Credit Information | `lead`, `loan_type`, `loan_amount`, `purpose_message` |
| A2C Loan Application | `lead_id`, `status`, `current_step`, `farmer_profile`, `phone_number`, `location`, `farmer_id`, `consent_id`, `loan_type`, `loan_amount`, `loan_reason`, `loan_officer` + 20+ demographic/agricultural fields copied from Farmer Profile |
| A2C Consent Request | `lead`, `farmer_fayda_id`, `partner`, `status`, `purpose`, `validity_from`, `validity_to`, `openg2p_consent_id`, `otp_transaction_id`, `otp_verified_at`, `consent_form_attachment`, `consent_receipt`, `websub_delivered`, `websub_delivered_at` |
| A2C Consent Data | `field_name`, `field_value` (child table of Consent Request) |
| A2C Visit Schedule | `lead`, `visit_date`, `visit_time`, `region`, `zone`, `woreda`, `kebele`, `meeting_location`, `notes`, `scheduled_by`, `status` |

### Status Machines

**A2C Lead `status`:**
- Values: `Active`, `Verified`, `Processed`, `Granted`, `Rejected`, `Dormant`
- **Terminal (immutable):** `Processed`, `Rejected`, `Granted`, `Dormant`
- Attempting `update_lead_status` on a terminal status → 400 `VALIDATION_ERROR`

**A2C Loan Application `status`:**
- Values: `Draft`, `Processing`, `Approved`, `Rejected`
- **Terminal (immutable):** `Approved`, `Rejected`
- Attempting `update_loan_status` on a terminal status → 400 `VALIDATION_ERROR`

**A2C Loan Application `current_step`:**
- Valid values: `1`, `2`, `3`, `4`
- Steps cannot be skipped: step N+2 is rejected if current step is N
- Back-navigation (lower step numbers) is allowed

**A2C Visit Schedule `status`:**
- Values: `Scheduled`, `Completed`, `Cancelled`, `Missed`
- **Terminal (immutable):** `Completed`, `Missed`
- Attempting any status change on a Completed or Missed schedule → 400 `VALIDATION_ERROR`

**A2C Consent Request `status`:**
- Values: `Pending OTP`, `Approved`, `Failed` (set by background job on error)

---

## 4. Endpoint Reference

Convention for parameter tables:
- **bold** = Required. Missing value raises 400 `VALIDATION_ERROR` or `MandatoryError`.
- plain = Optional.
- Invalid enum values are either silently coerced to a default (noted) or raise 400.

---

### 4.1 Auth (`api/auth.py`)

All auth endpoints use the standard envelope.

---

#### `POST /api/method/oan_a2c.api.auth.login`
No JWT required.

**Parameters:**

| Param | Type | Required | Notes |
|-------|------|----------|-------|
| **`usr`** | string | Yes | User email address |
| **`pwd`** | string | Yes | User password |
| `remember_me` | boolean | No | Extends refresh token expiry to 30 days if true (default is false, which expires in 1 day) |

**Success response** (HTTP 200):
```json
{
  "status": "success",
  "message": "Success",
  "data": {
    "token": "eyJ...",
    "refresh_token": "a1b2c3d4...",
    "user": {
      "email": "user@domain.com",
      "full_name": "Full Name",
      "roles": ["Development Agent"],
      "bank": "Bank Name or null"
    }
  }
}
```

> **Note:** Key is `bank`, not `linked_bank`. Only populated for users with "Bank Agent" role.

**Error cases:**

| Condition | HTTP | code | message |
|-----------|------|------|---------|
| Wrong credentials | 401 | `AUTHENTICATION_ERROR` | `"Incorrect email or password."` |
| `encryption_key` missing in site config | 500 | `INTERNAL_ERROR` | `"An unexpected error occurred"` |

---

#### `POST /api/method/oan_a2c.api.auth.refresh`
No JWT required.

**Parameters:**

| Param | Type | Required | Notes |
|-------|------|----------|-------|
| **`refresh_token`** | string | Yes | Raw refresh token string returned from login/refresh |

**Success response** (HTTP 200):
```json
{
  "status": "success",
  "message": "Success",
  "data": {
    "token": "eyJ...",
    "refresh_token": "new_rotated_refresh_token_string"
  }
}
```

**Error cases:**

| Condition | HTTP | code | message |
|-----------|------|------|---------|
| Token invalid/not found | 401 | `AUTHENTICATION_ERROR` | `"Invalid or expired refresh token."` |
| Token expired | 401 | `AUTHENTICATION_ERROR` | `"Refresh token has expired."` |
| User is disabled | 401 | `AUTHENTICATION_ERROR` | `"User is disabled or does not exist."` |

---

#### `POST /api/method/oan_a2c.api.auth.logout`
No JWT required.

**Parameters:**

| Param | Type | Required | Notes |
|-------|------|----------|-------|
| **`refresh_token`** | string | Yes | Raw refresh token string to revoke |

**Success response** (HTTP 200):
```json
{
  "status": "success",
  "message": "Logged out successfully.",
  "data": null
}
```

**Error cases:** None (always returns success even if token was already deleted or doesn't exist).

---

#### `POST /api/method/oan_a2c.api.auth.forgot_password`
No JWT required.

**Parameters:**

| Param | Type | Required | Notes |
|-------|------|----------|-------|
| **`email`** | string | Yes | |

**Success response** (HTTP 200): Always succeeds — no email enumeration.
```json
{
  "status": "success",
  "message": "Password reset instructions have been sent to your registered email.",
  "data": null
}
```

**Error cases:** None exposed to caller (errors swallowed intentionally).

---

#### `POST /api/method/oan_a2c.api.auth.reset_password`
No JWT required.

**Parameters:**

| Param | Type | Required | Notes |
|-------|------|----------|-------|
| **`email`** | string | Yes | |
| **`key`** | string | Yes | Reset token from email link |
| **`new_password`** | string | Yes | |

**Success response** (HTTP 200):
```json
{
  "status": "success",
  "message": "Your password has been successfully updated. You may now login.",
  "data": null
}
```

**Error cases:**

| Condition | HTTP | code | message |
|-----------|------|------|---------|
| Token not found / email mismatch | 401 | `AUTHENTICATION_ERROR` | `"Invalid or expired reset token."` |

**Side effect:** Logs out all existing sessions for the user.

---

### 4.2 Lead Management (`api/v1/leads.py`)

All endpoints use the standard envelope.

---

#### `GET /api/method/oan_a2c.api.v1.leads.get_leads`

**Parameters:**

| Param | Type | Required | Default | Constraint |
|-------|------|----------|---------|-----------|
| `start` | int | No | 0 | Offset (clamped to ≥ 0) |
| `page_length` | int | No | 20 | Clamped to [1, 100] |
| `search_query` | string | No | — | `LIKE` match on `name`, `phone_number`, `external_id` |
| `status` | string | No | — | Single value, **comma-separated** list, or **stringified JSON array**. Each value validated against allowlist (`in` filter). Invalid values silently dropped |
| `lead_source` | string | No | — | Single value, **comma-separated** list, or **stringified JSON array**. Each value validated against allowlist (`in` filter). Invalid values silently dropped |
| `loan_type` | string | No | — | Single value, **comma-separated** list, or **stringified JSON array**. Validated against `A2C Credit Information.loan_type` options. Filters via subquery on A2C Credit Information |
| `assigned_to` | string | No | — | Filter by assigned agent (User). Single value or **comma-separated** list of users (`in` filter on `assigned_to`). The literal `unassigned` matches leads with no agent; it can be combined with named users (e.g. `unassigned,agent@bank.com`). Not allowlist-validated — an unknown user simply yields no matches |
| `start_date` | string | No | — | ISO date. Used alone or with `end_date` |
| `end_date` | string | No | — | ISO date. Used alone or with `start_date` |
| `min_loan_amount` | float | No | — | Filters via subquery on A2C Credit Information |
| `max_loan_amount` | float | No | — | Filters via subquery on A2C Credit Information |

**Status allowlist:** `Active`, `Verified`, `Processed`, `Granted`, `Rejected`, `Dormant`
**Lead source allowlist:** `Missed Call`, `IVR`, `SMS`, `Agent Entry`
**Loan type allowlist:** dynamic — pulled from `A2C Credit Information.loan_type` Select options at request time.

> **Multi-value filters:** `status`, `lead_source`, and `loan_type` accept either a single value, a comma-separated list, or a stringified JSON array (e.g. `status=["Active","Verified"]`). Using a JSON array is the strongly recommended format to avoid delimiter conflicts when values contain commas. Values are split, de-duplicated, and matched against the allowlist; valid values are combined with an `in` filter.

> **Important:** Invalid `status`, `lead_source`, or `loan_type` values are **silently dropped** — the filter is not applied (or, for a multi-value list, only the invalid entries are removed) rather than returning an error. No 400 is thrown. If a list contains *only* invalid values, that filter is skipped entirely.

> **Credit criteria intersection:** `loan_type`, `min_loan_amount`, and `max_loan_amount` share a single subquery against A2C Credit Information — a lead must satisfy all supplied credit criteria together to match.

> **Assignee filter:** `assigned_to` accepts a single user, a comma-separated list of users, or the literal `unassigned` (leads with empty `assigned_to`). Unlike the allowlist filters, unknown users are **not** dropped or errored — they just match nothing. Combine with other filters for an agent's scoped queue, e.g. `assigned_to=agent@bank.com&status=Active`.

**Success response** (HTTP 200):
```json
{
  "status": "success",
  "message": "Leads retrieved successfully",
  "data": [
    {
      "name": "LEAD-2026-0001",
      "phone_number": "+251911000000",
      "first_name": "Abebe or null",
      "last_name": "Kebede or null",
      "external_id": "TELCO-REF-001 or null",
      "lead_source": "Missed Call",
      "status": "Active",
      "assigned_to": "agent@bank.com or null",
      "assigned_date": "2026-01-15 or null",
      "creation": "2026-01-15 10:30:00",
      "loan_type": "Crop Loan or null",
      "loan_amount": 5000.0
    }
  ],
  "pagination": {
    "page": 1,
    "limit": 20,
    "total": 150,
    "total_pages": 8,
    "has_next": true
  }
}
```

> **`loan_type` / `loan_amount`:** Fetched from A2C Credit Information in a single batched IN-query (not N+1). Uses the **most recent** credit info record per lead.

> **`visit_date` / `schedule_status` are NOT in this response.** Frontend must fetch these separately via `get_visit_schedules` if needed.

**Error cases:**

| Condition | HTTP | code |
|-----------|------|------|
| No `A2C Lead` read permission | 403 | `PERMISSION_DENIED` |
| Unexpected DB error | 500 | `INTERNAL_ERROR` |

---

#### `POST /api/method/oan_a2c.api.v1.leads.create_lead`

**Parameters:**

| Param | Type | Required | Default | Notes |
|-------|------|----------|---------|-------|
| **`phone_number`** | string | Yes | — | |
| `first_name` | string | No | — | |
| `last_name` | string | No | — | |
| `email` | string | No | — | Validated format if provided |
| `lead_source` | string | No | `"Agent Entry"` | Coerced to `"Agent Entry"` if invalid |
| `external_id` | string | No | — | |

**Success response** (HTTP 200):
```json
{
  "status": "success",
  "message": "Lead created successfully.",
  "data": {
    "lead_id": "LEAD-2026-0001",
    "lead": {
      "name": "LEAD-2026-0001",
      "phone_number": "+251911000000",
      "first_name": "Abebe",
      "last_name": "Kebede",
      "email": null,
      "lead_source": "Agent Entry",
      "external_id": null,
      "status": "Active"
    }
  }
}
```

> **Correction from previous version:** `create_lead` now returns the full `lead` object. No need to immediately re-fetch via `get_leads?search_query=`.

**Error cases:**

| Condition | HTTP | code | message |
|-----------|------|------|---------|
| `phone_number` missing | 400 | `VALIDATION_ERROR` | `"phone_number is required"` |
| `email` invalid format | 400 | `VALIDATION_ERROR` | `"Invalid email address format"` |
| No `A2C Lead` create permission | 403 | `PERMISSION_DENIED` | |

**Side effect:** Creates an `A2C Lead Audit Event` of type `"Created"`.

---

#### `GET /api/method/oan_a2c.api.v1.leads.get_lead_summary`

No parameters.

**Success response** (HTTP 200):
```json
{
  "status": "success",
  "message": "Lead summary retrieved successfully",
  "data": {
    "total": 342,
    "by_status": {
      "Active": 120,
      "Verified": 80,
      "Processed": 50,
      "Granted": 40,
      "Rejected": 30,
      "Dormant": 22
    },
    "tab_counts": {
      "all": 342,
      "assigned": 300,
      "unassigned": 42
    }
  }
}
```

> **`tab_counts`:** `all` = total. `assigned` = leads where `assigned_to` is set. `unassigned` = `all − assigned` (leads with no agent). All respect RBAC.

> **Performance note:** Executes 7 count queries (one per status, plus one for assigned). These are not cached.

**Error cases:**

| Condition | HTTP | code |
|-----------|------|------|
| No `A2C Lead` read permission | 403 | `PERMISSION_DENIED` |

---

#### `GET /api/method/oan_a2c.api.v1.leads.get_lead_metadata`

No parameters. Reads DocType meta only — no DB rows queried.

**Success response** (HTTP 200):
```json
{
  "status": "success",
  "message": "Lead metadata retrieved successfully",
  "data": {
    "statuses": ["Active", "Verified", "Processed", "Granted", "Rejected", "Dormant"],
    "sources": ["Missed Call", "IVR", "SMS", "Agent Entry"],
    "loan_types": ["Crop Loan", "Livestock Loan", "..."]
  }
}
```

> **Not cached.** Fetched fresh on every call.

**Error cases:**

| Condition | HTTP | code |
|-----------|------|------|
| No `A2C Lead` read permission | 403 | `PERMISSION_DENIED` |

---

#### `POST /api/method/oan_a2c.api.v1.leads.add_lead_credit_info`

**Parameters:**

| Param | Type | Required | Notes |
|-------|------|----------|-------|
| **`lead_id`** | string | Yes | Must exist |
| **`loan_type`** | string | Yes | Validated against `A2C Credit Information.loan_type` options |
| **`loan_amount`** | number | Yes | |
| **`purpose_message`** | string | Yes | |

**Success response** (HTTP 200):
```json
{
  "status": "success",
  "message": "Credit information added successfully.",
  "data": { "credit_info_id": "CRINFO-2026-0001" }
}
```

**Error cases:**

| Condition | HTTP | code | message |
|-----------|------|------|---------|
| Any required param missing | 400 | `VALIDATION_ERROR` | `"X is required"` |
| `lead_id` not found | 404 | `NOT_FOUND` | `"A2C Lead {lead_id} not found"` |
| Invalid `loan_type` | 400 | `VALIDATION_ERROR` | `"Invalid loan type: {loan_type}"` |
| No write permission on lead | 403 | `PERMISSION_DENIED` | |
| No create permission on Credit Info | 403 | `PERMISSION_DENIED` | |

**Side effect:** Creates `A2C Lead Audit Event` of type `"Credit Info Added"`.

---

#### `GET /api/method/oan_a2c.api.v1.leads.get_lead_credit_infos`

**Parameters:**

| Param | Type | Required | Notes |
|-------|------|----------|-------|
| **`lead_id`** | string | Yes | |

**Success response** (HTTP 200):
```json
{
  "status": "success",
  "message": "Lead credit information retrieved successfully",
  "data": [
    {
      "name": "CRINFO-2026-0001",
      "loan_type": "Crop Loan",
      "loan_amount": 5000.0,
      "purpose_message": "Purchase seeds",
      "created_by": "agent@bank.com",
      "creation": "2026-01-15 10:30:00"
    }
  ]
}
```

**Error cases:**

| Condition | HTTP | code |
|-----------|------|------|
| `lead_id` missing | 400 | `VALIDATION_ERROR` |
| No read permission on lead | 403 | `PERMISSION_DENIED` |
| No read permission on Credit Info | 403 | `PERMISSION_DENIED` |

---

#### `POST /api/method/oan_a2c.api.v1.leads.update_lead_status`

**Parameters:**

| Param | Type | Required | Notes |
|-------|------|----------|-------|
| **`lead_id`** | string | Yes | |
| **`status`** | string | Yes | Must be in allowlist |
| `reason` | string | No | Appended to audit event description |

**Status allowlist:** `Active`, `Verified`, `Processed`, `Granted`, `Rejected`, `Dormant`

**Success response** (HTTP 200):
```json
{
  "status": "success",
  "message": "Lead status updated successfully.",
  "data": {
    "lead_id": "LEAD-2026-0001",
    "new_status": "Verified"
  }
}
```

**Error cases:**

| Condition | HTTP | code | message |
|-----------|------|------|---------|
| `lead_id` or `status` missing | 400 | `VALIDATION_ERROR` | `"X is required"` |
| `lead_id` not found | 404 | `NOT_FOUND` | `"A2C Lead {lead_id} not found"` |
| Current status is terminal | 400 | `VALIDATION_ERROR` | `"Lead status is locked and cannot be updated because its current state is '{status}'."` |
| `status` not in allowlist | 400 | `VALIDATION_ERROR` | `"Invalid status: {status}"` |
| No write permission on lead | 403 | `PERMISSION_DENIED` | |

**Side effect:** Creates `A2C Lead Audit Event` of type `"Status Changed"` with old → new status and reason.

---

#### `GET /api/method/oan_a2c.api.v1.leads.get_assignable_users`

**Parameters:**

| Param | Type | Required | Default | Constraint |
|-------|------|----------|---------|-----------|
| `search_query` | string | No | — | `LIKE` match on `full_name`, `email`, `name` |
| `start` | int | No | 0 | Offset |
| `page_length` | int | No | 20 | Clamped to [1, 100] |

> **Warning:** Empty `search_query` returns all users with agent roles (no minimum length enforced). Can be expensive if many agents exist.

**Pagination shape differs from other list endpoints** — uses `start`/`page_length` offset style, not page numbers:

**Success response** (HTTP 200):
```json
{
  "status": "success",
  "message": "Assignable users retrieved successfully",
  "data": [
    {
      "email": "agent@bank.com",
      "full_name": "Abebe Kebede",
      "agent_id": "AG-2024-0042",
      "region": "Oromia"
    }
  ],
  "pagination": {
    "start": 0,
    "page_length": 20,
    "total_count": 45,
    "has_next": true
  }
}
```

> **`agent_id`:** Uses `user.username` if set, otherwise `"AG-2024-{abs(hash(name)) % 10000:04d}"` — a deterministic but non-sequential mock ID.
> **`region`:** Uses `user.location` if set, otherwise defaults to `"Oromia"`.

**Error cases:**

| Condition | HTTP | code |
|-----------|------|------|
| No read permission on A2C Lead | 403 | `PERMISSION_DENIED` |

---

#### `POST /api/method/oan_a2c.api.v1.leads.assign_lead`

**Parameters:**

| Param | Type | Required | Notes |
|-------|------|----------|-------|
| **`lead_id`** | string | Yes | |
| **`assigned_to`** | string | Yes | User email or name. Must be enabled. |

**Success response** (HTTP 200):
```json
{
  "status": "success",
  "message": "Lead assigned successfully.",
  "data": {
    "lead_id": "LEAD-2026-0001",
    "assigned_to": "agent@bank.com",
    "assigned_date": "2026-01-15"
  }
}
```

**Error cases:**

| Condition | HTTP | code | message |
|-----------|------|------|---------|
| `lead_id` or `assigned_to` missing | 400 | `VALIDATION_ERROR` | `"X is required"` |
| `lead_id` not found | 404 | `NOT_FOUND` | `"A2C Lead {lead_id} not found"` |
| `assigned_to` user not found or disabled | 404 | `NOT_FOUND` | `"User '{assigned_to}' is not a valid active agent"` |
| No write permission on lead | 403 | `PERMISSION_DENIED` | |

**Side effect:** Sets `assigned_date` to today's date. Creates `A2C Lead Audit Event` of type `"Assigned"`.

---

#### `POST /api/method/oan_a2c.api.v1.leads.add_lead_comment`

**Parameters:**

| Param | Type | Required | Notes |
|-------|------|----------|-------|
| **`lead_id`** | string | Yes | |
| **`content`** | string | Yes | |

**Success response** (HTTP 200):
```json
{
  "status": "success",
  "message": "Comment added successfully.",
  "data": { "comment_id": "AUDITEV-2026-0042" }
}
```

**Error cases:**

| Condition | HTTP | code |
|-----------|------|------|
| `lead_id` or `content` missing | 400 | `VALIDATION_ERROR` |
| No write permission on lead | 403 | `PERMISSION_DENIED` |

---

#### `GET /api/method/oan_a2c.api.v1.leads.get_lead_timeline`

**Parameters:**

| Param | Type | Required | Notes |
|-------|------|----------|-------|
| **`lead_id`** | string | Yes | |
| `event_type` | string | No | Filters by exact `event_type` match. Values: `Created`, `Status Changed`, `Credit Info Added`, `Assigned`, `Commented`, `Visit Scheduled` |

**Success response** (HTTP 200):
```json
{
  "status": "success",
  "message": "Lead timeline retrieved successfully",
  "data": {
    "lead_id": "LEAD-2026-0001",
    "timeline": [
      {
        "name": "AUDITEV-2026-0042",
        "event_type": "Status Changed",
        "event_title": "Status Updated",
        "event_description": "Changed to Verified\nUpdated by: agent@bank.com",
        "creation": "2026-01-16 09:00:00",
        "owner": "agent@bank.com"
      }
    ]
  }
}
```

> **Response shape:** `data` is an object with `lead_id` and `timeline` keys — not a flat array.

**Error cases:**

| Condition | HTTP | code |
|-----------|------|------|
| `lead_id` missing | 400 | `VALIDATION_ERROR` |
| No read permission on lead | 403 | `PERMISSION_DENIED` |

---

#### `GET /api/method/oan_a2c.api.v1.leads.get_lead_call_logs`

**Parameters:**

| Param | Type | Required | Notes |
|-------|------|----------|-------|
| **`lead_id`** | string | Yes | |

**Success response** (HTTP 200):
```json
{
  "status": "success",
  "message": "Lead call logs retrieved successfully",
  "data": {
    "lead_id": "LEAD-2026-0001",
    "call_logs": [
      {
        "source": "Missed Call",
        "ref_id": "TELCO-778899",
        "timestamp": "2026-05-27T12:00:00Z"
      }
    ]
  }
}
```

> **Response shape:** `data` is an object with `lead_id` and `call_logs` keys — not a flat array.
> **Parsing:** Call notes are stored as pipe-delimited strings (`"Source: X | Ref ID: Y | Timestamp: Z"`). Keys in the parsed output are lowercased and space-replaced with `_`. An entry with no parseable parts is silently skipped.

**Error cases:**

| Condition | HTTP | code |
|-----------|------|------|
| `lead_id` missing | 400 | `VALIDATION_ERROR` |
| No read permission on lead | 403 | `PERMISSION_DENIED` |

---

#### `POST /api/method/oan_a2c.api.v1.leads.schedule_visit`

**Parameters:**

| Param | Type | Required | Notes |
|-------|------|----------|-------|
| **`lead_id`** | string | Yes | |
| **`visit_date`** | string | Yes | ISO date `YYYY-MM-DD` |
| **`visit_time`** | string | Yes | `HH:MM:SS` |
| **`region`** | string | Yes | |
| **`zone`** | string | Yes | |
| **`woreda`** | string | Yes | |
| **`kebele`** | string | Yes | |
| `meeting_location` | string | No | |
| `notes` | string | No | |

**Success response** (HTTP 200):
```json
{
  "status": "success",
  "message": "Visit scheduled successfully.",
  "data": { "schedule_id": "VSCHED-2026-0001" }
}
```

**Error cases:**

| Condition | HTTP | code | message |
|-----------|------|------|---------|
| Any required param missing | 400 | `VALIDATION_ERROR` | `"X is required"` |
| `lead_id` not found | 404 | `NOT_FOUND` | `"A2C Lead {lead_id} not found"` |
| No write permission on lead | 403 | `PERMISSION_DENIED` | |
| No create permission on Visit Schedule | 403 | `PERMISSION_DENIED` | |

**Side effect:** Creates `A2C Lead Audit Event` of type `"Visit Scheduled"`. Sets `scheduled_by` to `frappe.session.user`. Initial status is always `"Scheduled"`.

---

#### `GET /api/method/oan_a2c.api.v1.leads.get_visit_schedules`

**Parameters:**

| Param | Type | Required | Default | Constraint |
|-------|------|----------|---------|-----------|
| `lead_id` | string | No | — | Omitting returns ALL schedules across all leads |
| `start_date` | string | No | — | ISO date. Filters `visit_date` |
| `end_date` | string | No | — | ISO date. Filters `visit_date` |
| `status` | string | No | — | No validation — passed raw into filter |
| `start` | int | No | 0 | Offset |
| `page_length` | int | No | 20 | Clamped to [1, 100] |

> **Caution:** No `lead_id` + large `page_length` = potentially large dataset. Frontend calling without `lead_id` will receive paginated results max 100 rows at a time — not all records. This breaks client-side filtering across the full dataset beyond page 1.

**Success response** (HTTP 200):
```json
{
  "status": "success",
  "message": "Visit schedules retrieved successfully",
  "data": [
    {
      "name": "VSCHED-2026-0001",
      "lead": "LEAD-2026-0001",
      "visit_date": "2026-02-10",
      "visit_time": "09:00:00",
      "meeting_location": "Main Office",
      "region": "Oromia",
      "zone": "East Hararge",
      "woreda": "Harar",
      "kebele": "01",
      "status": "Scheduled",
      "scheduled_by": "agent@bank.com",
      "creation": "2026-01-20 11:00:00"
    }
  ],
  "pagination": {
    "page": 1,
    "limit": 20,
    "total": 45,
    "total_pages": 3,
    "has_next": true
  }
}
```

**Error cases:**

| Condition | HTTP | code |
|-----------|------|------|
| No read permission on Visit Schedule | 403 | `PERMISSION_DENIED` |
| `lead_id` provided but no read permission on that lead | 403 | `PERMISSION_DENIED` |

---

#### `POST /api/method/oan_a2c.api.v1.leads.update_visit_schedule_status`

**Parameters:**

| Param | Type | Required | Notes |
|-------|------|----------|-------|
| **`schedule_id`** | string | Yes | |
| **`status`** | string | Yes | Must be in allowlist |

**Status allowlist:** `Scheduled`, `Completed`, `Cancelled`, `Missed`

**Success response** (HTTP 200):
```json
{
  "status": "success",
  "message": "Visit schedule status updated successfully.",
  "data": {
    "schedule_id": "VSCHED-2026-0001",
    "new_status": "Completed"
  }
}
```

**Error cases:**

| Condition | HTTP | code | message |
|-----------|------|------|---------|
| `schedule_id` or `status` missing | 400 | `VALIDATION_ERROR` | `"schedule_id and status are required"` |
| `schedule_id` not found | 404 | `NOT_FOUND` | `"A2C Visit Schedule {id} not found"` |
| Current status is `Completed` or `Missed` | 400 | `VALIDATION_ERROR` | `"Cannot update status of a {status} visit."` |
| `status` not in allowlist | 400 | `VALIDATION_ERROR` | `"Invalid status: {status}"` |
| No write permission on the linked lead | 403 | `PERMISSION_DENIED` | |

---

### 4.3 Loan Applications (`api/v1/loan_applications.py`)

All endpoints use `@handle_api_errors` and standard envelope.

---

#### `GET /api/method/oan_a2c.api.v1.loan_applications.get_basic_profile`

**Parameters:**

| Param | Type | Required | Notes |
|-------|------|----------|-------|
| **`lead_id`** | string | Yes | See validate_lead quirk in Section 2.3 |
| `include_consent_data` | int/bool | No | Pass `1` or `true` to include consent fields |

**Success response — without `include_consent_data`** (HTTP 200):
```json
{
  "status": "success",
  "message": "Basic profile retrieved successfully",
  "data": {
    "farmer_profile_created": false,
    "first_name": "Abebe",
    "last_name": "Kebede",
    "phone_number": "+251911000000",
    "email": "abebe@email.com or null",
    "region": "Oromia",
    "woreda": "East Hararge",
    "kebele": "Gudina or null",
    "language": "en_US or null",
    "consent_request": {
      "name": "CR-2026-00001",
      "status": "Pending OTP",
      "otp_verified": false
    }
  }
}
```

**Success response — with `include_consent_data=1`** (HTTP 200)
```json
{
  "status": "success",
  "data": {
    "farmer_profile_created": true,
    "first_name": "Abebe",
    "last_name": "Kebede",
    "phone_number": "+251911000000",
    "email": null,
    "region": "Oromia",
    "woreda": "East Hararge",
    "kebele": "Gudina",
    "language": "en_US",
    "consent_request": {
      "name": "CR-2026-00001",
      "status": "Approved",
      "otp_verified": true
    },
    "websub_delivered_at": "2026-01-15 10:00:00 or null",
    "consent_type": "OTP or OAuth or null",
    "purpose": "Credit check or null",
    "validity_from": "2026-01-01 00:00:00",
    "validity_to": "2027-01-01 00:00:00",
    "requested_data_fields": [
      { "field_name": "phone_no", "field_value": "+251911000000" }
    ]
  }
}
```

**Error cases:**

| Condition | HTTP | Response |
|-----------|------|---------|
| `lead_id` missing | 400 | **Malformed** — see Section 2.3 quirk |
| `lead_id` not found | 404 | **Malformed** — see Section 2.3 quirk |
| Lead has no `farmer_profile` | 400 | `VALIDATION_ERROR` — `"Farmer Profile not found for this lead"` |
| No read permission on lead | 403 | `PERMISSION_DENIED` |
| No read permission on farmer profile | 403 | `PERMISSION_DENIED` |

---

#### `POST /api/method/oan_a2c.api.v1.loan_applications.update_basic_profile`
Method restricted to `POST`.

**Parameters:**

| Param | Type | Required | Notes |
|-------|------|----------|-------|
| **`lead_id`** | string | Yes | See validate_lead quirk |
| `email` | string | No | Validated format if provided |
| `region` | string | No | |
| `woreda` | string | No | |
| `kebele` | string | No | |

At least one field should be provided (no error if omitted — returns current values).

**Success response** (HTTP 200):
```json
{
  "status": "success",
  "message": "Basic profile updated successfully",
  "data": {
    "email": "new@email.com",
    "region": "Oromia",
    "woreda": "East Hararge",
    "kebele": "Gudina"
  }
}
```

**Error cases:**

| Condition | HTTP | Response |
|-----------|------|---------|
| `lead_id` missing / not found | 400/404 | **Malformed** — see Section 2.3 quirk |
| Lead has no `farmer_profile` | 400 | `VALIDATION_ERROR` |
| `email` invalid format | 400 | `VALIDATION_ERROR` — `"Invalid email address format"` |
| No write permission on lead | 403 | `PERMISSION_DENIED` |
| No write permission on farmer profile | 403 | `PERMISSION_DENIED` |

---

#### `GET /api/method/oan_a2c.api.v1.loan_applications.get_full_profile`

**Parameters:**

| Param | Type | Required | Notes |
|-------|------|----------|-------|
| **`application_id`** | string | Yes | |

**Success response** (HTTP 200) — all fields:
```json
{
  "status": "success",
  "data": {
    "application_id": "LOANAPP-2026-0001",
    "lead_id": "LEAD-2026-0001",
    "farmer_profile": "FARMPROF-2026-0001",
    "first_name": "Abebe",
    "last_name": "Kebede",
    "region": "Oromia",
    "woreda": "East Hararge",
    "kebele": "Gudina",
    "language": "en_US",
    "phone_number": "+251911000000",
    "farmer_id": "FAYDA-123",
    "consent_id": "CONSENT-2026-0001",
    "loan_type": "Crop Loan",
    "loan_amount": 5000.0,
    "loan_reason": "Purchase seeds and fertilizer",
    "status": "Draft",
    "current_step": 1,
    "loan_officer": null,
    "creation": "2026-01-15 10:30:00",
    "date_of_birth": "1985-03-20 or null",
    "gender": "Male or null",
    "marital_status": "Married or null",
    "size_of_family": 4,
    "number_of_children": 2,
    "no_of_females_family": 2,
    "no_of_males_family": 2,
    "source_of_income": "Farming or null",
    "education_level": "Primary or null",
    "family_member_owns_land_independently": false,
    "total_farmland_size_as_landowner": 2.5,
    "total_farmland_size_as_crop_sharing": 0.0,
    "total_farmland_size_as_rented": 1.0,
    "farmland_size_hectares": "1.2, 0.2, 0.1",
    "land_ownership_status": "Owner or null",
    "soil_fertility_minerals": "High or null",
    "moisture_levels": "Medium or null",
    "certification_id": "CERT-001, CERT-002 or null",
    "certification_photo_url": "url or null"
  }
}
```

**Type notes:** All numeric fields are `float()` cast. Boolean fields are `bool()` cast. Date fields are ISO 8601 strings.

**Error cases:**

| Condition | HTTP | code | message |
|-----------|------|------|---------|
| `application_id` missing | 400 | `VALIDATION_ERROR` | `"application_id is required"` |
| Application not found | 404 | `NOT_FOUND` | `"Loan Application {id} not found"` |
| No read permission | 403 | `PERMISSION_DENIED` | |

---

#### `GET /api/method/oan_a2c.api.v1.loan_applications.get_loan_summary`

No parameters.

**Success response** (HTTP 200):
```json
{
  "status": "success",
  "message": "Loan summary retrieved successfully",
  "data": {
    "total": 85,
    "processing": 30,
    "approved": 25,
    "rejected": 10,
    "tab_counts": {
      "all": 85,
      "my": 20,
      "unassigned": 15
    }
  }
}
```

> **`my` / `unassigned`:** Only present if the `loan_officer` field exists on the doctype. `my` = applications where `loan_officer == frappe.session.user`. `unassigned` = applications where `loan_officer` is empty.

**Error cases:**

| Condition | HTTP | code |
|-----------|------|------|
| No read permission on Loan Application | 403 | `PERMISSION_DENIED` |

---

#### `GET /api/method/oan_a2c.api.v1.loan_applications.get_loan_metadata`

No parameters.

**Success response** (HTTP 200):
```json
{
  "status": "success",
  "data": { "statuses": ["Draft", "Processing", "Approved", "Rejected"] }
}
```

**Error cases:**

| Condition | HTTP | code |
|-----------|------|------|
| No read permission on Loan Application | 403 | `PERMISSION_DENIED` |

---

#### `GET /api/method/oan_a2c.api.v1.loan_applications.get_all_loans`

**Parameters:**

| Param | Type | Required | Default | Constraint |
|-------|------|----------|---------|-----------|
| `status` | string | No | — | Single value, comma-separated list, or stringified JSON array. Each value validated against `{Draft, Processing, Approved, Rejected}`, de-duplicated. Invalid values silently dropped (`in` filter). |
| `loan_amount` | float | No | — | Exact match (overridden by min/max if both provided) |
| `min_loan_amount` | float | No | — | |
| `max_loan_amount` | float | No | — | |
| `loan_type` | string | No | — | Single value, comma-separated list, or stringified JSON array (`in` filter). Free-text Data field on A2C Loan Application — **not** validated against an allowlist; values matched as-is. |
| `location` | string | No | — | `LIKE` match |
| `phone_number` | string | No | — | `LIKE` match |
| `loan_officer` | string | No | — | Filter by assigned Loan Officer (User). Single value or **comma-separated** list (`in` filter). The literal `unassigned` matches loans with no officer (same notion as the `unassigned` tab in `get_loan_summary`) and can be combined with named users. Not allowlist-validated — an unknown user yields no matches. |
| `from_date` | string | No | — | ISO date. Filters `creation` |
| `to_date` | string | No | — | ISO date. End time is padded to `23:59:59` |
| `page` | int | No | 1 | |
| `page_size` | int | No | 20 | Clamped to [1, 100] |
| `lead_id` | string | No | — | Exact match |
| `search_query` | string | No | — | `LIKE` match on `name`, `phone_number`, `farmer_id`, `first_name`, `last_name` |

**Success response** (HTTP 200):
```json
{
  "status": "success",
  "message": "Loan applications retrieved successfully",
  "data": [
    {
      "application_id": "LOANAPP-2026-0001",
      "status": "Draft",
      "step": 1,
      "lead_id": "LEAD-2026-0001",
      "loan_amount": 5000.0,
      "loan_type": "Crop Loan",
      "location": "Oromia",
      "phone_number": "+251911000000",
      "creation": "2026-01-15 10:30:00"
    }
  ],
  "pagination": {
    "page": 1,
    "limit": 20,
    "total": 85,
    "total_pages": 5,
    "has_next": true
  }
}
```

> **Type notes:** `loan_amount` is `float`. `step` is `int`. `creation` is an ISO 8601 string.

**Error cases:**

| Condition | HTTP | code |
|-----------|------|------|
| No read permission on Loan Application | 403 | `PERMISSION_DENIED` |

---

#### `POST /api/method/oan_a2c.api.v1.loan_applications.upload_supporting_documents`
Method restricted to `POST`. Multipart form data.

**Parameters:**

| Param | Type | Required | Notes |
|-------|------|----------|-------|
| **`application_id`** | string | Yes | Form field |
| files | multipart | Yes | One or more files |

**File constraints:**
- Allowed extensions: `.pdf`, `.png`, `.jpg`, `.jpeg` (case-insensitive)
- Max size per file: 5 MB
- Files stored as private

**Success response** (HTTP 200):
```json
{
  "status": "success",
  "message": "Supporting documents uploaded successfully",
  "data": [
    {
      "name": "FILE-2026-0001",
      "file_url": "/private/files/document.pdf",
      "file_name": "document.pdf"
    }
  ]
}
```

**Error cases:**

| Condition | HTTP | code | message |
|-----------|------|------|---------|
| `application_id` missing | 400 | `VALIDATION_ERROR` | `"application_id is required"` |
| Application not found | 404 | `NOT_FOUND` | |
| No files in request | 400 | `VALIDATION_ERROR` | `"No files found in request"` |
| Invalid file extension | 400 | `VALIDATION_ERROR` | `"Invalid file type for {name}. Only PDF, PNG, and JPG are allowed."` |
| File exceeds 5 MB | 400 | `VALIDATION_ERROR` | `"File {name} exceeds the 5MB size limit."` |
| No write permission on application | 403 | `PERMISSION_DENIED` | |

---

#### `GET /api/method/oan_a2c.api.v1.loan_applications.get_supporting_documents`

**Parameters:**

| Param | Type | Required | Notes |
|-------|------|----------|-------|
| **`application_id`** | string | Yes | |

**Success response** (HTTP 200):
```json
{
  "status": "success",
  "data": [
    {
      "name": "FILE-2026-0001",
      "file_name": "document.pdf",
      "file_url": "/private/files/document.pdf",
      "creation": "2026-01-15 11:00:00"
    }
  ]
}
```

**Error cases:**

| Condition | HTTP | code |
|-----------|------|------|
| `application_id` missing | 400 | `VALIDATION_ERROR` |
| Application not found | 404 | `NOT_FOUND` |
| No read permission | 403 | `PERMISSION_DENIED` |

---

#### `GET /api/method/oan_a2c.api.v1.loan_applications.download_supporting_document`

**Parameters:**

| Param | Type | Required | Notes |
|-------|------|----------|-------|
| **`file_id`** | string | Yes | |
| `view` | int | No | Pass `1` to stream inline (browser display). Omit for download. |

**Success response:** Binary file content. Not an envelope. Response headers set:
- `Content-Disposition: attachment; filename="..."` (or `inline` if `view=1`)

**Error cases:**

| Condition | HTTP | code |
|-----------|------|------|
| `file_id` missing | 400 | `VALIDATION_ERROR` |
| File not found | 404 | `NOT_FOUND` |
| No read permission on attached doctype | 403 | `PERMISSION_DENIED` |

---

#### `POST /api/method/oan_a2c.api.v1.loan_applications.delete_supporting_document`
Method restricted to `POST`.

**Parameters:**

| Param | Type | Required | Notes |
|-------|------|----------|-------|
| **`application_id`** | string | Yes | |
| **`file_id`** | string | Yes | |

**Success response** (HTTP 200):
```json
{
  "status": "success",
  "message": "File deleted successfully",
  "data": null
}
```

**Error cases:**

| Condition | HTTP | code | message |
|-----------|------|------|---------|
| Either param missing | 400 | `VALIDATION_ERROR` | `"application_id and file_id are required"` |
| Application not found | 404 | `NOT_FOUND` | |
| File not found or not attached to this application | 404 | `NOT_FOUND` | `"File not found or not attached to this application"` |
| No write permission | 403 | `PERMISSION_DENIED` | |

---

#### `POST /api/method/oan_a2c.api.v1.loan_applications.create_loan_application`
Method restricted to `POST`.

**Parameters:**

| Param | Type | Required | Notes |
|-------|------|----------|-------|
| **`lead_id`** | string | Yes | See validate_lead quirk in Section 2.3 |

**Prerequisites (checked in order):**
1. Lead must exist
2. Lead must have `farmer_profile` linked (consent webhook must have completed)
3. At least one `A2C Credit Information` record must exist for the lead
4. No existing loan application for this lead

**Success response** (HTTP 200):
```json
{
  "status": "success",
  "message": "Loan application created successfully",
  "data": {
    "application_id": "LOANAPP-2026-0001",
    "application": {
      "name": "LOANAPP-2026-0001",
      "status": "Draft",
      "farmer_profile": "FARMPROF-2026-0001",
      "first_name": "Abebe",
      "last_name": "Kebede",
      "loan_type": "Crop Loan",
      "loan_amount": 5000.0,
      "current_step": null
    }
  }
}
```

> **Correction from previous version:** Returns full `application` object, not only `application_id`.

**Error cases:**

| Condition | HTTP | Response |
|-----------|------|---------|
| `lead_id` missing / not found | 400/404 | **Malformed** — see Section 2.3 quirk |
| Loan application already exists for lead | 400 | `VALIDATION_ERROR` — `"Loan application already exists for this lead"` |
| Lead has no `farmer_profile` | 400 | `VALIDATION_ERROR` — `"No Farmer Profile found for this lead. Webhook consent might not be completed."` |
| Lead has no `A2C Credit Information` | 400 | `VALIDATION_ERROR` — `"Credit Information is missing for this lead. A loan application requires a valid loan amount."` |
| No create permission on Loan Application | 403 | `PERMISSION_DENIED` | |

---

#### `POST /api/method/oan_a2c.api.v1.loan_applications.update_loan_status`
Method restricted to `POST`.

**Parameters:**

| Param | Type | Required | Notes |
|-------|------|----------|-------|
| **`application_id`** | string | Yes | |
| **`status`** | string | Yes | No explicit allowlist check — any string accepted if not terminal |

**Success response** (HTTP 200):
```json
{
  "status": "success",
  "message": "Loan application status updated to Processing",
  "data": null
}
```

**Error cases:**

| Condition | HTTP | code | message |
|-----------|------|------|---------|
| Either param missing | 400 | `VALIDATION_ERROR` | `"application_id and status are required"` |
| Application not found | 404 | `NOT_FOUND` | `"Loan Application {id} not found"` |
| Current status is `Rejected` or `Approved` | 400 | `VALIDATION_ERROR` | `"Cannot change status. Loan application is already {status}"` |
| No write permission | 403 | `PERMISSION_DENIED` | |

---

#### `POST /api/method/oan_a2c.api.v1.loan_applications.update_loan_step`
Method restricted to `POST`.

**Parameters:**

| Param | Type | Required | Notes |
|-------|------|----------|-------|
| **`application_id`** | string | Yes | |
| **`step`** | int | Yes | |

**Step validation rules (from source):**
- Valid range: `1` to `4`
- Cannot skip steps: if `current_step = 2`, only `step = 1`, `2`, or `3` are accepted. `step = 4` → 400.
- Back-navigation to any lower step is allowed.

**Success response** (HTTP 200):
```json
{
  "status": "success",
  "message": "Loan application step updated to 2",
  "data": null
}
```

**Error cases:**

| Condition | HTTP | code | message |
|-----------|------|------|---------|
| Either param missing | 400 | `VALIDATION_ERROR` | `"application_id and step are required"` |
| Application not found | 404 | `NOT_FOUND` | |
| `step` not in [1, 4] | 400 | `VALIDATION_ERROR` | `"Step must be between 1 and 4"` |
| `step` > `current_step + 1` | 400 | `VALIDATION_ERROR` | `"Invalid step transition. You cannot skip steps."` |
| No write permission | 403 | `PERMISSION_DENIED` | |

---

### 4.4 Webhooks (`api/v1/webhooks.py`)

---

#### `POST /api/method/oan_a2c.api.v1.webhooks.lead_inbound`
JWT-exempt. Requires Frappe session or `Authorization: token apikey:apisecret`.

**Parameters:**

| Param | Type | Required | Default | Notes |
|-------|------|----------|---------|-------|
| **`phone_number`** | string | Yes | — | |
| `lead_source` | string | No | `"Missed Call"` | Coerced to `"Missed Call"` if invalid |
| `external_ref_id` | string | No | — | Used for primary deduplication |
| `timestamp` | string | No | — | Stored in call notes |

**Lead source allowlist:** `Missed Call`, `IVR`, `SMS`, `Agent Entry`

**Idempotency logic (checked in order):**
1. If `external_ref_id` matches an existing lead → update that lead's `call_notes`, return it
2. Else if `phone_number` matches a lead with status `Active` or `Verified` → update that lead's `call_notes`, return it
3. Else → create new lead with status `Active`

**Success response — new lead** (HTTP 200):
```json
{
  "status": "success",
  "message": "Lead captured successfully.",
  "data": { "lead_id": "LEAD-2026-0001" }
}
```

**Success response — existing lead updated** (HTTP 200):
```json
{
  "status": "success",
  "message": "Existing active lead updated with new event.",
  "data": { "lead_id": "LEAD-2026-0001" }
}
```

**Error cases:**

| Condition | HTTP | code |
|-----------|------|------|
| `phone_number` missing | 400 | `VALIDATION_ERROR` |
| No create permission on A2C Lead | 403 | `PERMISSION_DENIED` |

---

### 4.5 Consent Data Webhook (`api/v1/webhook_consent_data.py`)

---

#### `POST /api/method/oan_a2c.api.v1.webhook_consent_data.receive_consent_data`
JWT-exempt. Called by OpenG2P system.

**Request body** (JSON):
```json
{
  "consent": {
    "consent_creation_request_id": "OG2P-CONSENT-001",
    "status": "approved",
    "approved_at": "2026-01-15T10:00:00Z"
  },
  "farmer": { "id": 42, "name": "Abebe Kebede" },
  "selected_data": { 
    "10010": { 
      "Full Name": "Abebe Kebede",
      "Mobile Number": ["+251911000000"]
    } 
  }
}
```

**Success response** (HTTP 202):
```json
{
  "status": "success",
  "message": "Data accepted for background processing",
  "data": { "consent_request": "CONREQ-2026-0001" }
}
```

> HTTP 202 is set explicitly via `frappe.response["http_status_code"] = 202`.

**Error cases:**

| Condition | HTTP | code | message |
|-----------|------|------|---------|
| `consent_creation_request_id` missing in payload | 400 | `VALIDATION_ERROR` | `"Missing consent_creation_request_id"` |
| No A2C Consent Request found with that ID | 404 | `NOT_FOUND` | `"Consent Request not found: {id}"` |
| Linked lead not found | 404 | `NOT_FOUND` | `"Linked Lead not found: {lead_id}"` |
| No write permission on Consent Request | 403 | `PERMISSION_DENIED` | |

**Background job** (`process_consent_data`):
- Sets user context from `A2C Consent Request.owner` (falls back to `Administrator`)
- Creates or updates `A2C Farmer Profile`
- Links farmer profile to `A2C Lead`
- On failure: sets Consent Request status to `"Failed"` and logs to Error Log

---

### 4.6 Consent Flow (`api/v1/consent/consent.py`)

All three endpoints accept parameters via JSON body, form dict, or kwargs (merged by `_parse_request()`).

---

#### `GET/POST /api/method/oan_a2c.api.v1.consent.consent.search_farmer`

**Parameters:**

| Param | Type | Required | Notes |
|-------|------|----------|-------|
| **`fayda_id`** | string | Yes | National ID |

**Success response** (HTTP 200):
```json
{
  "status": "success",
  "message": "Farmer found successfully.",
  "data": {
    "farmer": {
      "id": 42,
      "name": "Abebe Kebede",
      "mobile": "+251911000000",
      "phone": "+251911000000",
      "profile_image_url": "https://example.com/image.jpg",
      "type": "FIN"
    }
  }
}
```

**Error cases:**

| Condition | HTTP | code | message |
|-----------|------|------|---------|
| `fayda_id` missing | 400 | `VALIDATION_ERROR` | `"fayda_id is required"` |
| Farmer not found  | 404 | `NOT_FOUND` | `"Farmer with Fayda ID '{id}' not found ."` |
| OpenG2P unreachable | 500 | `INTERNAL_ERROR` | |

---

#### `GET/POST /api/method/oan_a2c.api.v1.consent.consent.request_otp`

**Parameters:**

| Param | Type | Required | Notes |
|-------|------|----------|-------|
| **`fayda_id`** | string | Yes | |
| **`lead_id`** | string | Yes | |
| `idempotency_key` | string | No | Optional key to prevent duplicate OTP requests |

**Success response** (HTTP 200):
```json
{
  "status": "success",
  "message": "OTP sent successfully. Proceed to verify OTP.",
  "data": {
    "consent_request": "CONREQ-2026-0001",
    "transaction_id": "TXN-ABC-123",
    "masked_phone": "XXX-XXX-7890"
  }
}
```

**Error cases:**

| Condition | HTTP | code | message |
|-----------|------|------|---------|
| `lead_id` missing | 400 | `VALIDATION_ERROR` | |
| `fayda_id` missing | 400 | `VALIDATION_ERROR` | |
| Rate limit exceeded | 429 | `VALIDATION_ERROR` | `"Rate limit exceeded. Try again later."` |

**Side effects:**
- Stores Odoo session cookie/dict in Redis.
- Creates `A2C Consent Request` with status `"Pending OTP"`.

---

#### `GET/POST /api/method/oan_a2c.api.v1.consent.consent.verify_otp`

**Parameters:**

| Param | Type | Required | Notes |
|-------|------|----------|-------|
| **`lead_id`** | string | Yes | |
| **`consent_request`** | string | Yes | |
| **`otp_code`** | string | Yes | |

**Success response** (HTTP 200):
```json
{
  "status": "success",
  "message": "OTP verified successfully. Proceed to submit consent.",
  "data": {
    "lead_id": "LEAD-2026-0001",
    "consent_request": "CONREQ-2026-0001",
    "transaction_id": "TXN-ABC-123",
    "status": "OTP Verified"
  }
}
```

**Side effects:**
- Sets `otp_verified_at` on the `A2C Consent Request`.

---

#### `GET/POST /api/method/oan_a2c.api.v1.consent.consent.submit_consent`

**Parameters:**

| Param | Type | Required | Notes |
|-------|------|----------|-------|
| **`lead_id`** | string | Yes | |
| **`consent_request`** | string | Yes | |
| `consent_type` | string | No | Default: `"specific"` |
| `consent_reason_id` | integer | No | Default: `1` |
| `validity_months` | integer | No | Default: `12` |
| **`consent_form_filename`** | string | Yes | |
| **`consent_form_base64`** | string | Yes | Base64-encoded PDF/Image file |
| `allowed_data_field_ids` | array | No | List of permitted OpenG2P field IDs |

**Success response** (HTTP 200):
```json
{
  "status": "success",
  "message": "Consent submitted and approved successfully.",
  "data": {
    "lead_id": "LEAD-2026-0001",
    "consent_request": "CONREQ-2026-0001",
    "status": "Approved",
    "openg2p_consent_id": "OG2P-CONSENT-001",
    "consent_receipt": "HMAC_SIGNATURE_STRING",
    "farmer_preview": {
      "given_name": "Abebe",
      "family_name": "Kebede",
      "email": "",
      "phone_no": ["+251911000000"]
    }
  }
}
```

**Side effects:**
- Uploads consent document attachment to OpenG2P.
- Submits and approves the consent request .
- Marks `A2C Consent Request` as `"Approved"` and updates the lead with the farmer profile data.
- Queues WebSub delivery payload.

---

#### `GET/POST /api/method/oan_a2c.api.v1.consent.consent.get_partner_allowed_data_field_ids`

No parameters.

**Success response** (HTTP 200):
```json
{
  "status": "success",
  "message": "Allowed data field IDs retrieved successfully.",
  "data": {
    "allowed_data_field_ids": [1, 2]
  }
}
```

---

#### `GET/POST /api/method/oan_a2c.api.v1.consent.consent.get_consent_reasons`

No parameters.

**Success response** (HTTP 200):
```json
{
  "status": "success",
  "message": "Consent reasons retrieved successfully.",
  "data": [
    {
      "id": 1,
      "name": "Loan credit analysis"
    }
  ]
}
```

---

#### `GET/POST /api/method/oan_a2c.api.v1.consent.consent.get_consent_allowed_fields`

No parameters.

**Success response** (HTTP 200):
```json
{
  "status": "success",
  "message": "Allowed data fields retrieved successfully.",
  "data": [
    {
      "id": 1,
      "name": "Phone Number"
    }
  ]
}
```

---

---

## 5. Background Jobs

| Job function | Queue | Triggered by | User context |
|-------------|-------|-------------|-------------|
| `process_consent_data` | `default` | `receive_consent_data` webhook | Set from `A2C Consent Request.owner`, fallback to `Administrator` |
| `deliver_websub_payload` | `default` | `verify_otp_for_lead` | None set — runs without user context |

---

## 6. Caching

| Redis key pattern | Content | TTL | Invalidation |
|-------------------|---------|-----|-------------|
| `odoo_session_dict_{transaction_id}` | Odoo session cookie dict (requests-format) | 1800s | TTL expiry only |
| `odoo_session_{transaction_id}` | Legacy fallback: single session_id string | 1800s | TTL expiry only |

**Nothing else is cached.** `get_lead_metadata`, `get_loan_metadata`, `get_lead_summary`, `get_loan_summary` hit the database on every call.

---

## 7. Discrepancies vs `api-flow-frontend.md`

| # | Frontend assumption | Backend reality | Action |
|---|---------------------|----------------|--------|
| 1 | `get_leads` returns `visit_date` and `schedule_status` | Not in response — these fields don't exist on A2C Lead | Frontend needs separate `get_visit_schedules` call |
| 2 | All `leads.py` endpoints returned ad-hoc shapes | **Corrected:** All files now use `@handle_api_errors` and standard envelope | Frontend can use uniform response parsing |
| 3 | `create_lead` returns only `{ lead_id }` | **Corrected:** Returns `{ lead_id, lead: { full object } }` | Eliminate post-create `get_leads` refetch |
| 4 | `create_loan_application` returns only `{ application_id }` | **Corrected:** Returns `{ application_id, application: { ...fields } }` | Eliminate post-create `get_all_loans` refetch |
| 5 | `update_loan_step` accepts any positive integer | **Corrected:** Valid range is 1–4 only; step-skipping rejected | Frontend wizard must enforce 1→2→3→4 ordering |
| 6 | Visit schedules have no terminal states | **Corrected:** `Completed` and `Missed` are terminal | Disable status update UI for these states |
| 7 | `get_lead_timeline` / `get_lead_call_logs` return flat arrays | Return objects: `{ lead_id, timeline }` / `{ lead_id, call_logs }` | Update response destructuring |
| 8 | `get_assignable_users` has no pagination | Now paginated with offset-style `{ start, page_length, total_count, has_next }` | Update pagination handling |
| 9 | `verify_otp_for_lead` doesn't return consent receipt | Returns `consent_receipt` HMAC signature string | Available for storage if needed |
| 10 | `login` response has `linked_bank` key | Key is `bank` | Update field reference |
