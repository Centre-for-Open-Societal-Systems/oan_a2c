# api-flow-seller.md — Loan Marketplace Seller API Contract

_Derived from direct source code analysis — `apps/oan_a2c/oan_a2c/api/v1/seller/` and `apps/oan_a2c/oan_a2c/api/auth.py`_

> **Source of truth:** This document reflects the complete, up-to-date backend implementation for all Loan Marketplace Seller (Bank Admin & Bank Agent) APIs.

---

## 1. Authentication & Security Architecture

### 1.1 Stateless JWT Scheme

All authenticated endpoints require a Bearer JWT token in the request header, issued via the identity gateway (`oan_a2c.api.auth.login`):

- **Header:** `Authorization: Bearer <jwt_token>` (or `Authorization: token <api_key>:<api_secret>` for system integrations).
- **Token Lifespan:** Access tokens are short-lived (**15 minutes**). Token rotation is managed via database-backed refresh tokens (**1 day** default, or **30 days** if `remember_me` was selected during login).

### 1.2 Multi-Tenancy (Bank Scope Isolation)

Tenant isolation is strictly enforced across all seller APIs via `@bank_scoped` and query interception in `hooks.py`:

- **Fail-Closed Resolution:** When a request hits a bank-scoped endpoint, `@bank_scoped` resolves the caller's associated bank from `User Permission` (`allow: "A2C Participating Bank"`). If no bank binding exists for a bank role, the request is rejected with HTTP 403 (`BANK_NOT_ONBOARDED`).
- **Query-Level Security:** Queries executed via `frappe.get_list` or `frappe.get_all` are intercepted by `permissions.bank_filters()` to automatically inject `WHERE bank = '{user_bank}'`.
- **Document-Level Security:** Direct document writes and updates (`frappe.has_permission`) verify `doc.bank == {user_bank}` before allowing modifications.
- **Unbound Platform Admins:** Platform administrators (e.g., `A2C Marketplace Admin`, `System Manager`) are unbound by bank isolation and may pass an optional `bank` parameter to inspect or manage specific tenant scopes.

---

## 2. Response Envelope & Error Handling

All endpoints use standardized decorators (`@handle_api_errors`, `@validate_request`, `@bank_scoped`) from `api/utils.py` to guarantee uniform JSON response structures.

### 2.1 Success Envelope (HTTP 200)

```json
{
  "status": "success",
  "message": "Human-readable string",
  "data": null | {} | [],
  "meta": {},
  "pagination": null | {
    "page": 1,
    "page_size": 20,
    "total_records": 150,
    "total_pages": 8
  },
  "request_id": "uuid-string"
}
```

### 2.2 Error Envelope & Standard Error Codes

When an error occurs, the API returns an appropriate HTTP status code along with a machine-readable error code:

```json
{
  "status": "error",
  "message": "Human-readable error description",
  "code": "MACHINE_READABLE_CODE",
  "details": {
    "field_name": "Specific field validation message if applicable"
  },
  "request_id": "uuid-string"
}
```

#### Reference of Standard API Error Codes:

| HTTP Status | Error Code             | Trigger Condition & Description                                                                                                                                                                                                                                                                                             |
| :---------- | :--------------------- | :-------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **400**     | `VALIDATION_ERROR`     | Request payload failed Pydantic schema validation (e.g., regex mismatch, numeric range out of bounds, invalid date/email format), database constraint check (`MandatoryError`, `UniqueValidationError`, `DuplicateEntryError`), or explicit validation logic in the handler. Check `details` object for field-level errors. |
| **401**     | `AUTHENTICATION_ERROR` | Missing or invalid JWT Bearer token, expired access/refresh token, unauthenticated caller (`Guest`) on a protected endpoint, or incorrect login credentials.                                                                                                                                                                |
| **403**     | `PERMISSION_DENIED`    | Caller is authenticated but lacks the required role (`A2C Bank Admin` vs `A2C Bank Agent`), attempts to modify a resource belonging to another bank, or an unbound admin calls an endpoint requiring an active tenant scope.                                                                                                |
| **403**     | `BANK_NOT_ONBOARDED`   | Caller possesses a bank role but their user account has no `A2C Participating Bank` binding assigned in `User Permission`, or bank registration is incomplete.                                                                                                                                                              |
| **404**     | `NOT_FOUND`            | Requested document (Loan Product, Bank, User, Term, or Refresh Token record) does not exist in the database.                                                                                                                                                                                                                |
| **500**     | `INTERNAL_ERROR`       | Unhandled server exception, system configuration error (e.g., missing encryption key), or database transaction failure.                                                                                                                                                                                                     |

---

## 3. Endpoint Reference: Dashboard (`api/v1/seller/dashboard.py`)

### 3.1 `GET /api/method/oan_a2c.api.v1.seller.dashboard.get_stats`

Retrieves aggregated statistics for the bank's loan products and applications. The response is cache-first and automatically scoped to the caller's bank.

**Authentication & Permissions:** Requires valid JWT Bearer token.
**Parameters (Query):**
| Param | Type | Required | Default | Notes |
| :--- | :--- | :--- | :--- | :--- |
| `bank` | string | No | null | Optional bank code filter. Only applicable when called by an unbound platform admin (`A2C Marketplace Admin`). Automatically overridden/ignored for Bank Admins and Agents by `@bank_scoped`. |

**Success Response (HTTP 200):**

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

_(Note: If called by an unbound platform admin without specifying `bank`, returns `{"stats": totals, "by_bank": [...]}` across all banks)._

**Error Cases:**

- **401 `AUTHENTICATION_ERROR`**: Called by unauthenticated user (`Guest`).
- **403 `PERMISSION_DENIED`**: Caller lacks read access to dashboard statistics.
- **500 `INTERNAL_ERROR`**: Cache or database query execution failure.

---

## 4. Endpoint Reference: Loan Products (`api/v1/seller/loan_products.py`)

### 4.1 `POST /api/method/oan_a2c.api.v1.seller.loan_products.create_product`

Creates new loan product(s) under the caller's bank in `Pending Approval` status. Supports both single product creation and bulk creation via a `products` array.

**Authentication & Permissions:** Requires JWT Bearer token and `create` permission on `A2C Loan Product`.
**Parameters (JSON Body):**

_Option 1: Single Product_
| Param | Type | Required | Default | Notes |
| :--- | :--- | :--- | :--- | :--- |
| **`product_name`** | string | Yes | — | Name of the loan product |
| **`min_interest_rate`** | float | Yes | — | Minimum annual interest rate percentage |
| **`max_amount`** | float | Yes | — | Maximum loan amount allowed |
| **`tenure_months`** | int | Yes | — | Duration of the loan in months |
| `max_interest_rate` | float | No | null | Maximum annual interest rate percentage |
| `min_amount` | float | No | null | Minimum loan amount allowed |
| `description` | string | No | null | Detailed product description |
| `product_meta` | list[object] | No | null | Array of key-value metadata objects: `[{"meta_key": "...", "meta_value": "..."}]` |

_Option 2: Bulk Creation_
| Param | Type | Required | Default | Notes |
| :--- | :--- | :--- | :--- | :--- |
| **`products`** | list[object] | Yes | — | Array of product objects, where each object has the same fields as the Single Product option (Max 10 products per request) |

**Success Response (HTTP 200) - Single Product:**

```json
{
  "status": "success",
  "message": "Products created",
  "data": {
    "message": "Products created",
    "product_ids": ["PROD-2026-0001"]
  }
}
```

**Success Response (HTTP 200) - Bulk Creation:**

```json
{
  "status": "success",
  "message": "Products created",
  "data": {
    "message": "Products created",
    "product_ids": ["PROD-2026-0001", "PROD-2026-0002"]
  }
}
```

**Error Cases:**

- **400 `VALIDATION_ERROR`**: Missing required fields, negative values, or data type mismatch in request body.
- **401 `AUTHENTICATION_ERROR`**: Called by unauthenticated user.
- **403 `BANK_NOT_ONBOARDED`**: Caller has no bank binding assigned in `User Permission`.
- **403 `PERMISSION_DENIED`**: Caller lacks `create` permission on `A2C Loan Product`.
- **500 `INTERNAL_ERROR`**: Database insertion error.

---

### 4.2 `POST /api/method/oan_a2c.api.v1.seller.loan_products.update_product`

Updates fields and metadata of an existing loan product.

**Authentication & Permissions:** Requires JWT Bearer token and `write` permission on the specified `A2C Loan Product`.
**Parameters (JSON Body):**
| Param | Type | Required | Default | Notes |
| :--- | :--- | :--- | :--- | :--- |
| **`product_id`** | string | Yes | — | The document name/ID of the loan product |
| `product_name` | string | No | null | New product name |
| `min_interest_rate` | float | No | null | New minimum annual interest rate |
| `max_interest_rate` | float | No | null | New maximum annual interest rate |
| `min_amount` | float | No | null | New minimum loan amount |
| `max_amount` | float | No | null | New maximum loan amount |
| `tenure_months` | int | No | null | New loan tenure in months |
| `description` | string | No | null | New product description |
| `product_meta` | list[object] | No | null | If provided, completely replaces existing metadata with the new array of `[{"meta_key": "...", "meta_value": "..."}]` |

**Success Response (HTTP 200):**

```json
{
  "status": "success",
  "message": "Product updated",
  "data": {
    "message": "Product updated",
    "product_id": "PROD-2026-0001",
    "status": "Pending Approval"
  }
}
```

**Error Cases:**

- **400 `VALIDATION_ERROR`**: Missing `product_id` or invalid field types.
- **401 `AUTHENTICATION_ERROR`**: Called by unauthenticated user.
- **403 `PERMISSION_DENIED`**: Caller lacks `write` access (e.g., product belongs to another bank).
- **404 `NOT_FOUND`**: Specified `product_id` does not exist.
- **500 `INTERNAL_ERROR`**: Database save failure.

---

### 4.3 `POST /api/method/oan_a2c.api.v1.seller.loan_products.set_product_status`

Transitions the lifecycle status of a loan product.

**Authentication & Permissions:** Requires JWT Bearer token and `write` permission on the specified `A2C Loan Product`.
**Parameters (JSON Body):**
| Param | Type | Required | Default | Notes |
| :--- | :--- | :--- | :--- | :--- |
| **`product_id`** | string | Yes | — | The document name/ID of the loan product |
| **`status`** | string | Yes | — | Exactly one of: `Pending Approval`, `Active`, `Rejected` (enforced via regex `^(Pending Approval|Active|Rejected)$`) |
| **`reason`** | string | Yes (for Active/Rejected) | null | Mandatory reason/notes when setting status to `Active` or `Rejected`. Optional for `Pending Approval`. Logged to `A2C Loan Product Audit Event`. |

**Success Response (HTTP 200):**

```json
{
  "status": "success",
  "message": "Product status updated to Active",
  "data": {
    "message": "Product status updated to Active",
    "product_id": "PROD-2026-0001",
    "status": "Active"
  }
}
```

**Error Cases:**

- **400 `VALIDATION_ERROR`**: `status` is not exactly `Pending Approval`, `Active`, or `Rejected`, or missing `product_id`.
- **401 `AUTHENTICATION_ERROR`**: Called by unauthenticated user.
- **403 `PERMISSION_DENIED`**: Caller lacks `write` access on the product.
- **404 `NOT_FOUND`**: Specified `product_id` does not exist.
- **500 `INTERNAL_ERROR`**: Database save failure.

---

### 4.4 `GET /api/method/oan_a2c.api.v1.seller.loan_products.list_products`

Retrieves a paginated list of loan products scoped to the caller's bank, with optional catalog filtering.

**Authentication & Permissions:** Requires JWT Bearer token. Automatically scoped to caller's bank via `bank_filters()`.
**Parameters (Query):**
| Param | Type | Required | Default | Notes |
| :--- | :--- | :--- | :--- | :--- |
| `status` | string | No | null | Filter by status (e.g., `Active`, `Pending Approval`, `Rejected`) |
| `search` | string | No | null | Substring match against `product_name` |
| `category` | string | No | null | Filter products associated with a matching term category |
| `tag` | string | No | null | Filter products associated with a matching term tag |
| `min_interest_rate` | float | No | null | Filter products where `min_interest_rate >= value` |
| `max_interest_rate` | float | No | null | Filter products where `max_interest_rate <= value` |
| `min_amount` | float | No | null | Filter products where `min_amount >= value` |
| `max_amount` | float | No | null | Filter products where `max_amount <= value` |
| `tenure_months` | int | No | null | Filter by exact tenure duration in months |
| `limit` | int | No | 20 | Pagination limit |
| `start` | int | No | 0 | Pagination offset |

**Success Response (HTTP 200):**

```json
{
  "status": "success",
  "message": "Success",
  "data": {
    "products": [
      {
        "name": "PROD-2026-0001",
        "product_name": "Smallholder Agricultural Loan",
        "slug": "smallholder-ag-loan",
        "status": "Active",
        "min_interest_rate": 10.5,
        "max_interest_rate": 15.0,
        "min_amount": 5000.0,
        "max_amount": 50000.0,
        "tenure_months": 12,
        "creation": "2026-07-21 10:00:00"
      }
    ],
    "count": 1
  }
}
```

**Error Cases:**

- **400 `VALIDATION_ERROR`**: Non-numeric string passed for numeric parameters (`min_amount`, `tenure_months`, etc.).
- **401 `AUTHENTICATION_ERROR`**: Called by unauthenticated user.
- **500 `INTERNAL_ERROR`**: Database query failure.

---

### 4.5 `GET /api/method/oan_a2c.api.v1.seller.loan_products.get_product`

Retrieves full details for a specific loan product, including all metadata key-value pairs, assigned categories, tags, and attribute lookups.

**Authentication & Permissions:** Requires JWT Bearer token and `read` permission on the specified `A2C Loan Product`.
**Parameters (Query or JSON Body):**
| Param | Type | Required | Default | Notes |
| :--- | :--- | :--- | :--- | :--- |
| **`product_id`** | string | Yes | — | The document name/ID of the loan product |

**Success Response (HTTP 200):**

```json
{
  "status": "success",
  "message": "Success",
  "data": {
    "product": {
      "name": "PROD-2026-0001",
      "product_name": "Smallholder Agricultural Loan",
      "slug": "smallholder-ag-loan",
      "status": "Active",
      "min_interest_rate": 10.5,
      "max_interest_rate": 15.0,
      "min_amount": 5000.0,
      "max_amount": 50000.0,
      "tenure_months": 12,
      "description": "Flexible financing for seasonal farming inputs.",
      "bank": "A2C-BANK-0001",
      "creation": "2026-07-21 10:00:00",
      "modified": "2026-07-27 10:30:00",
      "product_meta": [
        {
          "meta_key": "brochure_pdf",
          "meta_value": "/public/files/agri-loan-brochure.pdf"
        }
      ],
      "categories": ["crop-input-loans"],
      "tags": ["no-collateral"],
      "attributes": {
        "Crop Type": ["maize", "teff"],
        "Region": ["oromia"]
      }
    }
  }
}
```

**Error Cases:**

- **400 `VALIDATION_ERROR`**: Missing `product_id`.
- **401 `AUTHENTICATION_ERROR`**: Called by unauthenticated user.
- **403 `PERMISSION_DENIED`**: Caller lacks `read` access on `product_id`.
- **404 `NOT_FOUND`**: Specified `product_id` does not exist.
- **500 `INTERNAL_ERROR`**: Database lookup failure.

---

## 5. Endpoint Reference: Taxonomy & Attributes (`api/v1/seller/taxonomy.py`)

### 5.1 `GET /api/method/oan_a2c.api.v1.seller.taxonomy.get_categories`

Retrieves all available term categories in the marketplace taxonomy.

**Authentication & Permissions:** Requires JWT Bearer token.
**Parameters:** None.
**Success Response (HTTP 200):**

```json
{
  "status": "success",
  "message": "Success",
  "data": {
    "categories": [
      {
        "term_id": "crop-input-loans",
        "parent_category": null,
        "term_name": "Crop Input Loans"
      }
    ]
  }
}
```

**Error Cases:**

- **401 `AUTHENTICATION_ERROR`**: Called by unauthenticated user.
- **500 `INTERNAL_ERROR`**: Database query failure.

---

### 5.2 `GET /api/method/oan_a2c.api.v1.seller.taxonomy.get_tags`

Retrieves all available term tags in the marketplace taxonomy.

**Authentication & Permissions:** Requires JWT Bearer token.
**Parameters:** None.
**Success Response (HTTP 200):**

```json
{
  "status": "success",
  "message": "Success",
  "data": {
    "tags": [
      {
        "term_id": "no-collateral",
        "term_name": "No Collateral"
      }
    ]
  }
}
```

**Error Cases:**

- **401 `AUTHENTICATION_ERROR`**: Called by unauthenticated user.
- **500 `INTERNAL_ERROR`**: Database query failure.

---

### 5.3 `GET /api/method/oan_a2c.api.v1.seller.taxonomy.get_attributes`

Retrieves all available terms in the marketplace system for use as product attributes.

**Authentication & Permissions:** Requires JWT Bearer token.
**Parameters:** None.
**Success Response (HTTP 200):**

```json
{
  "status": "success",
  "message": "Success",
  "data": {
    "attributes": [
      {
        "term_id": "maize",
        "term_name": "Maize",
        "slug": "maize"
      }
    ]
  }
}
```

**Error Cases:**

- **401 `AUTHENTICATION_ERROR`**: Called by unauthenticated user.
- **500 `INTERNAL_ERROR`**: Database query failure.

---

### 5.4 `POST /api/method/oan_a2c.api.v1.seller.taxonomy.set_product_categories`

Assigns a list of term categories to a loan product, replacing existing category relationships.

**Authentication & Permissions:** Requires JWT Bearer token and `write` permission on the specified product.
**Parameters (JSON Body):**
| Param | Type | Required | Default | Notes |
| :--- | :--- | :--- | :--- | :--- |
| **`product_id`** | string | Yes | — | The document name/ID of the loan product |
| **`term_ids`** | list[string] | Yes | — | Array of category term IDs (e.g., `["crop-input-loans"]`) |

**Success Response (HTTP 200):**

```json
{
  "status": "success",
  "message": "Categories updated",
  "data": {
    "message": "Categories updated"
  }
}
```

**Error Cases:**

- **400 `VALIDATION_ERROR`**: If any term ID in `term_ids` does not exist in `A2C Term Category` (`Category '<id>' does not exist.`).
- **401 `AUTHENTICATION_ERROR`**: Called by unauthenticated user.
- **403 `PERMISSION_DENIED`**: Caller lacks `write` access on `product_id`.
- **404 `NOT_FOUND`**: Specified `product_id` does not exist.
- **500 `INTERNAL_ERROR`**: Database transaction failure.

---

### 5.5 `POST /api/method/oan_a2c.api.v1.seller.taxonomy.set_product_tags`

Assigns a list of term tags to a loan product, replacing existing tag relationships.

**Authentication & Permissions:** Requires JWT Bearer token and `write` permission on the specified product.
**Parameters (JSON Body):**
| Param | Type | Required | Default | Notes |
| :--- | :--- | :--- | :--- | :--- |
| **`product_id`** | string | Yes | — | The document name/ID of the loan product |
| **`term_ids`** | list[string] | Yes | — | Array of tag term IDs (e.g., `["no-collateral", "fast-disbursal"]`) |

**Success Response (HTTP 200):**

```json
{
  "status": "success",
  "message": "Tags updated",
  "data": {
    "message": "Tags updated"
  }
}
```

**Error Cases:**

- **400 `VALIDATION_ERROR`**: If any term ID in `term_ids` does not exist in `A2C Term Tag` (`Tag '<id>' does not exist.`).
- **401 `AUTHENTICATION_ERROR`**: Called by unauthenticated user.
- **403 `PERMISSION_DENIED`**: Caller lacks `write` access on `product_id`.
- **404 `NOT_FOUND`**: Specified `product_id` does not exist.
- **500 `INTERNAL_ERROR`**: Database transaction failure.

---

### 5.6 `POST /api/method/oan_a2c.api.v1.seller.taxonomy.set_product_attributes`

Sets dynamic eligibility attribute lookups for a loan product across various taxonomies (e.g., eligible crops, regions, loan types).

**Authentication & Permissions:** Requires JWT Bearer token and `write` permission on the specified product.
**Parameters (JSON Body):**
| Param | Type | Required | Default | Notes |
| :--- | :--- | :--- | :--- | :--- |
| **`product_id`** | string | Yes | — | The document name/ID of the loan product |
| **`attributes`** | dict[string, list[string]] | Yes | — | Dictionary mapping taxonomy names to arrays of accepted term IDs (e.g., `{"Crop Type": ["maize", "teff"], "Region": ["oromia"]}`) |

**Success Response (HTTP 200):**

```json
{
  "status": "success",
  "message": "Attributes updated",
  "data": {
    "message": "Attributes updated"
  }
}
```

**Error Cases:**

- **400 `VALIDATION_ERROR`**: If any term ID in `attributes` does not exist in `A2C Term` (`Term '<id>' does not exist.`).
- **401 `AUTHENTICATION_ERROR`**: Called by unauthenticated user.
- **403 `PERMISSION_DENIED`**: Caller lacks `write` access on `product_id`.
- **404 `NOT_FOUND`**: Specified `product_id` does not exist.
- **500 `INTERNAL_ERROR`**: Database transaction failure.

---

### 5.7 `POST /api/method/oan_a2c.api.v1.seller.taxonomy.create_category`

Creates a new term category in the system taxonomy.

**Authentication & Permissions:** Requires JWT Bearer token and `create` permission on `A2C Term Category`.
**Parameters (JSON Body):**
| Param | Type | Required | Default | Notes |
| :--- | :--- | :--- | :--- | :--- |
| **`term_name`** | string | Yes | — | Human-readable name of the category (e.g., `Equipment Financing`) |
| `description` | string | No | null | Detailed description of the category |
| `parent_category` | string | No | null | Parent category term ID for nesting hierarchy |

**Success Response (HTTP 200):**

```json
{
  "status": "success",
  "message": "Category created",
  "data": {
    "message": "Category created",
    "term_id": "equipment-financing"
  }
}
```

**Error Cases:**

- **400 `VALIDATION_ERROR`**: Missing `term_name`, or category already exists (`Category '<name>' already exists.`).
- **401 `AUTHENTICATION_ERROR`**: Called by unauthenticated user.
- **403 `PERMISSION_DENIED`**: Caller lacks `create` permission on `A2C Term` or `A2C Term Category`.
- **500 `INTERNAL_ERROR`**: Database insertion failure.

---

### 5.8 `POST /api/method/oan_a2c.api.v1.seller.taxonomy.create_tag`

Creates a new term tag in the system taxonomy.

**Authentication & Permissions:** Requires JWT Bearer token and `create` permission on `A2C Term Tag`.
**Parameters (JSON Body):**
| Param | Type | Required | Default | Notes |
| :--- | :--- | :--- | :--- | :--- |
| **`term_name`** | string | Yes | — | Human-readable name of the tag (e.g., `Low Interest`) |
| `description` | string | No | null | Detailed description of the tag |

**Success Response (HTTP 200):**

```json
{
  "status": "success",
  "message": "Tag created",
  "data": {
    "message": "Tag created",
    "term_id": "low-interest"
  }
}
```

**Error Cases:**

- **400 `VALIDATION_ERROR`**: Missing `term_name`, or tag already exists (`Tag '<name>' already exists.`).
- **401 `AUTHENTICATION_ERROR`**: Called by unauthenticated user.
- **403 `PERMISSION_DENIED`**: Caller lacks `create` permission on `A2C Term` or `A2C Term Tag`.
- **500 `INTERNAL_ERROR`**: Database insertion failure.

---

### 5.9 `POST /api/method/oan_a2c.api.v1.seller.taxonomy.create_attribute_term`

Creates or ensures the existence of a general `A2C Term` for use in product attributes.

**Authentication & Permissions:** Requires JWT Bearer token and `create` permission on `A2C Term`.
**Parameters (JSON Body):**
| Param | Type | Required | Default | Notes |
| :--- | :--- | :--- | :--- | :--- |
| **`term_name`** | string | Yes | — | Human-readable name of the attribute term (e.g., `Sesame`) |

**Success Response (HTTP 200):**

```json
{
  "status": "success",
  "message": "Attribute term ready",
  "data": {
    "message": "Attribute term ready",
    "term_id": "sesame"
  }
}
```

**Error Cases:**

- **400 `VALIDATION_ERROR`**: Missing `term_name`.
- **401 `AUTHENTICATION_ERROR`**: Called by unauthenticated user.
- **403 `PERMISSION_DENIED`**: Caller lacks `create` permission on `A2C Term`.
- **500 `INTERNAL_ERROR`**: Database insertion failure.

---

## 6. Endpoint Reference: Onboarding & Registration (`api/v1/seller/onboarding.py`)

### 6.1 `POST /api/method/oan_a2c.api.v1.seller.onboarding.register_bank`

Registers a new participating bank entity and binds the authenticated caller as its default admin user in `User Permission`.

**Authentication & Permissions:** Requires JWT Bearer token. Caller must NOT already be associated with an organization.
**Parameters (JSON Body):**
| Param | Type | Required | Default | Notes |
| :--- | :--- | :--- | :--- | :--- |
| **`bank_name`** | string | Yes | — | Min length 2 characters |
| **`bank_code`** | string | Yes | — | Min length 2 characters. Non-alphanumeric characters are stripped, converted to uppercase (used as TIN) |
| **`entity_type`** | string | Yes | — | Legal entity type of the bank |
| **`registered_street`** | string | Yes | — | Min length 2 characters |
| `registered_kebele_village` | string | No | null | Kebele or village |
| `registered_woreda_district` | string | No | null | Woreda or district |
| `registered_zone` | string | No | null | Zone |
| **`registered_region`** | string | Yes | — | Min length 2 characters |
| **`registered_country`** | string | Yes | — | Min length 2 characters |
| **`registered_postal_code`** | string | Yes | — | Min length 2 characters |
| **`registered_email`** | string | Yes | — | Valid email address format |
| **`registered_phone`** | string | Yes | — | Min length 2 characters |
| `website` | string | No | null | Website URL |

**Success Response (HTTP 200):**

```json
{
  "status": "success",
  "message": "Bank registered successfully. Currently onboarding.",
  "data": {
    "message": "Bank registered successfully. Currently onboarding.",
    "bank_code": "BNK001",
    "bank_id": "A2C-BANK-0001"
  }
}
```

_(Note: If `bank_code` already exists in `A2C Participating Bank`, creates an admin review `ToDo` item and returns: `{"message": "Your registration attempt has been flagged for admin review."}`)_

**Error Cases:**

- **400 `VALIDATION_ERROR`**: Field length violation, invalid email format, or caller already associated with a bank (`User is already associated with an organization.`).
- **401 `AUTHENTICATION_ERROR`**: Called by unauthenticated user (`Guest`).
- **500 `INTERNAL_ERROR`**: Database rollback during bank or permission creation (`Failed to register bank: ...`).

---

### 6.3 `POST /api/method/oan_a2c.api.v1.seller.onboarding.save_org_contacts`

Saves Grievance Redressal Officer (GRO) and Operations (OPS) contact details for the caller's bank.

**Authentication & Permissions:** Requires JWT Bearer token. Caller must have an assigned bank binding.
**Parameters (JSON Body):**
| Param | Type | Required | Default | Notes |
| :--- | :--- | :--- | :--- | :--- |
| **`gro_name`** | string | Yes | — | Name of Grievance Redressal Officer |
| **`gro_mobile`** | string | Yes | — | Mobile phone number of GRO |
| **`ops_name`** | string | Yes | — | Name of Operations Contact |
| **`ops_mobile`** | string | Yes | — | Mobile phone number of Operations Contact |

**Success Response (HTTP 200):**

```json
{
  "status": "success",
  "message": "Contacts saved successfully.",
  "data": {
    "message": "Contacts saved successfully."
  }
}
```

**Error Cases:**

- **400 `VALIDATION_ERROR`**: Missing required parameters.
- **400 `VALIDATION_ERROR` / `BANK_NOT_ONBOARDED`**: Caller has no bank binding in `User Permission` (`No bank associated with the current user.`).
- **401 `AUTHENTICATION_ERROR`**: Called by unauthenticated user.
- **500 `INTERNAL_ERROR`**: Database save failure.

---

### 6.4 `POST /api/method/oan_a2c.api.v1.seller.onboarding.upload_kyc_document`

Uploads a Base64-encoded PDF KYC document for the caller's bank.

**Authentication & Permissions:** Requires JWT Bearer token. Caller must have an assigned bank binding.
**Parameters (JSON Body):**
| Param | Type | Required | Default | Notes |
| :--- | :--- | :--- | :--- | :--- |
| **`filename`** | string | Yes | — | Min 4, max 30 chars. Must match regex pattern `^.+\.pdf$` |
| **`filedata`** | string | Yes | — | Base64-encoded PDF string. Min 10, max 15,000,000 chars (~15MB limit) |

**Success Response (HTTP 200):**

```json
{
  "status": "success",
  "message": "KYC document uploaded successfully.",
  "data": {
    "message": "KYC document uploaded successfully.",
    "file_url": "/private/files/kyc.pdf"
  }
}
```

**Error Cases:**

- **400 `VALIDATION_ERROR`**: Filename does not end in `.pdf` or string length out of bounds.
- **400 `VALIDATION_ERROR`**: Base64 decoding fails (`Invalid file: content is not valid Base64.`).
- **400 `VALIDATION_ERROR`**: Decoded binary content lacks PDF magic bytes (`Invalid file: only PDF documents are accepted.`).
- **400 `VALIDATION_ERROR` / `BANK_NOT_ONBOARDED`**: Caller has no bank binding (`No bank associated with the current user.`).
- **401 `AUTHENTICATION_ERROR`**: Called by unauthenticated user.
- **500 `INTERNAL_ERROR`**: File creation or database save failure (`Failed to save uploaded file.`).

---

### 6.5 `GET /api/method/oan_a2c.api.v1.seller.onboarding.get_bank_profile`

Retrieves the full seller organization profile for the caller's mapped bank, including onboarding completion indicators.

**Authentication & Permissions:** Requires JWT **\*\*** Caller must have an assigned bank binding.
**Parameters:** None.
**Success Response (HTTP 200):**

```json
{
  "status": "success",
  "message": "Success",
  "data": {
    "bank_id": "A2C-BANK-0001",
    "bank_code": "BNK001",
    "bank_name": "Example Bank",
    "entity_type": "Bank",
    "registered_street": "Bole Road",
    "registered_kebele_village": "Kebele 01",
    "registered_woreda_district": "Bole",
    "registered_zone": "Bole Zone",
    "registered_region": "Addis Ababa",
    "registered_country": "Ethiopia",
    "registered_postal_code": "1000",
    "registered_email": "ops@examplebank.com",
    "registered_phone": "+251900000000",
    "website": "https://examplebank.com",
    "status": "Onboarding",
    "gro_name": "GRO Contact",
    "gro_mobile": "+251911111111",
    "ops_name": "OPS Contact",
    "ops_mobile": "+251922222222",
    "kyc_document": "/private/files/kyc.pdf",
    "kyc_document_uploaded": true,
    "org_grievance_updated": true
  }
}
```

**Derived Field Logic:**

- `kyc_document_uploaded` = `true` when `kyc_document` is present, else `false`.
- `org_grievance_updated` = `true` only when both `gro_name` and `gro_mobile` are present, else `false`.

**Error Cases:**

- **400 `VALIDATION_ERROR`**: Caller has no bank binding (`No bank associated with the current user.`).
- **401 `AUTHENTICATION_ERROR`**: Called by unauthenticated user.
- **403 `PERMISSION_DENIED`**: Caller lacks permission to read the mapped bank.
- **404 `NOT_FOUND`**: Mapped bank document does not exist.
- **500 `INTERNAL_ERROR`**: Unexpected server/database failure.

---

### 6.5b `POST /api/method/oan_a2c.api.v1.seller.onboarding.update_bank_profile`

Updates the organization details and branding profile of the caller's bank.

**Authentication & Permissions:** Requires JWT Bearer token. Caller must have an assigned bank binding and possess the `A2C Bank Admin` role.
**Parameters (JSON Body):**
| Param | Type | Required | Default | Notes |
| :--- | :--- | :--- | :--- | :--- |
| `bank_name` | string | No | null | Min length 2 characters (Legal Name) |
| `brand_name` | string | No | null | Display Name |
| `website` | string | No | null | Website URL |
| `registered_street` | string | No | null | Min length 2 characters |
| `registered_kebele_village` | string | No | null | |
| `registered_woreda_district` | string | No | null | |
| `registered_zone` | string | No | null | |
| `registered_region` | string | No | null | Min length 2 characters |
| `registered_country` | string | No | null | Min length 2 characters |
| `registered_postal_code` | string | No | null | Min length 2 characters |
| `registered_email` | string | No | null | Valid email address format |
| `registered_phone` | string | No | null | Min length 2 characters |
| `logo` | string | No | null | File URL from image upload API |

**Success Response (HTTP 200):**

```json
{
  "status": "success",
  "message": "Organization profile updated successfully.",
  "data": {
    "message": "Organization profile updated successfully."
  }
}
```

**Error Cases:**

- **400 `VALIDATION_ERROR`**: Invalid email, or field length out of bounds.
- **401 `AUTHENTICATION_ERROR`**: Called by unauthenticated user.
- **403 `PERMISSION_DENIED`**: Target user is not an `A2C Bank Admin` or caller lacks bank binding.
- **500 `INTERNAL_ERROR`**: Database save failure.

---

### 6.7 `POST /api/method/oan_a2c.api.v1.seller.onboarding.update_bank_status`

Updates the onboarding status of a specified bank. Restricted to Bank Admins.

**Authentication & Permissions:** Requires JWT Bearer token. Caller must possess the `A2C Bank Admin` role.
**Parameters (JSON Body):**
| Param | Type | Required | Default | Notes |
| :--- | :--- | :--- | :--- | :--- |
| **`bank_code`** | string | Yes | — | The TIN / bank code. Min length 2 characters |
| **`new_status`** | string | Yes | — | Exactly one of: `Onboarding`, `Active`, `Suspended` (enforced via regex `^(Onboarding|Active|Suspended)$`) |

**Success Response (HTTP 200):**

```json
{
  "status": "success",
  "message": "Bank status updated to Active",
  "data": {
    "message": "Bank status updated to Active"
  }
}
```

**Error Cases:**

- **400 `VALIDATION_ERROR`**: `new_status` does not match allowed pattern or `bank_code` is too short.
- **401 `AUTHENTICATION_ERROR`**: Called by unauthenticated user.
- **403 `PERMISSION_DENIED`**: Caller lacks the `A2C Bank Admin` role (`Only Bank Admins can update bank status.`).
- **404 `NOT_FOUND`**: Specified `bank_code` does not exist (`Bank <code> not found`).
- **500 `INTERNAL_ERROR`**: Database save failure.

---

### 6.8 `POST /api/method/oan_a2c.api.v1.seller.onboarding.invite_user`

Invites a new team member to the caller's bank. Creates the User account if it doesn't exist and binds them to the caller's bank in `User Permission`.

**Authentication & Permissions:** Requires JWT Bearer token. Caller must have an assigned bank binding.
**Parameters (JSON Body):**
| Param | Type | Required | Default | Notes |
| :--- | :--- | :--- | :--- | :--- |
| **`email`** | string | Yes | — | Valid email address format |
| **`full_name`** | string | Yes | — | Min length 2 characters |
| **`role`** | string | Yes | — | Exactly one of: `A2C Bank Admin`, `A2C Bank Agent` |
| **`password`** | string | Yes | — | Min length 6 characters. Used when creating a new user account |

**Success Response (HTTP 200):**

```json
{
  "status": "success",
  "message": "User invited successfully.",
  "data": {
    "message": "User invited successfully."
  }
}
```

_(Note: Returns "User has already joined." if user is already in this bank. Silently returns "User invited successfully." if user belongs to another bank to prevent enumeration)._

**Error Cases:**

- **400 `VALIDATION_ERROR`**: `role` not in `{A2C Bank Admin, A2C Bank Agent}` (`Invalid role.`), password fewer than 6 characters, or invalid email format.
- **400 `VALIDATION_ERROR` / `BANK_NOT_ONBOARDED`**: Caller has no bank binding (`No bank associated with the current user.`).
- **401 `AUTHENTICATION_ERROR`**: Called by unauthenticated user.
- **500 `INTERNAL_ERROR`**: Database transaction failure (`Failed to invite user: ...`).

---

### 6.10 `GET /api/method/oan_a2c.api.v1.seller.onboarding.list_users`

Lists all team members (users) associated with the caller's bank.

**Authentication & Permissions:** Requires JWT Bearer token. Caller must have an assigned bank binding.
**Parameters:** None.
**Success Response (HTTP 200):**

```json
{
  "status": "success",
  "message": "Success",
  "data": {
    "users": [
      {
        "name": "agent@bank.com",
        "email": "agent@bank.com",
        "first_name": "Tigist Bekele",
        "enabled": 1,
        "last_active": "2024-05-18 10:30:00",
        "role": "A2C Bank Agent"
      }
    ]
  }
}
```

**Error Cases:**

- **400 `VALIDATION_ERROR` / `BANK_NOT_ONBOARDED`**: Caller has no bank binding (`No bank associated with the current user.`).
- **401 `AUTHENTICATION_ERROR`**: Called by unauthenticated user.
- **500 `INTERNAL_ERROR`**: Database query failure.

---

### 6.11 `POST /api/method/oan_a2c.api.v1.seller.onboarding.update_user`

Updates the profile (`full_name`, `role`) of a user belonging to the caller's bank.

**Authentication & Permissions:** Requires JWT Bearer token. Caller must have an assigned bank binding.
**Parameters (JSON Body):**
| Param | Type | Required | Default | Notes |
| :--- | :--- | :--- | :--- | :--- |
| **`email`** | string | Yes | — | Email of the target user |
| `full_name` | string | No | null | New full name (first_name) |
| `role` | string | No | null | If provided, appends this role (must be `A2C Bank Admin` or `A2C Bank Agent`) |

**Success Response (HTTP 200):**

```json
{
  "status": "success",
  "message": "User profile updated successfully.",
  "data": {
    "message": "User profile updated successfully."
  }
}
```

**Error Cases:**

- **400 `VALIDATION_ERROR`**: Caller has no bank binding, invalid email format, or supplied `role` is not in `{A2C Bank Admin, A2C Bank Agent}` (`Invalid role.`).
- **401 `AUTHENTICATION_ERROR`**: Called by unauthenticated user.
- **403 `PERMISSION_DENIED`**: Target user belongs to a different bank (`Not permitted to update a user from another bank.`).
- **404 `NOT_FOUND`**: Target `email` does not exist in `User`.
- **500 `INTERNAL_ERROR`**: Database save failure.

---

### 6.12 `POST /api/method/oan_a2c.api.v1.seller.onboarding.upload_image`

Uploads a Base64-encoded image (e.g., bank logo or product image) and returns its URL.

**Authentication & Permissions:** Requires JWT Bearer token.
**Parameters (JSON Body):**
| Param | Type | Required | Default | Notes |
| :--- | :--- | :--- | :--- | :--- |
| **`filename`** | string | Yes | — | Min 4, max 100 chars. Must match regex pattern for images (png, jpg, jpeg, webp) |
| **`filedata`** | string | Yes | — | Base64-encoded image string. Min 10, max 7,000,000 chars (~5MB limit) |

**Success Response (HTTP 200):**

```json
{
  "status": "success",
  "message": "Image uploaded successfully.",
  "data": {
    "message": "Image uploaded successfully.",
    "file_url": "/files/bank-logo.png"
  }
}
```

**Error Cases:**

- **400 `VALIDATION_ERROR`**: Filename does not match allowed image extensions or string length out of bounds.
- **400 `VALIDATION_ERROR`**: Base64 decoding fails (`Invalid file: content is not valid Base64.`).
- **401 `AUTHENTICATION_ERROR`**: Called by unauthenticated user.
- **500 `INTERNAL_ERROR`**: File creation or database save failure (`Failed to save uploaded image.`).

---

### 6.13 `POST /api/method/oan_a2c.api.v1.seller.onboarding.reset_member_password`

Issues a fresh temporary password for a Bank Agent in the caller's bank — the recovery path for an agent who has forgotten theirs.

The agent cannot sign in with it: the account is re-flagged must-change, so `login` returns `403 PASSWORD_CHANGE_REQUIRED` until they rotate it through `7.9 set_initial_password`. Any session the agent currently holds ends immediately — their refresh tokens are deleted and the JWT middleware rejects their existing access token.

**Authentication & Permissions:** Requires JWT Bearer token and the `A2C Bank Admin` role. The target must be a Bank Agent in the caller's own bank. Rate limited to 10 calls per 5 minutes per admin.
**Parameters (JSON Body):**

| Param          | Type   | Required | Default | Notes                                                             |
| :------------- | :----- | :------- | :------ | :---------------------------------------------------------------- |
| **`email`**    | string | Yes      | —       | Email of the Bank Agent whose password is being reissued          |
| **`password`** | string | Yes      | —       | Temporary password. 8–64 chars, at least one letter and one digit |

**Success Response (HTTP 200):**

```json
{
  "status": "success",
  "message": "Temporary password issued. The agent must set their own password at next login.",
  "data": null
}
```

**Error Cases:**

- **400 `VALIDATION_ERROR`**: Invalid email, password too short/simple, or the target is the caller's own account.
- **403 `PERMISSION_DENIED`**: Caller is not a Bank Admin; or the target is not a Bank Agent, belongs to another bank, or is at an equal/higher privilege level.
- **404 `NOT_FOUND`**: No such user.

---

## 7. Endpoint Reference: Authentication & Identity Gateway (`api/auth.py`)

### 7.1 `POST /api/method/oan_a2c.api.auth.login`

Authenticates seller credentials and returns a short-lived access JWT (15-min expiry) along with a database-backed refresh token.

**Authentication & Permissions:** Guest accessible (`allow_guest=True`).
**Parameters (JSON Body):**
| Param | Type | Required | Default | Notes |
| :--- | :--- | :--- | :--- | :--- |
| **`usr`** | string | Yes | — | User email address. Min length 1 |
| **`pwd`** | string | Yes | — | User password. Min length 1 |
| `remember_me` | boolean | No | false | If true, refresh token expires in 30 days instead of 1 day |

**Success Response (HTTP 200):**

```json
{
  "status": "success",
  "message": "Success",
  "data": {
    "token": "eyJhbGciOiJIUzI1NiIsIn...",
    "refresh_token": "a1b2c3d4e5f6...",
    "user": {
      "email": "admin@bank.com",
      "full_name": "Abebe Kebede",
      "roles": ["A2C Bank Admin", "System Manager"],
      "bank": "A2C-BANK-0001"
    }
  }
}
```

**Error Cases:**

- **400 `VALIDATION_ERROR`**: Missing `usr` or `pwd`.
- **401 `AUTHENTICATION_ERROR`**: Incorrect email/password (`Incorrect email or password.`), or account disabled/locked.
- **403 `PASSWORD_CHANGE_REQUIRED`**: The credentials are correct, but the password was issued by an admin (invite or reset) and must be rotated first. **No `token` or `refresh_token` is returned** — the response carries no `data` at all. Send the user to `7.9 set_initial_password`, then back to the login screen. See §7.9.
- **500 `INTERNAL_ERROR`**: System configuration error (missing `encryption_key`) or database error.

---

### 7.2 `POST /api/method/oan_a2c.api.auth.forgot_password`

Generates a 6-digit OTP for password recovery and stores it against the account.

> **Delivery is not implemented.** No SMS or email is sent, and the OTP is **not** returned in the response — while it was, any anonymous caller could POST an address here, read the key out of the JSON and take the account over through `7.3 reset_password`. Until a delivery channel exists this endpoint cannot complete a recovery on its own. Bank Agents recover through their Bank Admin instead (`6.13 reset_member_password`).

**Authentication & Permissions:** Guest accessible (`allow_guest=True`).
**Parameters (JSON Body):**
| Param | Type | Required | Default | Notes |
| :--- | :--- | :--- | :--- | :--- |
| **`email`** | string | Yes | — | Valid email address format |

**Success Response (HTTP 200):**

```json
{
  "status": "success",
  "message": "If your email is registered, a password reset OTP has been generated.",
  "data": null
}
```

_(Note: Unknown email addresses return the same success response, to prevent account enumeration.)_

**Error Cases:**

- **400 `VALIDATION_ERROR`**: Invalid email format.
- **500 `INTERNAL_ERROR`**: Database failure while storing the key.

---

### 7.3 `POST /api/method/oan_a2c.api.auth.reset_password`

Verifies the 6-digit OTP key and sets a new password for the account.

**Authentication & Permissions:** Guest accessible (`allow_guest=True`).
**Parameters (JSON Body):**
| Param | Type | Required | Default | Notes |
| :--- | :--- | :--- | :--- | :--- |
| **`email`** | string | Yes | — | Valid email address format |
| **`key`** | string | Yes | — | The 6-digit OTP key sent to user. Min length 1 |
| **`new_password`** | string | Yes | — | New password string. Min length 1 |

**Success Response (HTTP 200):**

```json
{
  "status": "success",
  "message": "Your password has been successfully updated. You may now login.",
  "data": null
}
```

**Error Cases:**

- **400 `VALIDATION_ERROR`**: Missing parameters or invalid email format.
- **401 `AUTHENTICATION_ERROR`**: Invalid or expired OTP key (`Invalid or expired reset OTP.`).
- **500 `INTERNAL_ERROR`**: Database update failure.

---

### 7.4 `POST /api/method/oan_a2c.api.auth.refresh`

Rotates a valid refresh token, issuing a new access JWT and a new refresh token while deleting the old token.

**Authentication & Permissions:** Guest accessible (`allow_guest=True`).
**Parameters (JSON Body):**
| Param | Type | Required | Default | Notes |
| :--- | :--- | :--- | :--- | :--- |
| **`refresh_token`** | string | Yes | — | Currently valid refresh token string. Min length 1 |

**Success Response (HTTP 200):**

```json
{
  "status": "success",
  "message": "Success",
  "data": {
    "token": "eyJhbGciOiJIUzI1NiIsIn...",
    "refresh_token": "f6e5d4c3b2a1..."
  }
}
```

**Error Cases:**

- **400 `VALIDATION_ERROR`**: Missing `refresh_token` parameter.
- **401 `AUTHENTICATION_ERROR`**: Token hash not found (`Invalid or expired refresh token.`), token past expiry date (`Refresh token has expired.`), or target user account disabled (`User is disabled or does not exist.`).
- **500 `INTERNAL_ERROR`**: Database transaction failure.

---

### 7.5 `POST /api/method/oan_a2c.api.auth.logout`

Revokes a refresh token by deleting it from the database.

**Authentication & Permissions:** Guest accessible (`allow_guest=True`).
**Parameters (JSON Body):**
| Param | Type | Required | Default | Notes |
| :--- | :--- | :--- | :--- | :--- |
| **`refresh_token`** | string | Yes | — | Refresh token to revoke. Min length 1 |

**Success Response (HTTP 200):**

```json
{
  "status": "success",
  "message": "Logged out successfully.",
  "data": null
}
```

**Error Cases:**

- **400 `VALIDATION_ERROR`**: Missing `refresh_token` parameter.
- **500 `INTERNAL_ERROR`**: Database deletion failure.

---

### 7.6 `GET /api/method/oan_a2c.api.auth.get_me`

Returns the authenticated caller's profile details including roles and associated bank binding.

**Authentication & Permissions:** Requires JWT Bearer token.
**Parameters:** None.
**Success Response (HTTP 200):**

```json
{
  "status": "success",
  "message": "Success",
  "data": {
    "email": "admin@bank.com",
    "full_name": "Abebe Kebede",
    "roles": ["A2C Bank Admin", "System Manager"],
    "bank": "A2C-BANK-0001"
  }
}
```

**Error Cases:**

- **401 `AUTHENTICATION_ERROR`**: Called by unauthenticated user (`Guest`).
- **500 `INTERNAL_ERROR`**: Database lookup failure.

---

### 7.7 `GET /api/method/oan_a2c.api.auth.get_user_profile`

Returns detailed profile information for the authenticated user, designed specifically for the "My Profile" screen.

**Authentication & Permissions:** Requires JWT Bearer token.
**Parameters:** None.
**Success Response (HTTP 200):**

```json
{
  "status": "success",
  "message": "Success",
  "data": {
    "personal_information": {
      "user_image": "/private/files/avatar.png",
      "full_name": "Abebe Kebede",
      "email_address": "admin@bank.com",
      "phone_number": "+251911111111",
      "language": "English"
    },
    "account_information": {
      "user_role": "Bank Admin",
      "organization": "Example Bank",
      "employee_id": "EMP-001",
      "member_since": "July 2026"
    }
  }
}
```

**Error Cases:**

- **401 `AUTHENTICATION_ERROR`**: Called by unauthenticated user.

---

### 7.8 `POST /api/method/oan_a2c.api.auth.update_profile`

Updates the personal profile details of the authenticated user.

**Authentication & Permissions:** Requires JWT Bearer token.
**Parameters (JSON Body):**
| Param | Type | Required | Default | Notes |
| :--- | :--- | :--- | :--- | :--- |
| `full_name` | string | No | null | The user's full name |
| `phone_number` | string | No | null | The user's mobile number |
| `language` | string | No | null | Preferred language code (e.g., "English") |
| `user_image` | string | No | null | URL from the `upload_image` endpoint |

**Success Response (HTTP 200):**
_Returns the fully updated profile object identical to `7.7 get_user_profile`._

**Error Cases:**

- **401 `AUTHENTICATION_ERROR`**: Called by unauthenticated user.
- **500 `INTERNAL_ERROR`**: Database save failure.

---

### 7.9 `POST /api/method/oan_a2c.api.auth.set_initial_password`

Rotates an admin-issued temporary password into one only the user knows. This is the only action available to an account that `login` has answered with `403 PASSWORD_CHANGE_REQUIRED`.

Guest accessible by necessity: such an account cannot hold a session until this call succeeds, so there is no JWT to authorize it with. The temporary password is re-verified here — the same proof `login` itself demands.

On success the flag is cleared, every session for the user is invalidated (including any refresh tokens), and the user signs in normally with the new password.

**Authentication & Permissions:** Guest accessible (`allow_guest=True`). Rate limited to 5 calls per 5 minutes per IP.
**Parameters (JSON Body):**

| Param                  | Type   | Required | Default | Notes                                                                      |
| :--------------------- | :----- | :------- | :------ | :------------------------------------------------------------------------- |
| **`usr`**              | string | Yes      | —       | Email address (or phone number, resolved the same way as`login`)           |
| **`current_password`** | string | Yes      | —       | The temporary password issued by the Bank Admin                            |
| **`new_password`**     | string | Yes      | —       | 8–64 chars, and must contain at least one letter, one digit and one symbol |

**Success Response (HTTP 200):**

```json
{
  "status": "success",
  "message": "Password set successfully. Please sign in with your new password.",
  "data": null
}
```

**Error Cases:**

- **400 `VALIDATION_ERROR`**: New password fails the complexity rule, or is the same as the temporary one (`Choose a password different from the temporary one.`).
- **401 `AUTHENTICATION_ERROR`**: Wrong temporary password — **or** the account is not in the must-change state. The two are deliberately indistinguishable (`Incorrect email or password.`) so the endpoint reveals neither which accounts exist nor which are holding a temporary password.
- **429**: Rate limit exceeded.

---

### 7.10 `POST /api/method/oan_a2c.api.auth.change_password`

Changes the authenticated user's password.

**Authentication & Permissions:** Requires JWT Bearer token.
**Parameters (JSON Body):**
| Param | Type | Required | Default | Notes |
| :--- | :--- | :--- | :--- | :--- |
| **`current_password`** | string | Yes | — | The user's current password |
| **`new_password`** | string | Yes | — | Min 8, max 64 chars. Must contain at least 1 letter, 1 number, and 1 special character |

**Success Response (HTTP 200):**

```json
{
  "status": "success",
  "message": "Password changed successfully.",
  "data": null
}
```

**Error Cases:**

- **400 `VALIDATION_ERROR`**: New password does not meet complexity requirements.
- **401 `AUTHENTICATION_ERROR`**: Called by unauthenticated user, or current password is incorrect.
- **500 `INTERNAL_ERROR`**: Database save failure.

---

### 7.11 `POST /api/method/oan_a2c.api.v1.auth.register_user`

Registers a new user account with a default role of `A2C Bank Admin` (or `A2C Development Agent`).

**Authentication & Permissions:** Guest accessible (`allow_guest=True`).
**Parameters (JSON Body):**
| Param | Type | Required | Default | Notes |
| :--- | :--- | :--- | :--- | :--- |
| **`email`** | string | Yes | — | Must be a valid email address format |
| **`full_name`** | string | Yes | — | Min length 2 characters |
| **`password`** | string | Yes | — | Min 8, max 64 chars. Must contain at least 1 letter, 1 number, and 1 special character |
| **`phone_number`** | string | Yes | — | Must be a valid phone number format |
| `role` | string | No | `A2C Bank Admin` | Role to assign. Must be `A2C Bank Admin` or `A2C Development Agent` |

**Success Response (HTTP 200):**

```json
{
  "status": "success",
  "message": "If your email and phone number are not already registered, your account has been created successfully.",
  "data": {
    "message": "If your email and phone number are not already registered, your account has been created successfully."
  }
}
```

**Error Cases:**

- **400 `VALIDATION_ERROR`**: Invalid email/phone format, missing required fields, or invalid role requested.
- **500 `INTERNAL_ERROR`**: Database save failure.
