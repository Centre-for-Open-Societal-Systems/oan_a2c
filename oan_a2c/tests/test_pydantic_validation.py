import unittest
import frappe
from pydantic import BaseModel, Field
from oan_a2c.api.utils import validate_request, handle_api_errors

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
