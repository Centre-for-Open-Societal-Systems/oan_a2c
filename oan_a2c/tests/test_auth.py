import frappe
import unittest
import jwt
import datetime
from oan_a2c.api.loan_app_api import login, forgot_password, reset_password, _validate_jwt as validate_jwt_request


class TestAuthAPI(unittest.TestCase):
	"""
	Unit Tests for Identity and Access Management (IAM) endpoints.
	Ensures strict adherence to our NSPF and No-Hack mandates.

	Response shape note: @frappe.whitelist() envelopes the return value in
	{"message": <return_value>} on the wire. These tests call the Python
	functions directly, so they receive the inner dict — no outer "message" key.
	"""

	@classmethod
	def setUpClass(cls):
		cls.test_email = "test_agent@coopbank.com"
		cls.test_password = "test_agent@1234"

		if not frappe.db.exists("User", cls.test_email):
			user = frappe.new_doc("User")
			user.email = cls.test_email
			user.first_name = "Test Agent"
			user.insert(ignore_permissions=True)

			from frappe.core.doctype.user.user import update_password
			update_password(new_password=cls.test_password, user=cls.test_email)

		# Ensure a mock encryption key is present in isolated CI/CD environments
		if not frappe.conf.get("encryption_key"):
			frappe.conf.encryption_key = "ci_cd_test_encryption_key_for_jwt"

	@classmethod
	def tearDownClass(cls):
		frappe.set_user("Administrator")
		frappe.db.rollback()

	def setUp(self):
		frappe.local.response = {}
		frappe.set_user("Administrator")

		# frappe.local.request_ip is normally set by HTTPRequest.set_request_ip() during
		# the web request cycle. In unit tests HTTPRequest is never instantiated, so the
		# value stays None. LoginAttemptTracker uses it as its Redis hash key — passing
		# None causes Redis to reject the HDEL call with a DataError.
		frappe.local.request_ip = "127.0.0.1"

		# Mock request for LoginManager and middleware
		self._original_request = getattr(frappe.local, "request", None)
		frappe.local.request = frappe._dict({
			"path": "",
			"headers": {},
			"cookies": frappe._dict(),
			"scheme": "http",
			"remote_addr": "127.0.0.1"
		})

		# Mock CookieManager for LoginManager
		from frappe.auth import CookieManager
		self._original_cookie_manager = getattr(frappe.local, "cookie_manager", None)
		frappe.local.cookie_manager = CookieManager()

		# Patch get_request_header for middleware tests
		self._original_get_request_header = getattr(frappe, "get_request_header", None)
		frappe.get_request_header = self._mock_get_request_header
		self._mock_headers = {}

	def tearDown(self):
		frappe.get_request_header = self._original_get_request_header
		
		# Restore original request
		if self._original_request:
			frappe.local.request = self._original_request
		else:
			if hasattr(frappe.local, "request"):
				delattr(frappe.local, "request")
		
		# Restore original cookie_manager
		if self._original_cookie_manager:
			frappe.local.cookie_manager = self._original_cookie_manager
		else:
			if hasattr(frappe.local, "cookie_manager"):
				delattr(frappe.local, "cookie_manager")

	def _mock_get_request_header(self, key):
		return self._mock_headers.get(key)

	# ------------------------------------------------------------------
	# Auth endpoint tests
	# ------------------------------------------------------------------

	def test_1_login_success(self):
		response = login(self.test_email, self.test_password)

		# Function returns the inner dict; Frappe adds the outer envelope on the wire
		self.assertEqual(response.get("status"), "success")
		self.assertIn("token", response)

		token = response["token"]
		payload = jwt.decode(token, frappe.conf.encryption_key, algorithms=["HS256"])
		self.assertEqual(payload["sub"], self.test_email)
		self.assertEqual(payload["iss"], "oan_a2c_identity_gateway")

		# Confirm user block is present with the bank field
		user_block = response.get("user", {})
		self.assertEqual(user_block.get("email"), self.test_email)
		self.assertIn("bank", user_block)

	def test_2_login_failure(self):
		response = login(self.test_email, "WrongPassword999")

		self.assertEqual(frappe.local.response.get("http_status_code"), 401)
		self.assertEqual(response.get("exception"), "frappe.exceptions.AuthenticationError")

	def test_3_middleware_valid_jwt(self):
		payload = {
			"sub": self.test_email,
			"exp": datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=1)
		}
		token = jwt.encode(payload, frappe.conf.encryption_key, algorithm="HS256")

		# Patch frappe.local.request — this is what middleware.py reads
		frappe.local.request = frappe._dict({"path": "/api/method/oan_a2c.api.v1.get_leads"})
		self._mock_headers["Authorization"] = f"Bearer {token}"

		validate_jwt_request()

		self.assertEqual(frappe.session.user, self.test_email)

	def test_4_middleware_missing_header(self):
		frappe.local.request = frappe._dict({"path": "/api/method/oan_a2c.api.v1.get_leads"})
		self._mock_headers = {}

		with self.assertRaises(frappe.AuthenticationError):
			validate_jwt_request()

	def test_5_middleware_expired_jwt(self):
		payload = {
			"sub": self.test_email,
			# Already expired 1 hour ago
			"exp": datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=1)
		}
		token = jwt.encode(payload, frappe.conf.encryption_key, algorithm="HS256")

		frappe.local.request = frappe._dict({"path": "/api/method/oan_a2c.api.v1.get_leads"})
		self._mock_headers["Authorization"] = f"Bearer {token}"

		with self.assertRaises(frappe.AuthenticationError):
			validate_jwt_request()

	def test_6_forgot_password(self):
		response = forgot_password(self.test_email)

		self.assertEqual(response.get("status"), "success")

	def test_7_middleware_bypasses_public_endpoints(self):
		"""Auth endpoints must not require a JWT — they serve unauthenticated agents."""
		for path in [
			"/api/method/oan_a2c.api.auth.login",
			"/api/method/oan_a2c.api.auth.forgot_password",
			"/api/method/oan_a2c.api.auth.reset_password",
		]:
			frappe.local.request = frappe._dict({"path": path})
			self._mock_headers = {}  # No token

			# Should return None (early exit) without raising
			result = validate_jwt_request()
			self.assertIsNone(result, f"Middleware should bypass {path} without a token")
