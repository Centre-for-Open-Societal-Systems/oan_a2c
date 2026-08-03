# Refactor: Test Isolation for setUp Fixtures

## Problem

Tests that use `if not frappe.db.exists(...)` guards before creating fixtures pass locally
but fail on CI. Locally, leftover data from previous runs satisfies the guard so the
creation code never runs. CI starts with a clean database every time, hits the creation
path, and exposes schema mismatches (missing mandatory fields, renamed doctypes, removed
status options).

Discovered when `A2C Participating Bank` gained new mandatory fields and dropped the
`"Onboarding"` status option — three test files had stale fixture code that was never
exercised locally.

## What to change

Replace the `if not exists → reuse` pattern with proper isolated fixtures:

1. **Create with unique names per test run** — use a prefix + `frappe.generate_hash()`
   so each run owns its own records and can't collide with or depend on previous runs.

2. **Tear down in `tearDownClass`** — delete every record the class created so the
   database is clean for the next run.

3. **Never rely on ambient data** — don't call `frappe.db.get_value("Doctype", {}, "name")`
   to grab whatever happens to exist. Create exactly what the test needs.

## Pattern to follow

```python
@classmethod
def setUpClass(cls):
    frappe.set_user("Administrator")
    suffix = frappe.generate_hash(length=6)
    cls.bank = frappe.get_doc({
        "doctype": "A2C Participating Bank",
        "bank_code": f"TEST_{suffix}",
        "bank_name": f"Test Bank {suffix}",
        "status": "In Review",
        "entity_type": "Commercial Bank",
        "registered_email": f"test_{suffix}@example.com",
        "registered_phone": "+251911000000",
        "registered_city": "Addis Ababa",
        "registered_country": "Ethiopia",
    }).insert(ignore_permissions=True)

@classmethod
def tearDownClass(cls):
    frappe.delete_doc("A2C Participating Bank", cls.bank.name, force=True)
```

## Files to refactor

- `oan_a2c/tests/test_a2c_lead.py` — `_ensure_credit_info_prerequisites` and
  `TestLeadSanitizationXSS.setUpClass` both fall back to ambient `A2C Loan Product` /
  `A2C Participating Bank` records
- `oan_a2c/tests/test_stats_cache.py` — `TestStatsCache.setUpClass` reuses
  `TEST_STATS_BANK` if it exists; also manually patches `name` via raw SQL which is fragile
- `oan_a2c/tests/test_users_api.py` — `_create_test_bank` reuses bank by `bank_code` if
  it already exists

## Why this matters

A test suite that only passes because of leftover state is not a test suite — it is
a false safety net. The goal is every test class owning its data from `setUpClass` to
`tearDownClass`, making the suite repeatable on any clean environment.
