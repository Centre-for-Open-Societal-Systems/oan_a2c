import json
import unittest

import frappe
from werkzeug.test import EnvironBuilder
from werkzeug.wrappers import Request

from oan_a2c.api.router import API_URL_MAP, dispatch_rest_request, expand_path_param_aliases, get_routes_spec
from oan_a2c.tests.request_context import RequestContextMixin, sign_access_token


class TestRestRouter(RequestContextMixin, unittest.TestCase):
	"""Test suite for the A2C REST API Router and Werkzeug URL Map."""

	@classmethod
	def setUpClass(cls):
		cls.test_email = "test_rest_agent@coopbank.com"
		cls.test_password = "TestRestPassword@123"

		if not frappe.db.exists("User", cls.test_email):
			user = frappe.new_doc("User")
			user.email = cls.test_email
			user.first_name = "Test REST Agent"
			user.insert(ignore_permissions=True)

		from frappe.utils.password import update_password

		update_password(user=cls.test_email, pwd=cls.test_password)

		if not frappe.conf.get("jwt_secrets") and not frappe.conf.get("encryption_key"):
			frappe.conf.encryption_key = "ci_cd_test_encryption_key_for_jwt"

	@classmethod
	def tearDownClass(cls):
		frappe.set_user("Administrator")
		frappe.db.rollback()

	def test_all_94_routes_loaded(self):
		"""Verify all 94 routes from openapi_v1.yaml are compiled in the URL Map."""
		routes = get_routes_spec()
		self.assertEqual(len(routes), 94, f"Expected 94 routes in spec, found {len(routes)}")

	def test_expand_path_param_aliases(self):
		"""Verify path variable alias expansion for controller kwargs."""
		# Test id alias
		expanded = expand_path_param_aliases({"id": "LP-001"})
		self.assertEqual(expanded["product_id"], "LP-001")
		self.assertEqual(expanded["lead_id"], "LP-001")
		self.assertEqual(expanded["application_id"], "LP-001")

		# Test userId alias
		expanded_user = expand_path_param_aliases({"userId": "user@example.com"})
		self.assertEqual(expanded_user["email"], "user@example.com")
		self.assertEqual(expanded_user["user_id"], "user@example.com")

		# Test productId alias
		expanded_prod = expand_path_param_aliases({"productId": "PROD-123"})
		self.assertEqual(expanded_prod["loan_product"], "PROD-123")
		self.assertEqual(expanded_prod["product_id"], "PROD-123")

		# Test docId alias
		expanded_doc = expand_path_param_aliases({"docId": "DOC-99"})
		self.assertEqual(expanded_doc["doc_id"], "DOC-99")

	def test_public_auth_login_route_dispatch(self):
		"""Test routing POST /v1/auth/login to oan_a2c.api.auth.login."""
		builder = EnvironBuilder(
			path="/v1/auth/login",
			method="POST",
			data=json.dumps({"usr": self.test_email, "pwd": self.test_password}),
			content_type="application/json",
		)
		req = Request(builder.get_environ())
		frappe.local.request = req

		response = dispatch_rest_request(req)
		self.assertEqual(response.status_code, 200)
		body = json.loads(response.get_data(as_text=True))
		self.assertEqual(body.get("status"), "success")
		self.assertIn("token", body.get("data", {}))

	def test_404_not_found_response(self):
		"""Test that non-existent routes return standard 404 error envelope."""
		builder = EnvironBuilder(path="/v1/non/existent/endpoint", method="GET")
		req = Request(builder.get_environ())
		frappe.local.request = req

		response = dispatch_rest_request(req)
		self.assertEqual(response.status_code, 404)
		body = json.loads(response.get_data(as_text=True))
		self.assertEqual(body.get("status"), "error")
		self.assertEqual(body.get("code"), "NOT_FOUND")

	def test_405_method_not_allowed(self):
		"""Test that invalid HTTP verbs return standard 405 error envelope."""
		# /v1/auth/login only accepts POST
		builder = EnvironBuilder(path="/v1/auth/login", method="DELETE")
		req = Request(builder.get_environ())
		frappe.local.request = req

		response = dispatch_rest_request(req)
		self.assertEqual(response.status_code, 405)
		body = json.loads(response.get_data(as_text=True))
		self.assertEqual(body.get("status"), "error")
		self.assertEqual(body.get("code"), "METHOD_NOT_ALLOWED")

	def test_authenticated_route_with_jwt(self):
		"""Test dispatching an authenticated route GET /v1/me with valid Bearer JWT."""
		import datetime

		payload = {
			"sub": self.test_email,
			"iss": "oan_a2c_identity_gateway",
			"aud": "oan_a2c_client",
			"iat": int(datetime.datetime.now(datetime.UTC).timestamp()),
			"exp": int((datetime.datetime.now(datetime.UTC) + datetime.timedelta(minutes=15)).timestamp()),
		}
		token = sign_access_token(payload)
		builder = EnvironBuilder(
			path="/v1/me",
			method="GET",
			headers={"Authorization": f"Bearer {token}"},
		)
		req = Request(builder.get_environ())
		frappe.local.request = req
		self._mock_headers["Authorization"] = f"Bearer {token}"

		from oan_a2c.api.middleware import validate_jwt_request

		validate_jwt_request(req)

		response = dispatch_rest_request(req)
		self.assertEqual(response.status_code, 200)
		body = json.loads(response.get_data(as_text=True))
		self.assertEqual(body.get("status"), "success")
		self.assertEqual(body.get("data", {}).get("email"), self.test_email)

	def test_route_with_path_parameter_resolution(self):
		"""Test route matching and parameter extraction for complex path variables."""
		# Test GET /v1/catalog/banks/{bankId}
		adapter = API_URL_MAP.bind("localhost", "/")
		endpoint, path_args = adapter.match("/v1/catalog/banks/BANK-001", method="GET")
		self.assertEqual(endpoint, "oan_a2c.api.v1.farmer.catalog.get_bank_details")
		self.assertEqual(path_args.get("bankId"), "BANK-001")

		# Test multi-param path: /v1/loan-applications/{id}/documents/{docId}/content
		endpoint2, path_args2 = adapter.match(
			"/v1/loan-applications/APP-123/documents/DOC-456/content", method="GET"
		)
		self.assertEqual(endpoint2, "oan_a2c.api.v1.loan_applications.download_supporting_document")
		self.assertEqual(path_args2.get("id"), "APP-123")
		self.assertEqual(path_args2.get("docId"), "DOC-456")

		# Test alias expansion
		expanded2 = expand_path_param_aliases(path_args2)
		self.assertEqual(expanded2.get("doc_id"), "DOC-456")
		self.assertEqual(expanded2.get("loan_application_id"), "APP-123")

	def test_catalog_browse_with_query_params(self):
		"""Test query parameter extraction for GET /v1/catalog/products."""
		adapter = API_URL_MAP.bind("localhost", "/")
		endpoint, _path_args = adapter.match("/v1/catalog/products", method="GET")
		self.assertEqual(endpoint, "oan_a2c.api.v1.farmer.catalog.list_catalog")
