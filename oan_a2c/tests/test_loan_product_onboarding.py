import unittest

import frappe

from oan_a2c.api.v1.seller.loan_products import create_product


class TestLoanProductOnboarding(unittest.TestCase):
	"""Integration coverage for FR-AGL-CP-03 (create_product).

	create_product had schema-level coverage only (test_pydantic_validation.py) --
	nothing exercised the actual API: which bank a product lands under, the Bank
	Admin auto-approval hook on A2CLoanProduct.before_save, the inactive-bank
	guard, and bulk creation. This file closes that gap.
	"""

	@classmethod
	def setUpClass(cls):
		cls.active_bank = cls._ensure_bank("ONBOARD_ACTIVE", "Onboarding Active Bank", "Active")
		cls.inactive_bank = cls._ensure_bank("ONBOARD_INREVIEW", "Onboarding In Review Bank", "In Review")

		cls.agent_email = "onboarding_agent@test.com"
		cls._ensure_user(cls.agent_email, "A2C Bank Agent", cls.active_bank)

		cls.admin_email = "onboarding_admin@test.com"
		cls._ensure_user(cls.admin_email, "A2C Bank Admin", cls.active_bank)

		cls.inactive_agent_email = "onboarding_inactive_agent@test.com"
		cls._ensure_user(cls.inactive_agent_email, "A2C Bank Agent", cls.inactive_bank)

	@staticmethod
	def _ensure_bank(bank_code, bank_name, status):
		existing = frappe.db.exists("A2C Participating Bank", {"bank_code": bank_code})
		if existing:
			return existing
		doc = frappe.get_doc(
			{
				"doctype": "A2C Participating Bank",
				"bank_code": bank_code,
				"bank_name": bank_name,
				"entity_type": "Commercial Bank",
				"registered_region": "Addis Ababa",
				"registered_country": "Ethiopia",
				"registered_email": f"{bank_code.lower()}@test.com",
				"registered_phone": "+251900000001",
				"status": status,
				"kyc_document": "/private/files/test_kyc.pdf",
				"gro_name": "Test GRO",
				"ops_name": "Test Ops",
			}
		).insert(ignore_permissions=True)
		return doc.name

	@staticmethod
	def _ensure_user(email, role, bank):
		if not frappe.db.exists("User", email):
			frappe.get_doc(
				{
					"doctype": "User",
					"email": email,
					"first_name": email.split("@")[0],
					"roles": [{"role": role}],
				}
			).insert(ignore_permissions=True, ignore_mandatory=True)
		if not frappe.db.exists(
			"User Permission", {"user": email, "allow": "A2C Participating Bank", "for_value": bank}
		):
			frappe.get_doc(
				{
					"doctype": "User Permission",
					"user": email,
					"allow": "A2C Participating Bank",
					"for_value": bank,
				}
			).insert(ignore_permissions=True)

	def tearDown(self):
		frappe.set_user("Administrator")

	@staticmethod
	def _login(email):
		"""Switch session user, clearing the permission/user-permission cache too.

		frappe.set_user alone leaves has_permission's per-user cache in place;
		within one test-runner process that lets a bank resolved for a previous
		test's user bleed into this one (see test_bank_scope_enforcement's fix
		for the same class of leak). Always route user switches through here.
		"""
		frappe.set_user(email)
		frappe.clear_cache(user=email)

	@staticmethod
	def _payload(**overrides):
		payload = {
			"product_name": f"Onboarding Test Product {frappe.generate_hash(length=6)}",
			"min_interest_rate": 5,
			"max_interest_rate": 12,
			"min_amount": 1000,
			"max_amount": 50000,
			"tenure_months": 6,
		}
		payload.update(overrides)
		return payload

	def test_bank_agent_created_product_starts_pending_approval(self):
		self._login(self.agent_email)
		res = create_product(**self._payload())
		self.assertEqual(res.get("status"), "success", str(res))

		product_id = res["data"]["product_ids"][0]
		status = frappe.db.get_value("A2C Loan Product", product_id, "status")
		self.assertEqual(status, "Pending Approval")

	def test_bank_admin_created_product_is_auto_approved(self):
		"""Aruna's auto-approval request: a Bank Admin's own product skips review."""
		self._login(self.admin_email)
		res = create_product(**self._payload())
		self.assertEqual(res.get("status"), "success", str(res))

		product_id = res["data"]["product_ids"][0]
		status = frappe.db.get_value("A2C Loan Product", product_id, "status")
		self.assertEqual(status, "Active", "a Bank Admin-created product should auto-approve on creation")

	def test_create_product_is_bound_to_the_callers_own_bank(self):
		"""bank_scoped resolves the bank from the session -- a client can't set it."""
		self._login(self.agent_email)
		res = create_product(**self._payload())
		product_id = res["data"]["product_ids"][0]
		bank = frappe.db.get_value("A2C Loan Product", product_id, "bank")
		self.assertEqual(bank, self.active_bank)

	def test_create_product_blocked_for_an_inactive_bank(self):
		self._login(self.inactive_agent_email)
		res = create_product(**self._payload())
		self.assertEqual(res.get("status"), "error")
		self.assertEqual(res.get("code"), "BANK_NOT_ACTIVE")

	def test_bulk_create_returns_one_id_per_product(self):
		self._login(self.agent_email)
		res = create_product(
			products=[
				self._payload(product_name=f"Bulk A {frappe.generate_hash(length=4)}"),
				self._payload(product_name=f"Bulk B {frappe.generate_hash(length=4)}"),
			]
		)
		self.assertEqual(res.get("status"), "success", str(res))
		self.assertEqual(len(res["data"]["product_ids"]), 2)

	def test_min_amount_greater_than_max_amount_is_rejected(self):
		self._login(self.agent_email)
		res = create_product(**self._payload(min_amount=50000, max_amount=1000))
		self.assertEqual(res.get("status"), "error")
		self.assertEqual(res.get("code"), "VALIDATION_ERROR")
