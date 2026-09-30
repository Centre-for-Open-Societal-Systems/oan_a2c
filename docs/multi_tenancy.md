# Multi-Tenancy

How A2C keeps each Participating Bank's data separate inside one Frappe site. For why it is built this way, see `design_decisions.md` §5.

## The two facts

1. **Which bank a user works for.** A Frappe User Permission record: `allow = A2C Participating Bank`, value = the bank. Created in the same transaction as the user, when a bank is registered or a member is invited.
2. **Which bank owns a record.** A `bank` field on every bank-scoped record, copied from the product or the creator's bank. Never taken from the client.

Scoping compares the two.

## Who is bound to a bank

| Role                              | Sees                                                             |
| --------------------------------- | ---------------------------------------------------------------- |
| A2C Administrator, System Manager | Every bank                                                       |
| A2C Development Agent             | Every bank (agent-sourced applications only, live products only) |
| A2C Bank Admin, A2C Bank Agent    | Their own bank only                                              |
| A2C Farmer                        | Their own applications and profile; every bank's live products   |

One function (`is_bank_unbound`) decides whether a user sees every bank. Do not repeat role lists elsewhere.

## Bank-scoped doctypes

Listed in `BANK_SCOPED` in `hooks.py`: loan products, term relationships, product lookups, attribute lookups, loan applications, application audit events and loan status stages.

## How it is enforced

Two Frappe hooks are registered for every bank-scoped doctype:

- **List filter** (`permission_query_conditions`). Adds `bank = <user's bank>` to every `frappe.get_list` query. Loan applications and loan products have their own variants that also handle farmers, Development Agents and the hidden draft stage.
- **Single-record check** (`has_permission`). Denies `get_doc` and saves on another bank's record.

## Fail closed

A bank user with no bank binding sees nothing: the list filter returns `1=0` and the record check denies. A missing binding must never mean "sees everything".

## Reads that skip the hooks

`frappe.get_all`, `frappe.db.get_all` and `frappe.db.get_list` **do not** run the list filter. On a bank-scoped doctype, every such call must either:

- pass `bank_filters(base=...)` to add the bank filter explicitly, or
- carry a `# bank-scope-exempt: <reason>` comment, for example when the result only narrows a query that is itself scoped.

`tests/test_bank_scope_enforcement.py` scans the code in CI and fails the build on any unmarked call. Raw `frappe.db.sql` is not covered by the scan: avoid it on bank-scoped data, or apply the filter yourself.

## Adding a bank-scoped doctype

1. Give it a `bank` field, stamped on write from the parent record.
2. Add it to `BANK_SCOPED` in `hooks.py`.
3. Check any `get_all` reads on it pass the CI scan.

## Legacy records

A record with no `bank` value is invisible to bank users, because it matches no bank. Only unbound roles can see it.
