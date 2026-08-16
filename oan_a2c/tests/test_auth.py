import datetime
import hashlib
import unittest

import frappe
import jwt

from oan_a2c.api.auth import (
	change_password,
	forgot_password,
	generate_refresh_token,
	login,
	logout,
	refresh,
	reset_password,
	set_initial_password,
)
from oan_a2c.api.middleware import JWTUnauthorized, validate_jwt_request


class _RequestContextMixin:
	"""Stands up the request-scoped globals the auth code reaches for.

	LoginManager, the JWT middleware and the rate limiter all read
	frappe.local.request / cookie_manager / request_ip, which only exist during a
	real web request. Every auth test class needs the same scaffolding, so it
	lives here rather than being copied per class.
	"""

	def setUp(self):
		super().setUp()
		# _dict, not {} — frappe.local.response is a _dict at runtime and other test
		# modules set attributes on it (frappe.local.response.type = ...). A plain
		# dict here survives the module boundary and breaks them with an
		# AttributeError that looks like their own bug.
		frappe.local.response = frappe._dict()
		frappe.set_user("Administrator")

		# frappe.local.request_ip is normally set by HTTPRequest.set_request_ip() during
		# the web request cycle. In unit tests HTTPRequest is never instantiated, so the
		# value stays None. LoginAttemptTracker uses it as its Redis hash key — passing
		# None causes Redis to reject the HDEL call with a DataError.
		frappe.local.request_ip = "127.0.0.1"

		# Mock request for LoginManager and middleware
		self._original_request = getattr(frappe.local, "request", None)
		frappe.local.request = frappe._dict(
			{
				"path": "",
				"headers": {},
				"cookies": frappe._dict(),
				"scheme": "http",
				"remote_addr": "127.0.0.1",
			}
		)

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

		super().tearDown()

	def _mock_get_request_header(self, key):
		return self._mock_headers.get(key)


class TestAuthAPI(_RequestContextMixin, unittest.TestCase):
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

		from frappe.utils.password import update_password

		update_password(user=cls.test_email, pwd=cls.test_password)

		# Ensure a mock encryption key is present in isolated CI/CD environments
		if not frappe.conf.get("encryption_key"):
			frappe.conf.encryption_key = "ci_cd_test_encryption_key_for_jwt"

	@classmethod
	def tearDownClass(cls):
		frappe.set_user("Administrator")
		frappe.db.rollback()

	# ------------------------------------------------------------------
	# Auth endpoint tests
	# ------------------------------------------------------------------

	def test_1_login_success(self):
		response = login(self.test_email, self.test_password)

		# Function returns the inner dict; Frappe adds the outer envelope on the wire
		self.assertEqual(response.get("status"), "success")
		self.assertIn("token", response.get("data", {}))

		token = response["data"]["token"]
		payload = jwt.decode(
			token,
			frappe.conf.encryption_key,
			algorithms=["HS256"],
			audience="oan_a2c_client",
			issuer="oan_a2c_identity_gateway",
		)
		self.assertEqual(payload["sub"], self.test_email)
		self.assertEqual(payload["iss"], "oan_a2c_identity_gateway")

		# Confirm user block is present with the bank field
		user_block = response.get("data", {}).get("user", {})
		self.assertEqual(user_block.get("email"), self.test_email)
		self.assertIn("bank", user_block)

	def test_2_login_failure(self):
		response = login(self.test_email, "WrongPassword999")

		self.assertEqual(frappe.local.response.get("http_status_code"), 401)
		self.assertEqual(response.get("code"), "AUTHENTICATION_ERROR")

	def test_3_middleware_valid_jwt(self):
		payload = {
			"sub": self.test_email,
			"iss": "oan_a2c_identity_gateway",
			"aud": "oan_a2c_client",
			"exp": datetime.datetime.now(datetime.UTC) + datetime.timedelta(hours=1),
		}
		token = jwt.encode(payload, frappe.conf.encryption_key, algorithm="HS256", headers={"kid": "v1"})

		# Patch frappe.local.request — this is what middleware.py reads
		frappe.local.request = frappe._dict({"path": "/api/method/oan_a2c.api.v1.get_leads"})
		self._mock_headers["Authorization"] = f"Bearer {token}"

		validate_jwt_request()

		self.assertEqual(frappe.session.user, self.test_email)

	def test_4_middleware_missing_header(self):
		frappe.local.request = frappe._dict({"path": "/api/method/oan_a2c.api.v1.get_leads"})
		self._mock_headers = {}

		with self.assertRaises(JWTUnauthorized):
			validate_jwt_request()

	def test_5_middleware_expired_jwt(self):
		payload = {
			"sub": self.test_email,
			# Already expired 1 hour ago
			"exp": datetime.datetime.now(datetime.UTC) - datetime.timedelta(hours=1),
		}
		token = jwt.encode(payload, frappe.conf.encryption_key, algorithm="HS256", headers={"kid": "v1"})

		frappe.local.request = frappe._dict({"path": "/api/method/oan_a2c.api.v1.get_leads"})
		self._mock_headers["Authorization"] = f"Bearer {token}"

		with self.assertRaises(JWTUnauthorized):
			validate_jwt_request()

	def test_6_forgot_password(self):
		frappe.cache().delete_value("rl:forgot_pwd:127.0.0.1")
		response = forgot_password(self.test_email)

		self.assertEqual(response.get("status"), "success")

		# The OTP must never travel back to the caller. While it did, anyone could
		# POST an address here, read the key out of the response and take the
		# account over through reset_password.
		self.assertIsNone(response.get("data"))

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

	def test_8_middleware_invalid_kid(self):
		payload = {
			"sub": self.test_email,
			"exp": datetime.datetime.now(datetime.UTC) + datetime.timedelta(hours=1),
		}
		# Token with invalid kid
		token_invalid_kid = jwt.encode(
			payload, frappe.conf.encryption_key, algorithm="HS256", headers={"kid": "v2"}
		)
		frappe.local.request = frappe._dict({"path": "/api/method/oan_a2c.api.v1.get_leads"})
		self._mock_headers["Authorization"] = f"Bearer {token_invalid_kid}"
		with self.assertRaises(JWTUnauthorized) as context:
			validate_jwt_request()
		self.assertIn("Invalid or missing Key ID", context.exception.message)

		# Token with missing kid
		token_missing_kid = jwt.encode(payload, frappe.conf.encryption_key, algorithm="HS256")
		self._mock_headers["Authorization"] = f"Bearer {token_missing_kid}"
		with self.assertRaises(JWTUnauthorized) as context:
			validate_jwt_request()
		self.assertIn("Invalid or missing Key ID", context.exception.message)

	def test_9_middleware_disabled_user(self):
		payload = {
			"sub": self.test_email,
			"exp": datetime.datetime.now(datetime.UTC) + datetime.timedelta(hours=1),
			"iss": "oan_a2c_identity_gateway",
			"aud": "oan_a2c_client",
		}
		token = jwt.encode(payload, frappe.conf.encryption_key, algorithm="HS256", headers={"kid": "v1"})
		frappe.local.request = frappe._dict({"path": "/api/method/oan_a2c.api.v1.get_leads"})
		self._mock_headers["Authorization"] = f"Bearer {token}"

		# Disable user temporarily
		frappe.db.set_value("User", self.test_email, "enabled", 0)
		frappe.db.commit()

		try:
			with self.assertRaises(JWTUnauthorized) as context:
				validate_jwt_request()
			self.assertIn("User is disabled", context.exception.message)
		finally:
			# Restore user
			frappe.db.set_value("User", self.test_email, "enabled", 1)
			frappe.db.commit()

	def test_10_get_me(self):
		from oan_a2c.api.auth import get_me

		# 1. Guest request should fail
		frappe.set_user("Guest")
		response = get_me()
		self.assertEqual(response.get("status"), "error")
		self.assertEqual(response.get("code"), "AUTHENTICATION_ERROR")

		# 2. Authenticated request should succeed
		frappe.set_user(self.test_email)
		response = get_me()
		self.assertEqual(response.get("status"), "success")
		user_data = response.get("data", {})
		self.assertEqual(user_data.get("email"), self.test_email)
		self.assertEqual(user_data.get("full_name"), "Test Agent")
		self.assertIn("roles", user_data)
		self.assertIn("bank", user_data)

	def test_11_refresh_token_rotation_success(self):
		response = login(self.test_email, self.test_password, remember_me=True)
		self.assertEqual(response.get("status"), "success")
		data = response.get("data", {})
		self.assertIn("token", data)
		self.assertIn("refresh_token", data)

		old_refresh_token = data["refresh_token"]
		import hashlib

		old_hash = hashlib.sha256(old_refresh_token.encode("utf-8")).hexdigest()

		# Verify token document was created
		self.assertTrue(frappe.db.exists("A2C User Refresh Token", {"token_hash": old_hash}))

		# Refresh
		refresh_response = refresh(old_refresh_token)
		self.assertEqual(refresh_response.get("status"), "success")
		refresh_data = refresh_response.get("data", {})
		self.assertIn("token", refresh_data)
		self.assertIn("refresh_token", refresh_data)

		new_refresh_token = refresh_data["refresh_token"]
		new_hash = hashlib.sha256(new_refresh_token.encode("utf-8")).hexdigest()

		# Old token should be deleted (RTR), new token should exist
		self.assertFalse(frappe.db.exists("A2C User Refresh Token", {"token_hash": old_hash}))
		self.assertTrue(frappe.db.exists("A2C User Refresh Token", {"token_hash": new_hash}))

	def test_12_refresh_token_expired_or_invalid(self):
		# Invalid token
		response = refresh("some_invalid_token_random")
		self.assertEqual(response.get("status"), "error")
		self.assertEqual(response.get("code"), "AUTHENTICATION_ERROR")
		self.assertIn("Invalid or expired", response.get("message"))

		# Expired token
		import hashlib

		from frappe.utils import add_days, now_datetime

		raw_token = frappe.generate_hash(length=40)
		token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()

		# Create an expired token record in db
		token_doc = frappe.get_doc(
			{
				"doctype": "A2C User Refresh Token",
				"user": self.test_email,
				"token_hash": token_hash,
				"expiry": add_days(now_datetime(), -2),  # 2 days in the past
				"remember_me": 1,
			}
		)
		token_doc.insert(ignore_permissions=True)
		frappe.db.commit()

		self.assertTrue(frappe.db.exists("A2C User Refresh Token", {"token_hash": token_hash}))

		# Try to refresh using it
		response = refresh(raw_token)
		self.assertEqual(response.get("status"), "error")
		self.assertEqual(response.get("code"), "AUTHENTICATION_ERROR")
		self.assertIn("expired", response.get("message"))

		# Verify it got deleted upon detection
		self.assertFalse(frappe.db.exists("A2C User Refresh Token", {"token_hash": token_hash}))

	def test_13_logout_success(self):
		response = login(self.test_email, self.test_password)
		data = response.get("data", {})
		refresh_token = data["refresh_token"]

		import hashlib

		token_hash = hashlib.sha256(refresh_token.encode("utf-8")).hexdigest()
		self.assertTrue(frappe.db.exists("A2C User Refresh Token", {"token_hash": token_hash}))

		# Call logout
		logout_response = logout(refresh_token)
		self.assertEqual(logout_response.get("status"), "success")

		# Verify token is deleted
		self.assertFalse(frappe.db.exists("A2C User Refresh Token", {"token_hash": token_hash}))

	def test_14_change_password(self):
		frappe.set_user(self.test_email)

		# 1. Invalid current password
		bad_resp = change_password(
			current_password="WrongCurrentPassword123!", new_password="NewPassword123!"
		)
		self.assertEqual(bad_resp.get("status"), "error")
		self.assertEqual(bad_resp.get("code"), "AUTHENTICATION_ERROR")
		self.assertIn("Current password is incorrect", bad_resp.get("message"))

		# 2. Valid current password -> change success
		new_pwd = "NewValidPassword123!"
		good_resp = change_password(current_password=self.test_password, new_password=new_pwd)
		self.assertEqual(good_resp.get("status"), "success")

		# 3. Restore original password using change_password
		restore_resp = change_password(current_password=new_pwd, new_password=self.test_password)
		self.assertEqual(restore_resp.get("status"), "success")


class TestMustChangePassword(_RequestContextMixin, unittest.TestCase):
	"""An admin-issued temporary password authenticates but opens no session.

	This is the whole point of the flag: a Bank Admin knows the password they
	typed for an agent, so it must not be usable as a working credential — only
	as proof of identity for the one call that replaces it.
	"""

	@classmethod
	def setUpClass(cls):
		frappe.set_user("Administrator")
		suffix = frappe.generate_hash(length=6)

		cls.email = f"mcp_agent_{suffix}@oan.test"
		cls.temp_password = f"TempIssued{suffix}1!"
		cls.new_password = f"AgentChosen{suffix}9#"

		frappe.get_doc(
			{
				"doctype": "User",
				"email": cls.email,
				"first_name": "MCP Agent",
				"send_welcome_email": 0,
			}
		).insert(ignore_permissions=True)

		from frappe.utils.password import update_password

		update_password(user=cls.email, pwd=cls.temp_password)

		# Ensure a mock encryption key is present in isolated CI/CD environments
		if not frappe.conf.get("encryption_key"):
			frappe.conf.encryption_key = "ci_cd_test_encryption_key_for_jwt"

	@classmethod
	def tearDownClass(cls):
		frappe.set_user("Administrator")
		frappe.db.delete("A2C User Refresh Token", {"user": cls.email})
		frappe.delete_doc("User", cls.email, force=True, ignore_permissions=True)

	def setUp(self):
		super().setUp()
		# Every test starts from "just invited": flagged, holding the temp password.
		self._set_flag(1)
		self._restore_temp_password()
		# The endpoints under test are rate-limited per IP; without this the fifth
		# test in the class would fail on a 429 rather than on its own assertion.
		frappe.cache().delete_value("rl:set_initial_pwd:127.0.0.1")
		frappe.cache().delete_value("rl:login:127.0.0.1")

	def _set_flag(self, value: int):
		frappe.db.set_value("User", self.email, "a2c_must_change_password", value)

	def _restore_temp_password(self):
		from frappe.utils.password import update_password

		update_password(user=self.email, pwd=self.temp_password)

	def _jwt_for_user(self) -> str:
		payload = {
			"sub": self.email,
			"iss": "oan_a2c_identity_gateway",
			"aud": "oan_a2c_client",
			"exp": datetime.datetime.now(datetime.UTC) + datetime.timedelta(hours=1),
		}
		return jwt.encode(payload, frappe.conf.encryption_key, algorithm="HS256", headers={"kid": "v1"})

	# ------------------------------------------------------------------
	# login is gated
	# ------------------------------------------------------------------

	def test_login_with_a_temporary_password_issues_no_session(self):
		response = login(self.email, self.temp_password)

		self.assertEqual(frappe.local.response.get("http_status_code"), 403)
		self.assertEqual(response.get("code"), "PASSWORD_CHANGE_REQUIRED")
		# The security property, stated directly: correct credentials, and still
		# no token and no refresh token anywhere in the payload.
		self.assertNotIn("data", response)

	def test_login_still_rejects_a_wrong_password_as_a_plain_auth_failure(self):
		response = login(self.email, "NotTheTempPassword1!")

		self.assertEqual(frappe.local.response.get("http_status_code"), 401)
		self.assertEqual(response.get("code"), "AUTHENTICATION_ERROR")

	# ------------------------------------------------------------------
	# set_initial_password
	# ------------------------------------------------------------------

	def test_setting_a_password_clears_the_flag_and_restores_login(self):
		res = set_initial_password(self.email, self.temp_password, self.new_password)
		self.assertEqual(res.get("status"), "success")
		self.assertEqual(frappe.db.get_value("User", self.email, "a2c_must_change_password"), 0)

		# The temporary password is dead...
		frappe.local.response = frappe._dict()
		self.assertEqual(login(self.email, self.temp_password).get("code"), "AUTHENTICATION_ERROR")

		# ...and the agent's own password now opens a real session.
		frappe.local.response = frappe._dict()
		ok = login(self.email, self.new_password)
		self.assertEqual(ok.get("status"), "success")
		self.assertIn("token", ok.get("data", {}))

	def test_wrong_temporary_password_leaves_the_account_gated(self):
		res = set_initial_password(self.email, "NotTheTempPassword1!", self.new_password)

		self.assertEqual(frappe.local.response.get("http_status_code"), 401)
		self.assertEqual(res.get("code"), "AUTHENTICATION_ERROR")
		self.assertEqual(frappe.db.get_value("User", self.email, "a2c_must_change_password"), 1)

	def test_account_without_the_flag_cannot_use_the_endpoint(self):
		"""Guards against this becoming an unauthenticated change-password endpoint.

		Knowing a password would otherwise be enough to rotate it with no session,
		for any account on the site. The failure is deliberately identical to a
		wrong password so it leaks nothing either.
		"""
		self._set_flag(0)

		res = set_initial_password(self.email, self.temp_password, self.new_password)

		self.assertEqual(frappe.local.response.get("http_status_code"), 401)
		self.assertEqual(res.get("code"), "AUTHENTICATION_ERROR")

	def test_new_password_must_differ_from_the_temporary_one(self):
		res = set_initial_password(self.email, self.temp_password, self.temp_password)

		self.assertEqual(frappe.local.response.get("http_status_code"), 400)
		self.assertEqual(res.get("code"), "VALIDATION_ERROR")
		self.assertEqual(frappe.db.get_value("User", self.email, "a2c_must_change_password"), 1)

	def test_new_password_must_meet_the_complexity_rule(self):
		res = set_initial_password(self.email, self.temp_password, "alllowercaseletters")

		self.assertEqual(frappe.local.response.get("http_status_code"), 400)
		self.assertEqual(res.get("code"), "VALIDATION_ERROR")
		self.assertEqual(frappe.db.get_value("User", self.email, "a2c_must_change_password"), 1)

	# ------------------------------------------------------------------
	# Tokens minted before the flag was set must stop working
	# ------------------------------------------------------------------

	def test_middleware_rejects_a_token_for_a_flagged_user(self):
		frappe.local.request = frappe._dict({"path": "/api/method/oan_a2c.api.v1.leads.get_leads"})
		self._mock_headers["Authorization"] = f"Bearer {self._jwt_for_user()}"

		with self.assertRaises(JWTUnauthorized) as context:
			validate_jwt_request()
		self.assertIn("Password change required", context.exception.message)

	def test_middleware_lets_the_set_password_endpoint_through(self):
		frappe.local.request = frappe._dict({"path": "/api/method/oan_a2c.api.auth.set_initial_password"})
		self._mock_headers = {}

		self.assertIsNone(validate_jwt_request())

	def test_refresh_is_refused_and_the_token_burned(self):
		# A refresh token handed out before the admin reissued the password.
		self._set_flag(0)
		raw_token = generate_refresh_token(self.email)
		self._set_flag(1)

		frappe.local.response = frappe._dict()
		res = refresh(raw_token)

		self.assertEqual(frappe.local.response.get("http_status_code"), 403)
		self.assertEqual(res.get("code"), "PASSWORD_CHANGE_REQUIRED")

		token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
		self.assertFalse(frappe.db.exists("A2C User Refresh Token", {"token_hash": token_hash}))
