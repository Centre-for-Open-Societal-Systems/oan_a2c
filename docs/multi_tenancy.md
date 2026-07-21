# Loan Marketplace: Multi-Tenancy Security Architecture

## Overview
The OAN Access-To-Credit (A2C) system is a multi-tenant platform designed to handle multiple Participating Banks within a single Frappe application instance. To maintain strict data isolation and privacy, a robust, centralized multi-tenancy architecture is implemented.

This document describes how multi-tenancy is enforced securely for all `BANK_SCOPED` DocTypes across both modern API modules and legacy modules.

## Architecture

Multi-tenancy in the A2C application is **App-scoped**, not Module-scoped. This means security rules apply uniformly to all relevant data, regardless of which API endpoint or module initiates the request. 

The security architecture uses a two-pronged approach implemented via standard Frappe hooks:
1. **Query-level Isolation** (Database filtering)
2. **Document-level Isolation** (Python object evaluation)

### 1. Query-Level Isolation (`bank_scope_query`)
For any list-based data retrieval (e.g., `frappe.get_list`, `frappe.get_all`, `frappe.db.count`), Frappe allows injecting dynamic SQL `WHERE` conditions. 

The `bank_scope_query` function (in `oan_a2c.a2c_marketplace.permissions`) intercepts these queries and dynamically injects `` `bank` = '{user_bank}' ``.
This ensures that at the database layer, a user can only ever select rows corresponding to their assigned "A2C Participating Bank".

### 2. Document-Level Isolation (`bank_scope_doc`)
For single-document operations (e.g., `frappe.get_doc()`, `doc.save()`), standard SQL query conditions don't always apply, especially if the document is being created or updated.

The `bank_scope_doc` function runs as a `has_permission` hook. It compares the `bank` field of the current document in memory against the user's bound bank context. If they do not match, a `PermissionError` is raised by the framework.

## Enforcement Mechanism

Both security prongs are registered globally in `oan_a2c/hooks.py`. 

```python
# Bank Scoped DocTypes
BANK_SCOPED = [
    "A2C Loan Product",
    "A2C Loan Application",
    # ... other bank-specific doctypes
]

permission_query_conditions = {}
has_permission = {}

for doctype in BANK_SCOPED:
    permission_query_conditions[doctype] = "oan_a2c.a2c_marketplace.permissions.bank_scope_query"
    has_permission[doctype] = "oan_a2c.a2c_marketplace.permissions.bank_scope_doc"
```

Because these hooks are applied centrally, they guard both the newly structured `api/v1/seller/` APIs and the legacy `api/v1/loan_applications.py` APIs. 

## Exception: System Managers
Users with the `System Manager` role are unbound. The permission hooks explicitly bypass the bank context checks for these users, allowing them to see and manage all data across all tenants. 

## Best Practices for Developers
1. **Never bypass `frappe.get_all` / `frappe.get_list`**: Do not use raw `frappe.db.sql` for selecting bank-scoped data unless you manually apply the bank filter, as raw SQL bypasses `permission_query_conditions`.
2. **Register New DocTypes**: If you create a new DocType that belongs to a specific bank, you must add it to the `BANK_SCOPED` list in `hooks.py`.
3. **Legacy Data Note**: Legacy documents (like older `A2C Loan Application` records) that were created without a `bank` field populated will be completely hidden from Bank Agents (failing the `bank = ...` check). Only System Managers will be able to see them.
