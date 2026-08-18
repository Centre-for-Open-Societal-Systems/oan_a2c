"""An admin-issued temporary password authenticates but opens no session.

Kept in its own module rather than appended to `test_auth.py`: the login,
middleware and must-change flows are edited by different feature branches, and a
shared file means a conflict at its last line every time two of them add a test.
"""

import datetime
import hashlib
import unittest

import frappe
import jwt

from oan_a2c.api.auth import (
	generate_refresh_token,
	login,
	refresh,
	set_initial_password,
)
from oan_a2c.api.middleware import JWTUnauthorized, validate_jwt_request
from oan_a2c.tests.request_context import RequestContextMixin


class TestMustChangePassword(RequestContextMixin, unittest.TestCase):
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
