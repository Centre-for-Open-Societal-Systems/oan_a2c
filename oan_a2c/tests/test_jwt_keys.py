"""Tests for JWT signing-key resolution, the encryption_key fallback, and rotation.

Its own module rather than an append to test_auth.py — see docs/merge-hygiene.md.

No fixtures are created here: the middleware only needs a `sub` naming an enabled
User, so these use Administrator and touch no rows at all.
"""

import datetime
import unittest

import frappe
import jwt

from oan_a2c.api.jwt_keys import (
	FALLBACK_KID,
	JWTKeyConfigurationError,
	get_signing_key,
	get_verification_key,
)
from oan_a2c.api.middleware import JWTUnauthorized, validate_jwt_request
from oan_a2c.tests.request_context import RequestContextMixin

# Every conf key the resolver reads. Saved and restored wholesale so a test that
# clears encryption_key cannot leak that state into the rest of the suite.
CONF_KEYS = ("jwt_secrets", "jwt_current_kid", "encryption_key")

PROTECTED_PATH = "/api/method/oan_a2c.api.v1.get_leads"


class TestJWTKeyResolution(RequestContextMixin, unittest.TestCase):
	def setUp(self):
		super().setUp()
		self._saved_conf = {key: frappe.conf.get(key) for key in CONF_KEYS}

	def tearDown(self):
		for key, value in self._saved_conf.items():
			if value is None:
				frappe.conf.pop(key, None)
			else:
				frappe.conf[key] = value
		frappe.set_user("Administrator")
		super().tearDown()

	def _set_conf(self, **values):
		"""Replace the key config wholesale — absent kwargs mean absent from site_config."""
		for key in CONF_KEYS:
			frappe.conf.pop(key, None)
		for key, value in values.items():
			frappe.conf[key] = value

	def _token(self, secret, kid, sub="Administrator"):
		payload = {
			"sub": sub,
			"iss": "oan_a2c_identity_gateway",
			"aud": "oan_a2c_client",
			"exp": datetime.datetime.now(datetime.UTC) + datetime.timedelta(minutes=5),
		}
		return jwt.encode(payload, secret, algorithm="HS256", headers={"kid": kid})

	def _validate(self, token):
		frappe.local.request = frappe._dict({"path": PROTECTED_PATH})
		self._mock_headers["Authorization"] = f"Bearer {token}"
		return validate_jwt_request()

	# ------------------------------------------------------------------
	# Key resolution
	# ------------------------------------------------------------------

	def test_signing_key_uses_current_kid(self):
		self._set_conf(jwt_secrets={"v1": "old-secret", "v2": "new-secret"}, jwt_current_kid="v2")
		self.assertEqual(get_signing_key(), ("v2", "new-secret"))

	def test_signing_key_defaults_to_v1_without_current_kid(self):
		self._set_conf(jwt_secrets={"v1": "only-secret"})
		self.assertEqual(get_signing_key(), (FALLBACK_KID, "only-secret"))

	def test_jwt_secrets_takes_precedence_over_encryption_key(self):
		self._set_conf(jwt_secrets={"v1": "dedicated"}, encryption_key="legacy-key")
		self.assertEqual(get_signing_key(), (FALLBACK_KID, "dedicated"))

	def test_auth_survives_missing_encryption_key(self):
		"""The split's payoff: no encryption_key in site_config, login still signs."""
		self._set_conf(jwt_secrets={"v1": "dedicated"})
		kid, secret = get_signing_key()
		self.assertEqual((kid, secret), (FALLBACK_KID, "dedicated"))
		self.assertEqual(get_verification_key(kid), "dedicated")

	def test_falls_back_to_encryption_key(self):
		"""The other direction: a site never given jwt_secrets keeps working."""
		self._set_conf(encryption_key="legacy-key")
		self.assertEqual(get_signing_key(), (FALLBACK_KID, "legacy-key"))

	def test_blank_secret_entries_are_ignored(self):
		# A kid mapped to "" would otherwise look configured while verifying nothing.
		self._set_conf(jwt_secrets={"v1": ""}, encryption_key="legacy-key")
		self.assertEqual(get_signing_key(), (FALLBACK_KID, "legacy-key"))

	def test_current_kid_without_matching_secret_raises(self):
		self._set_conf(jwt_secrets={"v1": "a"}, jwt_current_kid="v9")
		with self.assertRaises(JWTKeyConfigurationError):
			get_signing_key()

	def test_no_key_material_raises(self):
		self._set_conf()
		with self.assertRaises(JWTKeyConfigurationError):
			get_signing_key()
		with self.assertRaises(JWTKeyConfigurationError):
			get_verification_key("v1")

	def test_verification_key_lookup(self):
		self._set_conf(jwt_secrets={"v1": "a", "v2": "b"}, jwt_current_kid="v2")
		self.assertEqual(get_verification_key("v1"), "a")
		self.assertEqual(get_verification_key("v2"), "b")
		self.assertIsNone(get_verification_key("v3"))
		self.assertIsNone(get_verification_key(None))

	# ------------------------------------------------------------------
	# Rotation, end to end through the middleware
	# ------------------------------------------------------------------

	def test_rotation_accepts_both_current_and_previous_kid(self):
		"""Rotating the signing key must not invalidate tokens already in flight."""
		self._set_conf(jwt_secrets={"v1": "old-secret", "v2": "new-secret"}, jwt_current_kid="v2")
		self.assertIsNone(self._validate(self._token("old-secret", "v1")))
		self.assertIsNone(self._validate(self._token("new-secret", "v2")))

	def test_retired_kid_is_rejected(self):
		"""Once the old kid is dropped from jwt_secrets, its tokens stop verifying."""
		self._set_conf(jwt_secrets={"v2": "new-secret"}, jwt_current_kid="v2")
		with self.assertRaises(JWTUnauthorized) as context:
			self._validate(self._token("old-secret", "v1"))
		self.assertIn("Invalid or missing Key ID", context.exception.message)

	def test_right_kid_wrong_secret_is_rejected(self):
		self._set_conf(jwt_secrets={"v2": "new-secret"}, jwt_current_kid="v2")
		with self.assertRaises(JWTUnauthorized) as context:
			self._validate(self._token("not-the-secret", "v2"))
		self.assertIn("Invalid token", context.exception.message)

	def test_missing_key_material_is_reported_as_configuration_error(self):
		# Distinguishable from a bad token, which is the reason JWTKeyConfigurationError
		# is a separate exception rather than a None return.
		self._set_conf()
		with self.assertRaises(JWTUnauthorized) as context:
			self._validate(self._token("whatever", "v1"))
		self.assertIn("System encryption key missing", context.exception.message)
