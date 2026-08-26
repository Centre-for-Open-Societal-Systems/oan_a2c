# api-flow-farmer.md — Loan Marketplace Farmer (B2C) API Contract

_Derived from direct source code analysis — `apps/oan_a2c/oan_a2c/api/v1/farmer/`, `apps/oan_a2c/oan_a2c/api/auth.py`, and `apps/oan_a2c/oan_a2c/api/v1/auth.py`_

> **Source of truth:** This document reflects the complete, up-to-date backend implementation for all Loan Marketplace Farmer (B2C) APIs.

---

## 1. Authentication, Permissions & Scoping Architecture

### 1.1 Authentication & Session

- **Authentication Scheme:** Stateless Bearer JWT issued via `oan_a2c.api.auth.login` (or token key pairs).
- **Header:** `Authorization: Bearer <jwt_token>`
- **User Role:** `A2C Farmer` (desk_access = `0`, Website User).

### 1.2 Multi-Tenancy & Data Scoping

- **Catalog Visibility:** Unlike bank staff who are scoped to their single institution, farmers can discover `Active` loan products across **all participating banks**. Inactive/Archived products are filtered out by `loan_product_scope_query`.
- **Application Scoping:** Scoped by farmer ownership via `loan_application_scope_query`. A farmer sees only loan applications bound to their `A2C Farmer Profile` (including applications created directly by the farmer or submitted on their behalf by a Development Agent).
- **Two-Layer Pipeline Stage Resolution:** Internal workflow archetypes (`In Transition`, `Completed`, `Rejected`, `Active`) are resolved into bank-configured display stages (`Submitted`, `Underwriting`, `Approved`, `Disbursed`, etc.). The internal `"In Transition"` state is never exposed in API responses.

---

## 2. Response Envelope & Error Handling

### 2.1 Standard Success Envelope (HTTP 200)

```json
{
  "status": "success",
  "message": "Human-readable message",
  "data": null | {} | [],
  "pagination": null | {
    "page": 1,
    "limit": 20,
    "total": 150,
    "total_pages": 8,
    "has_next": true
  },
  "request_id": "uuid-string"
}
```

### 2.2 Error Envelope & Standard Codes

```json
{
  "status": "error",
  "message": "Human-readable error description",
  "code": "MACHINE_READABLE_CODE",
  "details": {},
  "request_id": "uuid-string"
}
```

| HTTP Status | Error Code             | Description                                                                                      |
| :---------- | :--------------------- | :----------------------------------------------------------------------------------------------- |
| **400**     | `VALIDATION_ERROR`     | Schema validation error, negative amount, unknown status filter, or invalid workflow transition. |
| **401**     | `AUTHENTICATION_ERROR` | Missing or invalid JWT Bearer token, or session expired.                                         |
| **403**     | `PERMISSION_DENIED`    | Caller lacks `A2C Farmer` role or attempts to access another farmer's profile/application.       |
| **404**     | `NOT_FOUND`            | Requested loan product, application, or bank does not exist.                                     |
| **500**     | `INTERNAL_ERROR`       | Server exception or database transaction failure.                                                |

---

## 3. Endpoint Reference: Product Discovery & Catalog (`api/v1/farmer/catalog.py`)

### 3.1 `GET /api/method/oan_a2c.api.v1.farmer.catalog.list_catalog`

Retrieves a paginated list of active loan products across all banks, with multi-facet filtering and deterministic sorting.

**Authentication & Permissions:** Requires JWT Bearer token.
**Parameters (Query):**
| Param | Type | Required | Default | Notes |
| :--- | :--- | :--- | :--- | :--- |
| `bank` | string | No | null | Filter by specific Participating Bank document ID |
| `region` | string | No | null | Filter products from banks registered in this region |
| `category` | string | No | null | Filter by term category ID (e.g. `crop-input-loans`) |
| `tag` | string | No | null | Filter by term tag ID (e.g. `no-collateral`) |
| `is_saved` | boolean | No | null | If true, returns only products bookmarked by the caller |
| `min_amount` | float | No | null | Filter where product `max_amount >= min_amount` (range overlap) |
| `max_amount` | float | No | null | Filter where product `min_amount <= max_amount` (range overlap) |
| `max_interest_rate` | float | No | null | Filter where `min_interest_rate <= max_interest_rate` |
| `min_tenure_months` | int | No | null | Minimum loan tenure in months |
| `max_tenure_months` | int | No | null | Maximum loan tenure in months |
| `search` | string | No | null | Case-insensitive substring match against `product_name` |
| `sort_by` | string | No | `product_name` | One of: `product_name`, `interest_low_high`, `interest_high_low`, `amount_low_high`, `amount_high_low`, `tenure_low_high`, `newest` |
| `start` | int | No | 0 | Pagination offset (>= 0) |
| `limit` | int | No | 20 | Items per page (1–100) |

**Success Response (HTTP 200):**

```json
{
  "status": "success",
  "message": "Catalog retrieved successfully",
  "data": {
    "products": [
      {
        "name": "PROD-PB-0001-0001",
        "product_name": "Smallholder Agricultural Loan",
        "slug": "smallholder-ag-loan",
        "status": "Active",
        "bank": "PB-0001",
        "bank_name": "Cooperative Bank of Oromia",
        "bank_logo": "/files/cbo-logo.png",
        "image_url": "/files/product-banner.png",
        "min_interest_rate": 8.5,
        "max_interest_rate": 12.0,
        "min_amount": 5000.0,
        "max_amount": 100000.0,
        "tenure_months": 12
      }
    ]
  },
  "pagination": {
    "page": 1,
    "limit": 20,
    "total": 45,
    "total_pages": 3,
    "has_next": true
  }
}
```

> [!NOTE]
> When called by a **Bank User / Bank Admin**, each product item also includes `"applications_count": <number>` representing the count of their bank's applications for that loan product.

---

### 3.2 `GET /api/method/oan_a2c.api.v1.farmer.catalog.get_catalog_facets`

Returns global facet aggregations for rendering sidebar filters in the discovery UI.

**Authentication & Permissions:** Requires JWT Bearer token.
**Parameters:** None.
**Success Response (HTTP 200):**

```json
{
  "status": "success",
  "message": "Catalog facets retrieved successfully",
  "data": {
    "categories": [
      { "id": "crop-input-loans", "name": "Crop Input Loans" },
      { "id": "equipment-financing", "name": "Equipment Financing" }
    ],
    "tags": [
      { "id": "no-collateral", "name": "No Collateral" },
      { "id": "fast-disbursal", "name": "Fast Disbursal" }
    ],
    "regions": ["Amhara", "Oromia", "Sidama", "Tigray"],
    "banks": [
      {
        "name": "PB-0001",
        "bank_name": "Cooperative Bank of Oromia",
        "logo": "/files/cbo-logo.png",
        "registered_region": "Oromia"
      }
    ],
    "tenures": [6, 12, 24, 36],
    "tenure_range": { "min": 1, "max": 1200 },
    "amount_range": { "min": 0.0, "max": 100000000.0 },
    "max_interest_rate": 100.0
  }
}
```

---

### 3.3 `GET /api/method/oan_a2c.api.v1.farmer.catalog.get_bank_details`

Returns public storefront details for a specific active participating bank.

**Authentication & Permissions:** Requires JWT Bearer token.
**Parameters (Query):**
| Param | Type | Required | Default | Notes |
| :--- | :--- | :--- | :--- | :--- |
| **`bank`** | string | Yes | — | Participating Bank document ID (e.g. `PB-0001`) |

**Success Response (HTTP 200):**

```json
{
  "status": "success",
  "message": "Bank details retrieved successfully",
  "data": {
    "bank": "PB-0001",
    "bank_name": "Cooperative Bank of Oromia",
    "bank_code": "CBOETAA",
    "brand_name": "Coopbank",
    "entity_type": "Commercial Bank",
    "website": "https://coopbankoromia.com.et",
    "logo_url": "/files/cbo-logo.png",
    "registered_region": "Oromia",
    "registered_country": "Ethiopia"
  }
}
```

---

### 3.4 `POST /api/method/oan_a2c.api.v1.farmer.catalog.save_product`

Bookmarks a loan product for the authenticated user (idempotent).

**Authentication & Permissions:** Requires JWT Bearer token.
**Parameters (JSON Body):**
| Param | Type | Required | Default | Notes |
| :--- | :--- | :--- | :--- | :--- |
| **`loan_product`** | string | Yes | — | Loan Product document ID (e.g. `PROD-PB-0001-0001`) |

**Success Response (HTTP 200):**

```json
{
  "status": "success",
  "message": "Product saved successfully",
  "data": null
}
```

---

### 3.5 `POST /api/method/oan_a2c.api.v1.farmer.catalog.unsave_product`

Removes a bookmarked loan product for the authenticated user.

**Authentication & Permissions:** Requires JWT Bearer token.
**Parameters (JSON Body):**
| Param | Type | Required | Default | Notes |
| :--- | :--- | :--- | :--- | :--- |
| **`loan_product`** | string | Yes | — | Loan Product document ID to remove |

**Success Response (HTTP 200):**

```json
{
  "status": "success",
  "message": "Product removed from saved list",
  "data": null
}
```

---

### 3.6 `GET /api/method/oan_a2c.api.v1.farmer.catalog.get_saved_products`

Retrieves paginated loan products saved/bookmarked by the caller.

**Authentication & Permissions:** Requires JWT Bearer token.
**Parameters (Query):**
| Param | Type | Required | Default | Notes |
| :--- | :--- | :--- | :--- | :--- |
| `start` | int | No | 0 | Pagination offset |
| `limit` | int | No | 20 | Items per page (1–100) |

**Success Response (HTTP 200):**

```json
{
  "status": "success",
  "message": "Saved products retrieved successfully",
  "data": {
    "products": [
      {
        "name": "PROD-PB-0001-0001",
        "product_name": "Smallholder Agricultural Loan",
        "slug": "smallholder-ag-loan",
        "bank": "PB-0001",
        "bank_name": "Cooperative Bank of Oromia",
        "bank_logo": "/files/cbo-logo.png",
        "image_url": "/files/product-banner.png",
        "min_interest_rate": 8.5,
        "max_interest_rate": 12.0,
        "min_amount": 5000.0,
        "max_amount": 100000.0,
        "tenure_months": 12
      }
    ]
  },
  "pagination": {
    "page": 1,
    "limit": 20,
    "total": 1,
    "total_pages": 1,
    "has_next": false
  }
}
```

---

## 4. Endpoint Reference: Applications (`api/v1/farmer/applications.py`)

### 4.1 `GET /api/method/oan_a2c.api.v1.farmer.applications.list_applications`

Lists loan applications belonging to the authenticated farmer across all participating banks, enriched with stage payloads.

**Authentication & Permissions:** Requires JWT Bearer token and role `A2C Farmer`.
**Parameters (Query):**
| Param | Type | Required | Default | Notes |
| :--- | :--- | :--- | :--- | :--- |
| `status` | string | No | null | Filter by bank stage label, stage ID, external code, or `"Active"`. Single value, comma-separated, or JSON array. |
| `page` | int | No | 1 | Page number |
| `page_size` | int | No | 20 | Items per page (1–100) |

**Success Response (HTTP 200):**

```json
{
  "status": "success",
  "message": "Applications retrieved successfully",
  "data": [
    {
      "application_id": "APP-2026-0001",
      "status": "Submitted",
      "stage_id": "submitted-31011c",
      "stage_label": "Submitted",
      "sequence": 1,
      "is_terminal": false,
      "is_successful": false,
      "loan_product": "PROD-PB-0001-0001",
      "loan_product_name": "Smallholder Agricultural Loan",
      "bank": "PB-0001",
      "requested_amount": 25000.0,
      "loan_amount": 25000.0,
      "creation": "2026-08-20T10:30:00+03:00"
    }
  ],
  "pagination": {
    "page": 1,
    "limit": 20,
    "total": 1,
    "total_pages": 1,
    "has_next": false
  }
}
```

---

### 4.2 `GET /api/method/oan_a2c.api.v1.farmer.applications.get_application`

Retrieves the full application profile, farm metadata, and current pipeline stage for a single loan application.

**Authentication & Permissions:** Requires JWT Bearer token and role `A2C Farmer`. Caller must own the application.
**Parameters (Query):**
| Param | Type | Required | Default | Notes |
| :--- | :--- | :--- | :--- | :--- |
| **`application_id`** | string | Yes | — | Document ID of the loan application |

**Success Response (HTTP 200):**

```json
{
  "status": "success",
  "message": "Application retrieved successfully",
  "data": {
    "application_id": "APP-2026-0001",
    "bank": "PB-0001",
    "first_name": "Abebe",
    "last_name": "Kebede",
    "region": "Oromia",
    "woreda": "East Hararge",
    "kebele": "Gudina",
    "language": "om",
    "phone_number": "+251911000000",
    "id_type": "National ID",
    "id_number": "NID-992811",
    "farmer_id": "FAYDA-ETH-001",
    "consent_id": "CONSENT-2026-0001",
    "loan_type": "Crop Loan",
    "loan_product": "PROD-PB-0001-0001",
    "loan_product_name": "Smallholder Agricultural Loan",
    "requested_amount": 25000.0,
    "loan_amount": 25000.0,
    "loan_reason": "Seeds and fertilizers for Teff harvest",
    "status": "Submitted",
    "stage_id": "submitted-31011c",
    "sequence": 1,
    "is_terminal": false,
    "is_successful": false,
    "current_step": 1,
    "loan_officer": null,
    "creation": "2026-08-20T10:30:00+03:00",
    "date_of_birth": "1988-04-12",
    "gender": "Male",
    "marital_status": "Married",
    "size_of_family": 5,
    "number_of_children": 3,
    "no_of_females_family": 2,
    "no_of_males_family": 3,
    "source_of_income": "Farming",
    "education_level": "Secondary",
    "family_member_owns_land_independently": false,
    "total_farmland_size_as_landowner": 2.5,
    "total_farmland_size_as_crop_sharing": 0.0,
    "total_farmland_size_as_rented": 0.5,
    "farmland_size_hectares": "3.0",
    "land_ownership_status": "Owner",
    "soil_fertility_minerals": "High",
    "moisture_levels": "Optimal",
    "certification_id": "CERT-2026-01",
    "certification_photo_url": "/private/files/cert.png"
  }
}
```

---

### 4.3 `POST /api/method/oan_a2c.api.v1.farmer.applications.create_application`

Creates a new self-service draft application in `Active` status bound to the farmer's profile.

**Authentication & Permissions:** Requires JWT Bearer token and role `A2C Farmer`. Caller must have an existing bound `A2C Farmer Profile`.
**Parameters (JSON Body):**
| Param | Type | Required | Default | Notes |
| :--- | :--- | :--- | :--- | :--- |
| **`loan_product`** | string | Yes | — | Active Loan Product document ID |
| **`requested_amount`** | float | Yes | — | Desired borrowing amount (>= 1.0) |
| `loan_reason` | string | No | null | Explanation of loan purpose (max 2000 chars) |
| `consent_request` | string | No | null | Specific approved `A2C Consent Request` ID. Defaults to profile consent. |

**Success Response (HTTP 200):**

```json
{
  "status": "success",
  "message": "Application created successfully",
  "data": {
    "application_id": "APP-2026-0002"
  }
}
```

---

### 4.4 `POST /api/method/oan_a2c.api.v1.farmer.applications.update_application`

Updates amount or reason for an existing `Active` draft application.

**Authentication & Permissions:** Requires JWT Bearer token and role `A2C Farmer`.
**Parameters (JSON Body):**
| Param | Type | Required | Default | Notes |
| :--- | :--- | :--- | :--- | :--- |
| **`application_id`** | string | Yes | — | Document ID of the application |
| `requested_amount` | float | No | null | New requested loan amount (>= 1.0) |
| `loan_reason` | string | No | null | Updated loan purpose notes |

**Success Response (HTTP 200):**

```json
{
  "status": "success",
  "message": "Application updated successfully",
  "data": null
}
```

---

### 4.5 `POST /api/method/oan_a2c.api.v1.farmer.applications.submit_application`

Submits an `Active` draft application to the bank pipeline. Transitions status to the bank's initial stage (`In Transition` archetype).

**Authentication & Permissions:** Requires JWT Bearer token and role `A2C Farmer` or `A2C Development Agent`.
**Parameters (JSON Body):**
| Param | Type | Required | Default | Notes |
| :--- | :--- | :--- | :--- | :--- |
| **`application_id`** | string | Yes | — | Document ID of the application in `Active` status |

**Success Response (HTTP 200):**

```json
{
  "status": "success",
  "message": "Application submitted successfully",
  "data": null
}
```

**Error Cases:**

- **400 `VALIDATION_ERROR`**: If application is not in `Active` status (`Only Active applications can be submitted.`).
- **400 `VALIDATION_ERROR`**: If linked consent is missing or not approved (`Consent is required before an application can be submitted.`).

---

## 5. Endpoint Reference: Dashboard (`api/v1/farmer/dashboard.py`)

### 5.1 `GET /api/method/oan_a2c.api.v1.farmer.dashboard.get_dashboard_summary`

Retrieves summary profile details and the 5 most recent loan applications for the farmer's dashboard.

**Authentication & Permissions:** Requires JWT Bearer token and role `A2C Farmer`.
**Parameters:** None.
**Success Response (HTTP 200):**

```json
{
  "status": "success",
  "message": "Dashboard summary retrieved successfully",
  "data": {
    "farmer_profile": {
      "first_name": "Abebe",
      "last_name": "Kebede",
      "farmer_id": "FAYDA-ETH-001",
      "region": "Oromia",
      "woreda": "East Hararge",
      "kebele": "Gudina",
      "farmland_size_hectares": "3.0",
      "land_ownership_status": "Owner",
      "source_of_income": "Farming"
    },
    "recent_applications": [
      {
        "application_id": "APP-2026-0001",
        "bank": "PB-0001",
        "loan_product_name": "Smallholder Agricultural Loan",
        "requested_amount": 25000.0,
        "status": "Submitted",
        "stage_id": "submitted-31011c",
        "sequence": 1,
        "is_terminal": false,
        "is_successful": false,
        "creation": "2026-08-20T10:30:00+03:00"
      }
    ]
  }
}
```
