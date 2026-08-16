"""
Round-trip coverage for the A2C Loan Product -> A2C Loan Product Lookup mirror.

The lookup is a denormalized copy (bank stamp + accepting_applications) kept in
sync by doc_events in hooks.py. Denormalization is correctness-by-convention:
every lifecycle event must keep the mirror consistent, and a single missed hook
is silent data skew. These tests pin the whole lifecycle -- create, status
change, bank re-stamp, delete, rename -- so a dropped hook fails loudly.
"""

import unittest

import frappe

BANK_A_CODE = "TESTLKPBANKA"
BANK_B_CODE = "TESTLKPBANKB"


def _ensure_bank(code):
	bank_id = frappe.db.exists("A2C Participating Bank", {"bank_code": code})
	if not bank_id:
		doc = frappe.get_doc(
			{
				"doctype": "A2C Participating Bank",
				"registered_city": "Test City",
				"kyc_document": "/private/files/test_kyc.pdf",
				"gro_name": "Test GRO",
				"ops_name": "Test Ops",
				"bank_name": f"Test Lookup Bank {code}",
				"bank_code": code,
				"entity_type": "Bank",
				"registered_email": f"{code.lower()}@example.com",
				"registered_phone": "+251900000000",
				"registered_region": "Addis Ababa",
				"registered_country": "Ethiopia",
			}
		)
		doc.insert(ignore_permissions=True)
		return doc.name
	return bank_id


class TestLoanProductLookupSync(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		frappe.set_user("Administrator")
		cls.BANK_A_ID = _ensure_bank(BANK_A_CODE)
		cls.BANK_B_ID = _ensure_bank(BANK_B_CODE)
		frappe.db.commit()

	def setUp(self):
		frappe.set_user("Administrator")
		self._products = []

	def tearDown(self):
		for name in list(self._products):
			lookup = frappe.db.exists("A2C Loan Product Lookup", {"loan_product": name})
			if lookup:
				frappe.delete_doc("A2C Loan Product Lookup", lookup, ignore_permissions=True, force=True)
			if frappe.db.exists("A2C Loan Product", name):
				frappe.delete_doc("A2C Loan Product", name, ignore_permissions=True, force=True)
		frappe.db.commit()

	def _make_product(self, bank=None, status="Pending Approval"):
		if not bank:
			bank = self.BANK_A_ID
		doc = frappe.get_doc(
			{
				"doctype": "A2C Loan Product",
				"product_name": "Lookup Sync Test Product",
				"bank": bank,
				"min_interest_rate": 5,
				"max_interest_rate": 10,
				"max_amount": 100000,
				"tenure_months": 12,
				"status": status,
			}
		).insert(ignore_permissions=True)
		self._products.append(doc.name)
		return doc

	def _lookup(self, product_name):
		name = frappe.db.exists("A2C Loan Product Lookup", {"loan_product": product_name})
		return frappe.get_doc("A2C Loan Product Lookup", name) if name else None

	def test_lookup_created_and_stamped_on_insert(self):
		product = self._make_product(bank=self.BANK_A_ID, status="Active")
		lookup = self._lookup(product.name)
		self.assertIsNotNone(lookup, "lookup should be created when the product is inserted")
		self.assertEqual(lookup.bank, self.BANK_A_ID)
		self.assertEqual(lookup.accepting_applications, 1)

	def test_lookup_reflects_status_change(self):
		product = self._make_product(status="Pending Approval")
		self.assertEqual(self._lookup(product.name).accepting_applications, 0)

		product.status = "Active"
		product.save(ignore_permissions=True)
		self.assertEqual(self._lookup(product.name).accepting_applications, 1)

	def test_lookup_rebanks_on_bank_change(self):
		product = self._make_product(bank=self.BANK_A_ID)
		self.assertEqual(self._lookup(product.name).bank, self.BANK_A_ID)

		product.bank = self.BANK_B_ID
		product.save(ignore_permissions=True)
		self.assertEqual(
			self._lookup(product.name).bank,
			self.BANK_B_ID,
			"lookup.bank must re-stamp when the product's bank changes",
		)

	def test_lookup_deleted_on_trash(self):
		product = self._make_product()
		self.assertIsNotNone(self._lookup(product.name))

		frappe.delete_doc("A2C Loan Product", product.name, ignore_permissions=True, force=True)
		self.assertIsNone(
			self._lookup(product.name),
			"lookup must not outlive its product (on_trash handler)",
		)
		self._products.remove(product.name)

	def test_lookup_link_follows_rename(self):
		product = self._make_product()
		new_name = f"{product.name}-RENAMED"

		frappe.rename_doc("A2C Loan Product", product.name, new_name, force=True)
		self._products.remove(product.name)
		self._products.append(new_name)

		# Frappe auto-updates Link fields on rename, so the lookup should now
		# resolve via the new product name.
		self.assertIsNotNone(
			self._lookup(new_name),
			"lookup.loan_product should follow the product rename",
		)
