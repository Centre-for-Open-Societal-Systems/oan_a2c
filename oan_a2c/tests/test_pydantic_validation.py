import unittest

import frappe
from pydantic import BaseModel, Field

from oan_a2c.api.utils import handle_api_errors, validate_request


# Simple Schema for testing
class DummySchema(BaseModel):
	name: str = Field(min_length=3)
	age: int = Field(ge=18)
	is_active: bool = True


@validate_request(DummySchema)
@handle_api_errors
def dummy_endpoint(name, age, is_active=True):
	return {"name": name, "age": age, "is_active": is_active}


@validate_request(DummySchema)
@handle_api_errors
def dummy_kwargs_endpoint(**kwargs):
	return kwargs


class TestPydanticValidation(unittest.TestCase):
	def setUp(self):
		# Reset response and message log state
		frappe.response["http_status_code"] = 200
		frappe.local.message_log = []

	def test_validation_success_and_coercion(self):
		# Pass age as string to verify coercion
		res = dummy_endpoint(name="Alice", age="25", is_active="false")

		# Verify the wrapper parsed the result and succeeded
		self.assertEqual(res.get("status"), "success")
		self.assertEqual(res["data"]["name"], "Alice")
		self.assertEqual(res["data"]["age"], 25)  # Coerced to int
		self.assertEqual(res["data"]["is_active"], False)  # Coerced to bool

	def test_validation_failure_too_short_name(self):
		res = dummy_endpoint(name="Al", age=25)

		self.assertEqual(res.get("status"), "error")
		self.assertEqual(res.get("code"), "VALIDATION_ERROR")
		self.assertEqual(frappe.response.get("http_status_code"), 400)
		self.assertIn("name", res.get("details", {}))
		self.assertIn("String should have at least 3 characters", res["details"]["name"])

	def test_validation_failure_underage(self):
		res = dummy_endpoint(name="Alice", age=17)

		self.assertEqual(res.get("status"), "error")
		self.assertEqual(res.get("code"), "VALIDATION_ERROR")
		self.assertEqual(frappe.response.get("http_status_code"), 400)
		self.assertIn("age", res.get("details", {}))
		self.assertIn("Input should be greater than or equal to 18", res["details"]["age"])

	def test_validation_missing_required_fields(self):
		# Missing name and age
		res = dummy_endpoint()

		self.assertEqual(res.get("status"), "error")
		self.assertEqual(res.get("code"), "VALIDATION_ERROR")
		self.assertIn("name", res.get("details", {}))
		self.assertIn("age", res.get("details", {}))

	def test_validation_kwargs_signature(self):
		res = dummy_kwargs_endpoint(name="Bob", age=30, is_active=True)
		self.assertEqual(res.get("status"), "success")
		self.assertEqual(res["data"]["name"], "Bob")
		self.assertEqual(res["data"]["age"], 30)
		self.assertEqual(res["data"]["is_active"], True)

	def test_create_product_schema_validation(self):
		from pydantic import ValidationError

		from oan_a2c.api.v1.seller.loan_products import CreateProductSchema

		# Valid schema
		valid = CreateProductSchema(
			product_name="Test Loan",
			min_interest_rate=5.0,
			max_interest_rate=12.0,
			min_amount=1000.0,
			max_amount=50000.0,
			tenure_months=12,
		)
		self.assertEqual(valid.product_name, "Test Loan")

		# Negative interest rate
		with self.assertRaises(ValidationError) as ctx:
			CreateProductSchema(
				product_name="Test Loan",
				min_interest_rate=-1.0,
				max_amount=5000.0,
				tenure_months=12,
			)
		self.assertIn("min_interest_rate", str(ctx.exception))

		# min_interest_rate > max_interest_rate
		with self.assertRaises(ValidationError) as ctx:
			CreateProductSchema(
				product_name="Test Loan",
				min_interest_rate=15.0,
				max_interest_rate=10.0,
				max_amount=5000.0,
				tenure_months=12,
			)
		self.assertIn("min_interest_rate cannot be greater than max_interest_rate", str(ctx.exception))

		# min_amount > max_amount
		with self.assertRaises(ValidationError) as ctx:
			CreateProductSchema(
				product_name="Test Loan",
				min_interest_rate=5.0,
				min_amount=100000.0,
				max_amount=5000.0,
				tenure_months=12,
			)
		self.assertIn("min_amount cannot be greater than max_amount", str(ctx.exception))

		# interest rate > 20% capped
		with self.assertRaises(ValidationError) as ctx:
			CreateProductSchema(
				product_name="Test Loan",
				min_interest_rate=21.0,
				max_amount=5000.0,
				tenure_months=12,
			)
		self.assertIn("min_interest_rate", str(ctx.exception))

		# amount > 6 digits (> 999,999.99) capped
		with self.assertRaises(ValidationError) as ctx:
			CreateProductSchema(
				product_name="Test Loan",
				min_interest_rate=5.0,
				max_amount=1000000.0,
				tenure_months=12,
			)
		self.assertIn("max_amount", str(ctx.exception))

		# interest rate with > 2 decimal places
		with self.assertRaises(ValidationError) as ctx:
			CreateProductSchema(
				product_name="Test Loan",
				min_interest_rate=15.123,
				max_amount=5000.0,
				tenure_months=12,
			)
		self.assertIn("min_interest_rate", str(ctx.exception))

	def test_create_lead_schema_max_length(self):
		from pydantic import ValidationError

		from oan_a2c.api.v1.leads import CreateLeadSchema

		# Excessively long name > 140 chars
		long_name = "A" * 141
		with self.assertRaises(ValidationError) as ctx:
			CreateLeadSchema(phone_number="+251911223344", first_name=long_name)
		self.assertIn("first_name", str(ctx.exception))

	def test_register_bank_schema_entity_type(self):
		from pydantic import ValidationError

		from oan_a2c.api.v1.seller.onboarding import RegisterBankSchema

		# Single char entity_type rejected
		with self.assertRaises(ValidationError) as ctx:
			RegisterBankSchema(
				bank_name="Test Bank",
				bank_code="TB01",
				entity_type="X",
				registered_street="Street 1",
				registered_region="Addis",
				registered_country="Ethiopia",
				registered_postal_code="1000",
				registered_email="test@bank.com",
				registered_phone="+251911223344",
			)
		self.assertIn("entity_type", str(ctx.exception))

	def test_filter_amount_ordering(self):
		from pydantic import ValidationError

		from oan_a2c.api.v1.loan_applications import BrowseProductsSchema, GetAllLoansSchema

		# GetAllLoansSchema min_loan_amount > max_loan_amount
		with self.assertRaises(ValidationError) as ctx:
			GetAllLoansSchema(min_loan_amount=50000.0, max_loan_amount=10000.0)
		self.assertIn("min_loan_amount cannot be greater than max_loan_amount", str(ctx.exception))

		# BrowseProductsSchema min_amount > max_amount
		with self.assertRaises(ValidationError) as ctx:
			BrowseProductsSchema(min_amount=50000.0, max_amount=1000.0)
		self.assertIn("min_amount cannot be greater than max_amount", str(ctx.exception))

	def test_active_product_edit_blocked(self):
		from oan_a2c.a2c_marketplace.doctype.a2c_loan_product.a2c_loan_product import A2CLoanProduct

		doc = A2CLoanProduct(
			{
				"doctype": "A2C Loan Product",
				"product_name": "Active Loan",
				"min_interest_rate": 10.0,
				"status": "Active",
			}
		)
		doc.is_new = lambda: False

		before_doc = frappe._dict(
			{
				"status": "Active",
				"min_interest_rate": 10.0,
				"product_name": "Active Loan",
			}
		)
		doc.get_doc_before_save = lambda: before_doc

		# Changing min_interest_rate from 10.0 -> 12.0 on an Active product
		doc.min_interest_rate = 12.0

		with self.assertRaises(frappe.PermissionError):
			doc.before_save()
