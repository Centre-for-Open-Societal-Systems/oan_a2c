import unittest

import frappe

from oan_a2c.a2c_marketplace.roles import FARMER_ROLE
from oan_a2c.api.auth import access_token_claims, on_user_profile
from oan_a2c.tests.request_context import RequestContextMixin


class TestUserProfileHook(RequestContextMixin, unittest.TestCase):
	"""`on_user_profile` feeds oan_auth_service's GET /api/v1/auth/me."""

	def test_farmer_profile_has_user_type_and_no_bank(self):
		user_doc = frappe.get_doc("User", "Administrator")

		namespace, data = on_user_profile(user_doc, [FARMER_ROLE])

		self.assertEqual(namespace, "a2c")
		self.assertEqual(data["user_type"], "farmer")
		self.assertNotIn("bank_id", data)

	def test_hook_is_registered(self):
		self.assertIn("oan_a2c.api.auth.on_user_profile", frappe.get_hooks("on_user_profile"))

	def test_access_token_carries_user_type(self):
		import jwt

		from oan_a2c.tests.request_context import sign_access_token

		token = sign_access_token("Administrator", [FARMER_ROLE])

		self.assertEqual(jwt.decode(token, options={"verify_signature": False})["user_type"], "farmer")
		self.assertEqual(access_token_claims("Administrator", [FARMER_ROLE]), {"user_type": "farmer"})
