"""Regression tests for the security findings fixed in this branch.

Each test asserts the FIXED behaviour: the previously-vulnerable action is now
blocked (or the intended action still works). A PASS means the fix holds. These
run inside the test transaction and roll back.

Covered: A1 (bank-status authorization), A2 (dev-agent catalog scope),
A3 (bank-logo file ownership), A4 (pagination bounds), D15 (platform-admin
recognition), D18 (audit-trail create permission), D21 (platform-admin
product approval).
"""

import unittest

import frappe

from oan_a2c.a2c_marketplace.roles import (
    ADMIN_ROLE,
    BANK_ADMIN_ROLE,
    BANK_AGENT_ROLE,
    DEVELOPMENT_AGENT_ROLE,
    FARMER_ROLE,
)


def _user(email, role):
    if not frappe.db.exists("User", email):
        frappe.get_doc(
            {"doctype": "User", "email": email, "first_name": email.split("@")[0],
             "roles": [{"role": role}]}
        ).insert(ignore_permissions=True, ignore_mandatory=True)
    return email


def _bank(h, code):
    # A COMPLETE record (all mandatory fields), so the API's internal doc.save()
    # re-validates cleanly.
    return frappe.get_doc(
        {"doctype": "A2C Participating Bank", "bank_name": f"Bank-{h}", "bank_code": code,
         "entity_type": "Bank", "registered_email": f"reg-{h}@ex.com",
         "registered_phone": "251900000000", "registered_region": "Addis Ababa",
         "registered_country": "Ethiopia"}
    ).insert(ignore_permissions=True)


def _bind(email, bank_name):
    frappe.get_doc(
        {"doctype": "User Permission", "user": email, "allow": "A2C Participating Bank",
         "for_value": bank_name}
    ).insert(ignore_permissions=True)


class TestSecurityRegression(unittest.TestCase):
    @classmethod
    def tearDownClass(cls):
        frappe.set_user("Administrator")

    def tearDown(self):
        frappe.set_user("Administrator")

    # ---------- A1: bank status changes are platform-admin-only ----------
    # update_bank_status now denies non-platform-admins and leaves the status
    # untouched. @handle_api_errors turns the PermissionError into a
    # {"status": "error", "code": "PERMISSION_DENIED"} envelope rather than
    # re-raising, so we assert on the envelope + the DB.
    def test_A1_dev_agent_cannot_change_bank_status(self):
        from oan_a2c.api.v1.seller.onboarding import update_bank_status
        h = frappe.generate_hash(length=6)
        bank = _bank(h, f"DC{h[:5]}")
        dev = _user(f"dev-{h}@ex.com", DEVELOPMENT_AGENT_ROLE)

        frappe.set_user(dev)
        res = update_bank_status(bank_code=bank.bank_code, new_status="Suspended")
        status = frappe.db.get_value("A2C Participating Bank", bank.name, "status")
        self.assertEqual(res.get("code"), "PERMISSION_DENIED", f"A1 dev-agent not blocked: res={res}")
        self.assertNotEqual(status, "Suspended", "A1 dev-agent: bank status must be unchanged")

    def test_A1_bank_admin_cannot_approve_own_bank(self):
        from oan_a2c.api.v1.seller.onboarding import update_bank_status
        h = frappe.generate_hash(length=6)
        bank = _bank(h, f"AC{h[:5]}")
        admin = _user(f"badmin-{h}@ex.com", BANK_ADMIN_ROLE)
        _bind(admin, bank.name)

        frappe.set_user(admin)
        res = update_bank_status(bank_code=bank.bank_code, new_status="Active")
        status = frappe.db.get_value("A2C Participating Bank", bank.name, "status")
        self.assertEqual(res.get("code"), "PERMISSION_DENIED", f"A1 bank-admin not blocked: res={res}")
        self.assertNotEqual(status, "Active", "A1 bank-admin: a bank must not self-approve")

    def test_A1_platform_admin_can_change_bank_status(self):
        # The intended path still works: a platform admin moves a bank through its
        # lifecycle. In Review -> Suspended is a valid transition that (unlike
        # -> Active) needs no KYC document, so it isolates the auth gate.
        from oan_a2c.api.v1.seller.onboarding import update_bank_status
        h = frappe.generate_hash(length=6)
        bank = _bank(h, f"OK{h[:5]}")  # created "In Review" by default

        frappe.set_user("Administrator")
        res = update_bank_status(bank_code=bank.bank_code, new_status="Suspended")
        status = frappe.db.get_value("A2C Participating Bank", bank.name, "status")
        self.assertEqual(res.get("status"), "success", f"platform admin blocked: res={res}")
        self.assertEqual(status, "Suspended", "platform admin should be able to change bank status")

    def test_A1_invalid_transition_is_rejected(self):
        # Even for a platform admin, the lifecycle state machine is enforced:
        # nothing transitions back into "In Review".
        from oan_a2c.api.v1.seller.onboarding import update_bank_status
        h = frappe.generate_hash(length=6)
        bank = _bank(h, f"TR{h[:5]}")
        frappe.db.set_value("A2C Participating Bank", bank.name, "status", "Active")

        frappe.set_user("Administrator")
        res = update_bank_status(bank_code=bank.bank_code, new_status="In Review")
        status = frappe.db.get_value("A2C Participating Bank", bank.name, "status")
        self.assertEqual(res.get("status"), "error", f"invalid transition not rejected: res={res}")
        self.assertEqual(status, "Active", "invalid transition must not change status")

    # ---------- A2: Development Agent does not read a bank's pending products ----------
    def test_A2_dev_agent_cannot_read_pending_products(self):
        h = frappe.generate_hash(length=6)
        bank = _bank(h, f"P1{h[:5]}")
        p_pending = frappe.get_doc(
            {"doctype": "A2C Loan Product", "product_name": f"Prod1-{h}", "bank": bank.name,
             "min_interest_rate": 5, "max_amount": 1000, "tenure_months": 12,
             "status": "Pending Approval"}
        ).insert(ignore_permissions=True, ignore_mandatory=True)
        # The controller auto-approves on save, so set_value bypasses it to keep the
        # product genuinely pending -- the same trick the seller PR's own tests use.
        frappe.db.set_value("A2C Loan Product", p_pending.name, "status", "Pending Approval")

        dev = _user(f"dev2-{h}@ex.com", DEVELOPMENT_AGENT_ROLE)
        frappe.set_user(dev)
        visible = frappe.get_list("A2C Loan Product", pluck="name", limit_page_length=0)
        self.assertNotIn(
            p_pending.name, visible,
            "A2: a Development Agent must not see another bank's pending product",
        )

    # ---------- A3: bank logo swap cannot touch files it doesn't own ----------
    def test_A3_bank_admin_cannot_delete_arbitrary_file(self):
        from oan_a2c.api.v1.seller.onboarding import update_bank_profile
        h = frappe.generate_hash(length=6)
        victim = frappe.get_doc(
            {"doctype": "File", "file_name": f"victim-{h}.txt", "content": "secret-kyc",
             "is_private": 1}
        ).insert(ignore_permissions=True)

        bank = _bank(h, f"L{h[:6]}")
        admin = _user(f"ladmin-{h}@ex.com", BANK_ADMIN_ROLE)
        _bind(admin, bank.name)

        frappe.set_user(admin)
        res = update_bank_profile(logo=victim.file_url)   # not this bank's file -> rejected
        self.assertEqual(res.get("status"), "error", f"A3: non-owned logo not rejected: res={res}")
        self.assertTrue(frappe.db.exists("File", victim.name), "A3: victim file must survive")

    def test_A3_bank_admin_can_change_own_logo(self):
        from oan_a2c.api.v1.seller.onboarding import update_bank_profile
        h = frappe.generate_hash(length=6)
        bank = _bank(h, f"LG{h[:5]}")
        admin = _user(f"lgadmin-{h}@ex.com", BANK_ADMIN_ROLE)
        _bind(admin, bank.name)

        frappe.set_user(admin)  # uploaded file's owner becomes this bank's admin
        own = frappe.get_doc(
            {"doctype": "File", "file_name": f"logo-{h}.png", "content": "x", "is_private": 0}
        ).insert(ignore_permissions=True)
        res = update_bank_profile(logo=own.file_url)
        self.assertEqual(res.get("status"), "success", f"A3: own logo rejected: res={res}")
        self.assertEqual(
            frappe.db.get_value("A2C Participating Bank", bank.name, "logo"), own.file_url
        )

    # ---------- D15: an A2C Administrator is recognized as a platform admin ----------
    # is_platform_admin now also returns True for the ADMIN_ROLE, not just the
    # built-in Administrator user / System Manager.
    def test_D15_a2c_administrator_is_platform_admin(self):
        from oan_a2c.a2c_marketplace.permissions import is_platform_admin
        h = frappe.generate_hash(length=6)
        admin = _user(f"platadmin-{h}@ex.com", ADMIN_ROLE)
        self.assertTrue(
            is_platform_admin(admin),
            "D15: an A2C Administrator must be recognized as a platform admin",
        )

    def test_D15_farmer_is_not_platform_admin(self):
        from oan_a2c.a2c_marketplace.permissions import is_platform_admin
        h = frappe.generate_hash(length=6)
        farmer = _user(f"plainfarmer-{h}@ex.com", FARMER_ROLE)
        self.assertFalse(
            is_platform_admin(farmer),
            "D15: a farmer must not be treated as a platform admin",
        )

    # ---------- D21: an A2C Administrator may approve products (auto-approve on create) ----------
    # before_save's is_bank_admin gate now also accepts the ADMIN_ROLE, so a
    # platform admin creating a product gets it approved instead of stuck Pending.
    def test_D21_platform_admin_created_product_is_auto_approved(self):
        h = frappe.generate_hash(length=6)
        bank = _bank(h, f"D2{h[:5]}")
        admin = _user(f"a2cadmin-{h}@ex.com", ADMIN_ROLE)

        frappe.set_user(admin)
        prod = frappe.get_doc(
            {"doctype": "A2C Loan Product", "product_name": f"AdminProd-{h}", "bank": bank.name,
             "min_interest_rate": 5, "max_amount": 1000, "tenure_months": 12}
        ).insert(ignore_permissions=True, ignore_mandatory=True)
        self.assertEqual(
            prod.status, "Active",
            "D21: an A2C Administrator-created product should be auto-approved (Active)",
        )

    # ---------- A4: seller product listing bounds its pagination ----------
    # list_products now clamps page (a negative page gave a negative SQL offset)
    # and page_size (an unbounded value is a memory/DoS vector) before querying.
    def test_A4_zero_page_and_huge_page_size_are_clamped(self):
        from oan_a2c.api.v1.seller.loan_products import list_products
        h = frappe.generate_hash(length=6)
        bank = _bank(h, f"A4{h[:5]}")
        admin = _user(f"a4admin-{h}@ex.com", BANK_ADMIN_ROLE)
        _bind(admin, bank.name)

        frappe.set_user(admin)
        res = list_products(page=0, page_size=99999)
        self.assertEqual(res.get("status"), "success", f"A4: list_products errored: res={res}")
        pg = res.get("pagination") or {}
        self.assertEqual(pg.get("page"), 1, f"A4: page 0 must clamp up to 1. pagination={pg}")
        self.assertEqual(pg.get("page_size"), 200, f"A4: page_size must cap at 200. pagination={pg}")

    def test_A4_negative_page_and_zero_page_size_are_clamped(self):
        from oan_a2c.api.v1.seller.loan_products import list_products
        h = frappe.generate_hash(length=6)
        bank = _bank(h, f"A4N{h[:4]}")
        admin = _user(f"a4nadmin-{h}@ex.com", BANK_ADMIN_ROLE)
        _bind(admin, bank.name)

        frappe.set_user(admin)
        res = list_products(page=-5, page_size=0)
        self.assertEqual(res.get("status"), "success", f"A4: list_products errored: res={res}")
        pg = res.get("pagination") or {}
        self.assertEqual(pg.get("page"), 1, f"A4: a negative page must clamp to 1. pagination={pg}")
        self.assertGreaterEqual(
            pg.get("page_size"), 1, f"A4: page_size must clamp up to at least 1. pagination={pg}"
        )

    # ---------- D18: audit trail is append-only; bank roles cannot create it ----------
    # The status-update endpoint writes its audit record with
    # insert(ignore_permissions=True) precisely because bank roles have create=0
    # on this doctype. A plain insert() would raise PermissionError after the
    # status already changed. This locks that permission invariant in.
    def test_D18_bank_roles_cannot_create_audit_events(self):
        h = frappe.generate_hash(length=6)
        admin = _user(f"d18admin-{h}@ex.com", BANK_ADMIN_ROLE)
        agent = _user(f"d18agent-{h}@ex.com", BANK_AGENT_ROLE)
        padmin = _user(f"d18padmin-{h}@ex.com", ADMIN_ROLE)

        self.assertFalse(
            frappe.has_permission("A2C Loan Application Audit Event", "create", user=admin),
            "D18: a Bank Admin must not have create on the append-only audit doctype",
        )
        self.assertFalse(
            frappe.has_permission("A2C Loan Application Audit Event", "create", user=agent),
            "D18: a Bank Agent must not have create on the append-only audit doctype",
        )
        # Sanity-check the other side of the invariant: a platform admin legitimately may.
        self.assertTrue(
            frappe.has_permission("A2C Loan Application Audit Event", "create", user=padmin),
            "D18: an A2C Administrator should have create on the audit doctype",
        )
