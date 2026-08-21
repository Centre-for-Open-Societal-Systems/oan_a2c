"""Loan terms on a farmer's own applications.

The card in "My Applications" shows Interest p.a and Tenure next to the amount.
Neither was ever returned by list_applications, so the client filled those slots
with whatever else it had -- the bank's name and a second copy of the status.
The endpoint now carries the terms the application was actually made under, and
these tests hold that contract: snapshotted on create, returned on read, and left
null (never 0) when an older application has none.

Fixtures are suffixed per run and torn down in tearDownClass; see
docs/refactor-test-isolation.md.
"""

import random
import unittest

import frappe

from oan_a2c.a2c_marketplace.roles import FARMER_ROLE
from oan_a2c.api.v1.farmer.applications import (
	create_application,
	get_application,
	list_applications,
)

SUFFIX = frappe.generate_hash(length=6)
BANK_CODE = f"APPTERMS_{SUFFIX}"
FARMER_EMAIL = f"appterms_farmer_{SUFFIX}@example.com"

PRODUCT_RATE = 8.75
PRODUCT_TENURE = 18


class TestFarmerApplicationTerms(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		frappe.set_user("Administrator")
		num = str(random.randint(100000, 999999))

		cls.bank = frappe.get_doc(
			{
				"doctype": "A2C Participating Bank",
				"bank_name": f"App Terms Bank {SUFFIX}",
				"bank_code": BANK_CODE,
				"status": "Active",
				"entity_type": "Commercial Bank",
				"registered_email": f"appterms_{SUFFIX}@test.com",
				"registered_phone": "+251911000000",
				"registered_city": "Test City",
				"registered_region": "Addis Ababa",
				"registered_country": "Ethiopia",
				"kyc_document": "/private/files/test_kyc.pdf",
				"gro_name": "Test GRO",
				"ops_name": "Test Ops",
			}
		).insert(ignore_permissions=True)

		# max_interest_rate is deliberately different from min: the snapshot must
		# take the headline (min) rate the catalog card advertises, not the ceiling.
		cls.product = frappe.get_doc(
			{
				"doctype": "A2C Loan Product",
				"product_name": f"App Terms Product {SUFFIX}",
				"bank": cls.bank.name,
				"status": "Active",
				"min_amount": 1_000,
				"max_amount": 100_000,
				"min_interest_rate": PRODUCT_RATE,
				"max_interest_rate": 12.5,
				"tenure_months": PRODUCT_TENURE,
			}
		).insert(ignore_permissions=True)

		frappe.get_doc(
			{
				"doctype": "User",
				"email": FARMER_EMAIL,
				"first_name": "AppTerms",
				"last_name": "Farmer",
				"mobile_no": f"+25191{num}0",
				"send_welcome_email": 0,
			}
		).insert(ignore_permissions=True)
		frappe.get_doc("User", FARMER_EMAIL).add_roles(FARMER_ROLE)

		cls.profile = frappe.get_doc(
			{
				"doctype": "A2C Farmer Profile",
				"first_name": "AppTerms",
				"last_name": "Farmer",
				"user": FARMER_EMAIL,
			}
		).insert(ignore_permissions=True)

		frappe.db.commit()

	@classmethod
	def tearDownClass(cls):
		frappe.set_user("Administrator")
		for app in frappe.get_all(
			"A2C Loan Application", filters={"farmer_profile": cls.profile.name}, pluck="name"
		):
			frappe.delete_doc("A2C Loan Application", app, force=True, ignore_permissions=True)
		for lead in frappe.get_all("A2C Lead", filters={"owner": FARMER_EMAIL}, pluck="name"):
			frappe.delete_doc("A2C Lead", lead, force=True, ignore_permissions=True)
		frappe.delete_doc("A2C Farmer Profile", cls.profile.name, force=True, ignore_permissions=True)
		frappe.delete_doc("User", FARMER_EMAIL, force=True, ignore_permissions=True)
		frappe.delete_doc("A2C Loan Product", cls.product.name, force=True, ignore_permissions=True)
		frappe.delete_doc("A2C Participating Bank", cls.bank.name, force=True, ignore_permissions=True)
		frappe.db.commit()

	def setUp(self):
		frappe.set_user(FARMER_EMAIL)

	def tearDown(self):
		frappe.set_user("Administrator")

	def _create(self, amount=5_000):
		response = create_application(loan_product=self.product.name, requested_amount=amount)
		self.assertEqual(response["status"], "success", response)
		return response["data"]["application_id"]

	def _row(self, application_id):
		response = list_applications()
		self.assertEqual(response["status"], "success", response)
		rows = [r for r in response["data"] if r["application_id"] == application_id]
		self.assertEqual(len(rows), 1, response["data"])
		return rows[0]

	def test_create_snapshots_the_products_headline_terms(self):
		doc = frappe.get_doc("A2C Loan Application", self._create())

		self.assertEqual(float(doc.interest_rate), PRODUCT_RATE)
		self.assertEqual(int(doc.tenure_months), PRODUCT_TENURE)

	def test_list_returns_the_terms_the_card_displays(self):
		row = self._row(self._create())

		self.assertEqual(row["interest_rate"], PRODUCT_RATE)
		self.assertEqual(row["tenure_months"], PRODUCT_TENURE)

	def test_get_application_returns_the_same_terms(self):
		application_id = self._create()

		response = get_application(application_id=application_id)

		self.assertEqual(response["status"], "success", response)
		self.assertEqual(response["data"]["interest_rate"], PRODUCT_RATE)
		self.assertEqual(response["data"]["tenure_months"], PRODUCT_TENURE)

	def test_terms_stay_at_what_was_offered_when_the_product_changes(self):
		"""The whole point of a snapshot: a repriced product must not restate an
		application the farmer already made in the new numbers."""
		application_id = self._create()

		# Written straight to the row: the controller refuses to edit an Active
		# product at all, which is a stronger guarantee than this test needs. What
		# is being proved here is only that the read does not join back to the
		# product, so the value has to differ by whatever means.
		frappe.set_user("Administrator")
		frappe.db.set_value(
			"A2C Loan Product",
			self.product.name,
			{"min_interest_rate": PRODUCT_RATE + 3, "tenure_months": PRODUCT_TENURE + 12},
		)
		frappe.db.commit()
		try:
			frappe.set_user(FARMER_EMAIL)
			row = self._row(application_id)

			self.assertEqual(row["interest_rate"], PRODUCT_RATE)
			self.assertEqual(row["tenure_months"], PRODUCT_TENURE)
		finally:
			frappe.set_user("Administrator")
			frappe.db.set_value(
				"A2C Loan Product",
				self.product.name,
				{"min_interest_rate": PRODUCT_RATE, "tenure_months": PRODUCT_TENURE},
			)
			frappe.db.commit()

	def test_missing_terms_read_as_null_not_zero(self):
		"""Applications predating the snapshot have no terms. A 0 here would render
		as a real "0%" offer on the card, which is worse than an honest blank."""
		application_id = self._create()

		frappe.set_user("Administrator")
		frappe.db.set_value(
			"A2C Loan Application", application_id, {"interest_rate": 0, "tenure_months": 0}
		)
		frappe.db.commit()
		frappe.set_user(FARMER_EMAIL)

		row = self._row(application_id)

		self.assertIsNone(row["interest_rate"])
		self.assertIsNone(row["tenure_months"])
