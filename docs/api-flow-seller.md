# api-flow-seller.md — Loan Marketplace Seller API Contract

_Derived from direct source code analysis — `apps/oan_a2c/oan_a2c/api/v1/seller/`_

> **Source of truth:** This document reflects what the backend **actually implements** for the Loan Marketplace Seller (Bank Agent) APIs.

---

## 1. Authentication & Security Architecture

### 1.1 JWT Scheme

All endpoints under `/api/method/oan_a2c.api.v1.seller.*` require a Bearer JWT token, managed via the central `oan_a2c` auth gateway.

- Header: `Authorization: token <api_key>:<api_secret>` or `Authorization: Bearer <jwt_token>`

### 1.2 Multi-Tenancy (Bank Isolation)

These APIs rely on Frappe's underlying permissions architecture mapped in `hooks.py`:

- **Query-Level Security**: `get_all` queries are intercepted to inject `WHERE bank = '{user_bank}'`.
- **Document-Level Security**: `has_permission` checks ensure `doc.bank == {user_bank}` before allowing write/update operations.
- Consequently, Bank Agents using these endpoints will **only** ever see and interact with Loan Products and Loan Applications scoped to their respective bank.

---

## 2. Response Envelope

All endpoints in this module use the standard `@handle_api_errors` and `success_response` utilities from `api/utils.py`.

### 2.1 Success Envelope

```json
{
  "status": "success",
  "message": "Human-readable string",
  "data": null | {} | []
}
```

### 2.2 Error Envelope

```json
{
  "status": "error",
  "message": "Human-readable description",
  "code": "MACHINE_READABLE_CODE",
  "details": {}
}
```

---

## 3. Endpoint Referencewearewher

Convention for parameter tables:

- **bold** = Required. Missing value raises a `VALIDATION_ERROR` or `MandatoryError`.
- plain = Optional.

---

### 3.1 Dashboard (`api/v1/seller/dashboard.py`)

#### `GET /api/method/oan_a2c.api.v1.seller.dashboard.get_stats`

Retrieves high-level aggregated statistics for the bank's loan products and applications.

**Parameters:** None.

**Success response** (HTTP 200):

```json
{
  "status": "success",
  "message": "Success",
  "data": {
    "stats": {
      "total_products": 10,
      "active_products": 8,
      "total_applications": 150,
      "pending_applications": 45,
      "approved_applications": 20,
      "total_approved_amount": 100000.0
    }
  }
}
```

---

### 3.2 Applicants (`api/v1/seller/applicants.py`)

#### `GET /api/method/oan_a2c.api.v1.seller.applicants.list_applicants`

Retrieves a paginated list of all loan applications belonging to the bank.

**Parameters:**

| Param      | Type   | Required | Default | Notes                               |
| ---------- | ------ | -------- | ------- | ----------------------------------- |
| `limit`  | int    | No       | 20      | Pagination limit                    |
| `start`  | int    | No       | 0       | Pagination offset                   |
| `status` | string | No       | —      | Filter applications by exact status |

**Success response** (HTTP 200):

```json
{
  "status": "success",
  "message": "Success",
  "data": {
    "applicants": [
      {
        "name": "APP-2026-0001",
        "creation": "2026-07-21 10:00:00",
        "status": "Submitted",
        "requested_amount": 5000.0,
        "loan_product": "PROD-001",
        "customer_name": "Abebe Kebede"
      }
    ]
  }
}
```

---

#### `GET /api/method/oan_a2c.api.v1.seller.applicants.get_applicants_for_product`

Retrieves a paginated list of loan applications linked to a specific loan product.

**Parameters:**

| Param                    | Type   | Required | Default | Notes                          |
| ------------------------ | ------ | -------- | ------- | ------------------------------ |
| **`product_id`** | string | Yes      | —      | The ID of the A2C Loan Product |
| `limit`                | int    | No       | 20      | Pagination limit               |
| `start`                | int    | No       | 0       | Pagination offset              |

**Success response** (HTTP 200):

```json
{
  "status": "success",
  "message": "Success",
  "data": {
    "applicants": [
      {
        "name": "APP-2026-0002",
        "creation": "2026-07-21 11:00:00",
        "status": "Under Review",
        "requested_amount": 12000.0,
        "customer_name": "Tigist Bekele"
      }
    ]
  }
}
```

**Error cases:**

- 403 `PERMISSION_DENIED`: If the user lacks read access to the specified Loan Product (e.g., belongs to another bank).

---

#### `GET /api/method/oan_a2c.api.v1.seller.applicants.search_applicants`

Searches loan applications by application ID (name) or customer name.

**Parameters:**

| Param               | Type   | Required | Default | Notes                                                |
| ------------------- | ------ | -------- | ------- | ---------------------------------------------------- |
| **`query`** | string | Yes      | —      | Substring match against`name` OR `customer_name` |
| `limit`           | int    | No       | 20      | Pagination limit                                     |
| `start`           | int    | No       | 0       | Pagination offset                                    |

**Success response** (HTTP 200):

```json
{
  "status": "success",
  "message": "Success",
  "data": {
    "applicants": [ ... ]
  }
}
```

---

#### `POST /api/method/oan_a2c.api.v1.seller.applicants.update_status`

Updates the status of a specific loan application.

**Parameters (JSON Body):**

| Param                        | Type   | Required | Notes                                                                                     |
| ---------------------------- | ------ | -------- | ----------------------------------------------------------------------------------------- |
| **`application_id`** | string | Yes      | The ID of the A2C Loan Application                                                        |
| **`status`**         | string | Yes      | Must be one of:`Submitted`, `Under Review`, `Approved`, `Rejected`, `Disbursed` |

**Success response** (HTTP 200):

```json
{
  "status": "success",
  "message": "Status updated to Under Review",
  "data": {
    "message": "Status updated to Under Review"
  }
}
```

**Error cases:**

- 400 `VALIDATION_ERROR`: If the `status` does not match the allowed pattern.
- 403 `PERMISSION_DENIED`: If the user lacks write access to the Loan Application.

---

#### `POST /api/method/oan_a2c.api.v1.seller.applicants.assign_applicant`

Assigns a loan application to a specific user (Bank Agent).

**Parameters (JSON Body):**

| Param                        | Type   | Required | Notes                                   |
| ---------------------------- | ------ | -------- | --------------------------------------- |
| **`application_id`** | string | Yes      | The ID of the A2C Loan Application      |
| **`assigned_to`**    | string | Yes      | Email or user ID of the target assignee |

**Success response** (HTTP 200):

```json
{
  "status": "success",
  "message": "Applicant assigned successfully",
  "data": {
    "message": "Applicant assigned successfully"
  }
}
```

**Error cases:**

- 403 `PERMISSION_DENIED`: If the user lacks write access to the Loan Application.

---

#### `POST /api/method/oan_a2c.api.v1.seller.applicants.unassign_applicant`

Removes an assignment from a loan application.

**Parameters (JSON Body / Query):**

| Param                        | Type   | Required | Notes                                    |
| ---------------------------- | ------ | -------- | ---------------------------------------- |
| **`application_id`** | string | Yes      | The ID of the A2C Loan Application       |
| **`unassign_from`**  | string | Yes      | Email or user ID of the user to unassign |

**Success response** (HTTP 200):

```json
{
  "status": "success",
  "message": "Applicant unassigned successfully",
  "data": {
    "message": "Applicant unassigned successfully"
  }
}
```

**Error cases:**

- 403 `PERMISSION_DENIED`: If the user lacks write access to the Loan Application.

---

## 4. Onboarding & Registration (`api/v1/seller/onboarding.py`)

### 4.1 `POST /api/method/oan_a2c.api.v1.seller.onboarding.register_bank`

Registers a new bank entity.

**Parameters (JSON Body):**
| Param | Type | Required | Notes |
|---|---|---|---|
| `bank_name` | string | Yes | |
| `bank_code` | string | Yes | |
| `entity_type` | string | Yes | |
| `registered_street` | string | Yes | |
| `registered_city` | string | Yes | |
| `registered_country` | string | Yes | |
| `registered_postal_code` | string | Yes | |
| `registered_email` | string | Yes | |
| `registered_phone` | string | Yes | |

### 4.2 `POST /api/method/oan_a2c.api.v1.seller.onboarding.save_org_contacts`
Saves organizational contacts (GRO, OPS).

### 4.3 `POST /api/method/oan_a2c.api.v1.seller.onboarding.upload_kyc_document`
Uploads the KYC Document.

**Parameters (JSON Body):**
| Param | Type | Required | Notes |
|---|---|---|---|
| `filename` | string | Yes | Example: `kyc.pdf` |
| `filedata` | string | Yes | Base64 Encoded Document String |

### 4.4 `GET /api/method/oan_a2c.api.v1.seller.onboarding.get_bank_status`
Retrieves the bank's current onboarding status.

### 4.5 `POST /api/method/oan_a2c.api.v1.seller.onboarding.update_bank_status`
(A2C Administrator only) Updates the bank's onboarding status.

**Parameters (JSON Body):**
| Param | Type | Required | Notes |
|---|---|---|---|
| `bank_code` | string | Yes | |
| `new_status` | string | Yes | One of: `Onboarding`, `Active`, `Suspended` |

### 4.6 `POST /api/method/oan_a2c.api.v1.seller.onboarding.invite_user`
Invites a new user to the bank.

**Parameters (JSON Body):**
| Param | Type | Required | Notes |
|---|---|---|---|
| `email` | string | Yes | |
| `full_name` | string | Yes | |
| `role` | string | Yes | |

### 4.7 `POST /api/method/oan_a2c.api.v1.seller.onboarding.update_user_profile`
Updates a user profile.

**Parameters (JSON Body):**
| Param | Type | Required | Notes |
|---|---|---|---|
| `email` | string | Yes | |
| `full_name` | string | No | |
| `role` | string | No | |

### 4.8 `POST /api/method/oan_a2c.api.v1.seller.onboarding.list_users`
Lists all users under the current bank.

### 4.9 `POST /api/method/oan_a2c.api.v1.seller.onboarding.deactivate_user`
Deactivates a user.

---

## 5. Authentication (`api/v1/auth.py`)

### 5.1 `POST /api/method/oan_a2c.api.v1.auth.register_user`
Registers a new user (first step of onboarding).

### 5.2 `POST /api/method/oan_a2c.api.v1.auth.login`
Logs the user in and returns access and refresh tokens.


---

## 4. Onboarding & Registration (`api/v1/seller/onboarding.py`)

### 4.1 `POST /api/method/oan_a2c.api.v1.seller.onboarding.register_bank`

Registers a new bank entity.

**Parameters (JSON Body):**
| Param | Type | Required | Notes |
|---|---|---|---|
| `bank_name` | string | Yes | |
| `bank_code` | string | Yes | |
| `entity_type` | string | Yes | |
| `registered_street` | string | Yes | |
| `registered_city` | string | Yes | |
| `registered_country` | string | Yes | |
| `registered_postal_code` | string | Yes | |
| `registered_email` | string | Yes | |
| `registered_phone` | string | Yes | |

### 4.2 `POST /api/method/oan_a2c.api.v1.seller.onboarding.save_org_contacts`
Saves organizational contacts (GRO, OPS).

### 4.3 `POST /api/method/oan_a2c.api.v1.seller.onboarding.upload_kyc_document`
Uploads the KYC Document.

**Parameters (JSON Body):**
| Param | Type | Required | Notes |
|---|---|---|---|
| `filename` | string | Yes | Example: `kyc.pdf` |
| `filedata` | string | Yes | Base64 Encoded Document String |

### 4.4 `GET /api/method/oan_a2c.api.v1.seller.onboarding.get_bank_status`
Retrieves the bank's current onboarding status.

### 4.5 `POST /api/method/oan_a2c.api.v1.seller.onboarding.update_bank_status`
(Bank Admin only) Updates the bank's onboarding status.

**Parameters (JSON Body):**
| Param | Type | Required | Notes |
|---|---|---|---|
| `bank_code` | string | Yes | |
| `new_status` | string | Yes | One of: `Onboarding`, `Active`, `Suspended` |

### 4.6 `POST /api/method/oan_a2c.api.v1.seller.onboarding.invite_user`
Invites a new user to the bank.

**Parameters (JSON Body):**
| Param | Type | Required | Notes |
|---|---|---|---|
| `email` | string | Yes | |
| `full_name` | string | Yes | |
| `role` | string | Yes | |

### 4.7 `POST /api/method/oan_a2c.api.v1.seller.onboarding.update_user_profile`
Updates a user profile.

**Parameters (JSON Body):**
| Param | Type | Required | Notes |
|---|---|---|---|
| `email` | string | Yes | |
| `full_name` | string | No | |
| `role` | string | No | |

### 4.8 `GET /api/method/oan_a2c.api.v1.seller.onboarding.list_users`
Lists all users under the current bank.

### 4.9 `POST /api/method/oan_a2c.api.v1.seller.onboarding.deactivate_user`
Deactivates a user.

---

## 5. Authentication (`api/v1/auth.py`)

### 5.1 `POST /api/method/oan_a2c.api.v1.auth.register_user`
Registers a new user (first step of onboarding).

### 5.2 `POST /api/method/oan_a2c.api.v1.auth.login`
Logs the user in and returns access and refresh tokens.

---

## 4. Onboarding & Registration (`api/v1/seller/onboarding.py`)

### 4.1 `POST /api/method/oan_a2c.api.v1.seller.onboarding.register_bank`

Registers a new bank entity.

**Parameters (JSON Body):**
| Param | Type | Required | Notes |
|---|---|---|---|
| `bank_name` | string | Yes | |
| `bank_code` | string | Yes | |
| `entity_type` | string | Yes | |
| `registered_street` | string | Yes | |
| `registered_city` | string | Yes | |
| `registered_country` | string | Yes | |
| `registered_postal_code` | string | Yes | |
| `registered_email` | string | Yes | |
| `registered_phone` | string | Yes | |

### 4.2 `POST /api/method/oan_a2c.api.v1.seller.onboarding.save_org_contacts`
Saves organizational contacts (GRO, OPS).

### 4.3 `POST /api/method/oan_a2c.api.v1.seller.onboarding.upload_kyc_document`
Uploads the KYC Document.

**Parameters (JSON Body):**
| Param | Type | Required | Notes |
|---|---|---|---|
| `filename` | string | Yes | Example: `kyc.pdf` |
| `filedata` | string | Yes | Base64 Encoded Document String |

### 4.4 `GET /api/method/oan_a2c.api.v1.seller.onboarding.get_bank_status`
Retrieves the bank's current onboarding status.

### 4.5 `POST /api/method/oan_a2c.api.v1.seller.onboarding.update_bank_status`
(Bank Admin only) Updates the bank's onboarding status.

**Parameters (JSON Body):**
| Param | Type | Required | Notes |
|---|---|---|---|
| `bank_code` | string | Yes | |
| `new_status` | string | Yes | One of: `Onboarding`, `Active`, `Suspended` |

### 4.6 `POST /api/method/oan_a2c.api.v1.seller.onboarding.invite_user`
Invites a new user to the bank.

**Parameters (JSON Body):**
| Param | Type | Required | Notes |
|---|---|---|---|
| `email` | string | Yes | |
| `full_name` | string | Yes | |
| `role` | string | Yes | |

### 4.7 `POST /api/method/oan_a2c.api.v1.seller.onboarding.update_user_profile`
Updates a user profile.

**Parameters (JSON Body):**
| Param | Type | Required | Notes |
|---|---|---|---|
| `email` | string | Yes | |
| `full_name` | string | No | |
| `role` | string | No | |

### 4.8 `GET /api/method/oan_a2c.api.v1.seller.onboarding.list_users`
Lists all users under the current bank.

### 4.9 `POST /api/method/oan_a2c.api.v1.seller.onboarding.deactivate_user`
Deactivates a user.

---

## 5. Authentication (`api/v1/auth.py`)

### 5.1 `POST /api/method/oan_a2c.api.v1.auth.register_user`
Registers a new user (first step of onboarding).

### 5.2 `POST /api/method/oan_a2c.api.v1.auth.login`
Logs the user in and returns access and refresh tokens.
