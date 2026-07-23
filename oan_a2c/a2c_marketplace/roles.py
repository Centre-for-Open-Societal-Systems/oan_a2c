"""
Single source of truth for oan_a2c role names and groupings.

Tiers (highest to lowest privilege over bank data):
  - A2C Marketplace Admin : platform admin, unbound by bank isolation (sees all banks)
  - Bank Admin            : manages one bank; scoped by its A2C Participating Bank
                            User Permission
  - Bank Agent            : operates within one bank; scoped likewise

Other roles:
  - Development Agent     : platform-side field agent who helps farmers apply
                            (often on their behalf). Operates across banks, so
                            it is bank-unbound — bank tenant isolation does not
                            apply. What it can *do* per doctype (e.g. edit
                            applications but only read the product catalog) is
                            standard RBAC via DocPerm, a separate axis from this.

System Manager (Frappe built-in) is also treated as bank-unbound so site
administrators/support are never locked out. The `Administrator` user bypasses
everything via Frappe core.

IMPORTANT: a role name is the Frappe `Role` document's identifier. Changing a
value here is a DATA MIGRATION (a frappe.rename_doc patch plus updates to every
doctype permission JSON, workflow definition and fixture that references it) —
never just an edit. Import these constants everywhere instead of hardcoding the
strings, so the exact casing/spacing is defined in exactly one place.
"""

MARKETPLACE_ADMIN_ROLE = "A2C Marketplace Admin"
BANK_ADMIN_ROLE = "A2C Bank Admin"
BANK_AGENT_ROLE = "A2C Bank Agent"
DEVELOPMENT_AGENT_ROLE = "A2C Development Agent"

# Bank-scoped roles (belong to exactly one A2C Participating Bank).
BANK_ROLES = (BANK_ADMIN_ROLE, BANK_AGENT_ROLE)

# Roles exempt from bank tenant isolation (see all banks).
#
# Development Agent is here because it is platform staff operating across banks,
# NOT because it may see everything: bank-unbound only removes the bank filter on
# doctypes the role already has DocPerm for. Development Agent has DocPerm on
# A2C Loan Application (its job) but none on the a2c_marketplace catalog/seller
# doctypes, so being unbound never exposes the seller side — RBAC keeps that wall.
BANK_UNBOUND_ROLES = frozenset({MARKETPLACE_ADMIN_ROLE, DEVELOPMENT_AGENT_ROLE, "System Manager"})
