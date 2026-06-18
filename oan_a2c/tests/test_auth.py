import frappe
import unittest
import jwt
import datetime
from unittest.mock import patch, MagicMock
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives import serialization
from oan_a2c.api.auth import login, forgot_password, reset_password, whoami
from oan_a2c.api.middleware import validate_jwt_request, _sync_roles, _ensure_user_exists


def _generate_rsa_keypair():
	"""Generate a fresh RSA keypair for testing RS256 tokens."""
	private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
	public_key = private_key.public_key()
	return private_key, public_key


def _make_rs256_token(private_key, payload):
	"""Create an RS256-signed JWT using the given RSA private key."""
	return jwt.encode(payload, private_key, algorithm="RS256")


def _make_hs256_token(secret, payload):
	"""Create an HS256-signed JWT using the given secret."""
	return jwt.encode(payload, secret, algorithm="HS256")


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

		from frappe.utils.password import update_password
		update_password(user=cls.test_email, pwd=cls.test_password)

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

	# ------------------------------------------------------------------
	# RS256 / Keycloak token tests
	# ------------------------------------------------------------------

	def test_10_middleware_rs256_valid_token(self):
		"""RS256 Keycloak token with valid signature sets user context."""
		private_key, public_key = _generate_rsa_keypair()

		now = datetime.datetime.now(datetime.timezone.utc)
		payload = {
			"sub": "keycloak-user-id",
			"email": self.test_email,
			"given_name": "Test",
			"family_name": "Agent",
			"exp": now + datetime.timedelta(hours=1),
			"iat": now,
			"iss": "http://localhost:8080/realms/oan",
			"aud": "oan-a2c",
			"realm_access": {"roles": []},
		}
		token = _make_rs256_token(private_key, payload)

		frappe.local.request = frappe._dict({"path": "/api/method/oan_a2c.api.v1.get_leads"})
		self._mock_headers["Authorization"] = f"Bearer {token}"

		# Mock the JWKS client to return our test public key
		mock_signing_key = MagicMock()
		mock_signing_key.key = public_key

		with patch("oan_a2c.api.middleware._get_jwks_client") as mock_get_client:
			mock_client = MagicMock()
			mock_client.get_signing_key_from_jwt.return_value = mock_signing_key
			mock_get_client.return_value = mock_client

			validate_jwt_request()

		self.assertEqual(frappe.session.user, self.test_email)

	def test_11_middleware_rs256_expired_token(self):
		"""RS256 Keycloak token that has expired raises AuthenticationError."""
		private_key, public_key = _generate_rsa_keypair()

		now = datetime.datetime.now(datetime.timezone.utc)
		payload = {
			"sub": "keycloak-user-id",
			"email": self.test_email,
			"exp": now - datetime.timedelta(hours=1),  # Already expired
			"iat": now - datetime.timedelta(hours=2),
			"iss": "http://localhost:8080/realms/oan",
			"aud": "oan-a2c",
			"realm_access": {"roles": []},
		}
		token = _make_rs256_token(private_key, payload)

		frappe.local.request = frappe._dict({"path": "/api/method/oan_a2c.api.v1.get_leads"})
		self._mock_headers["Authorization"] = f"Bearer {token}"

		mock_signing_key = MagicMock()
		mock_signing_key.key = public_key

		with patch("oan_a2c.api.middleware._get_jwks_client") as mock_get_client:
			mock_client = MagicMock()
			mock_client.get_signing_key_from_jwt.return_value = mock_signing_key
			mock_get_client.return_value = mock_client

			with self.assertRaises(frappe.AuthenticationError):
				validate_jwt_request()

	def test_12_middleware_rs256_wrong_key(self):
		"""RS256 token signed by an unknown key is rejected."""
		attacker_private_key, _ = _generate_rsa_keypair()
		_, legitimate_public_key = _generate_rsa_keypair()

		now = datetime.datetime.now(datetime.timezone.utc)
		payload = {
			"sub": "keycloak-user-id",
			"email": self.test_email,
			"exp": now + datetime.timedelta(hours=1),
			"iat": now,
			"iss": "http://localhost:8080/realms/oan",
			"aud": "oan-a2c",
			"realm_access": {"roles": []},
		}
		# Signed with attacker's key
		token = _make_rs256_token(attacker_private_key, payload)

		frappe.local.request = frappe._dict({"path": "/api/method/oan_a2c.api.v1.get_leads"})
		self._mock_headers["Authorization"] = f"Bearer {token}"

		# Mock JWKS client returns the legitimate (different) public key
		mock_signing_key = MagicMock()
		mock_signing_key.key = legitimate_public_key

		with patch("oan_a2c.api.middleware._get_jwks_client") as mock_get_client:
			mock_client = MagicMock()
			mock_client.get_signing_key_from_jwt.return_value = mock_signing_key
			mock_get_client.return_value = mock_client

			with self.assertRaises(frappe.AuthenticationError):
				validate_jwt_request()

	def test_13_middleware_rejects_none_algorithm(self):
		"""Tokens with alg=none must be rejected — prevents algorithm confusion attack."""
		# Craft a token with alg=none (no signature)
		# PyJWT won't encode with none by default, so we construct it manually
		import base64
		import json as json_mod

		header = base64.urlsafe_b64encode(
			json_mod.dumps({"alg": "none", "typ": "JWT"}).encode()
		).rstrip(b"=")
		payload_data = base64.urlsafe_b64encode(
			json_mod.dumps({"sub": self.test_email, "exp": 9999999999}).encode()
		).rstrip(b"=")
		token = f"{header.decode()}.{payload_data.decode()}."

		frappe.local.request = frappe._dict({"path": "/api/method/oan_a2c.api.v1.get_leads"})
		self._mock_headers["Authorization"] = f"Bearer {token}"

		with self.assertRaises(frappe.AuthenticationError):
			validate_jwt_request()

	def test_14_middleware_rs256_missing_email(self):
		"""RS256 token without an email claim raises AuthenticationError."""
		private_key, public_key = _generate_rsa_keypair()

		now = datetime.datetime.now(datetime.timezone.utc)
		payload = {
			"sub": "keycloak-user-id",
			# No "email" claim
			"exp": now + datetime.timedelta(hours=1),
			"iat": now,
			"iss": "http://localhost:8080/realms/oan",
			"aud": "oan-a2c",
		}
		token = _make_rs256_token(private_key, payload)

		frappe.local.request = frappe._dict({"path": "/api/method/oan_a2c.api.v1.get_leads"})
		self._mock_headers["Authorization"] = f"Bearer {token}"

		mock_signing_key = MagicMock()
		mock_signing_key.key = public_key

		with patch("oan_a2c.api.middleware._get_jwks_client") as mock_get_client:
			mock_client = MagicMock()
			mock_client.get_signing_key_from_jwt.return_value = mock_signing_key
			mock_get_client.return_value = mock_client

			with self.assertRaises(frappe.AuthenticationError):
				validate_jwt_request()

	# ------------------------------------------------------------------
	# JIT User Provisioning tests
	# ------------------------------------------------------------------

	def test_20_jit_creates_new_user(self):
		"""JIT provisioning creates a new Frappe User from Keycloak token claims."""
		jit_email = "jit_test_user@openagrinet.org"

		# Ensure clean state
		if frappe.db.exists("User", jit_email):
			frappe.delete_doc("User", jit_email, force=True)
			frappe.db.commit()

		payload = {
			"email": jit_email,
			"given_name": "JIT",
			"family_name": "TestUser",
		}

		_ensure_user_exists(payload, jit_email)

		self.assertTrue(frappe.db.exists("User", jit_email))

		user = frappe.get_doc("User", jit_email)
		self.assertEqual(user.first_name, "JIT")
		self.assertEqual(user.last_name, "TestUser")
		self.assertEqual(user.user_type, "System User")

		# Cleanup
		frappe.delete_doc("User", jit_email, force=True)
		frappe.db.commit()

	def test_21_jit_skips_existing_user(self):
		"""JIT provisioning does not duplicate an existing user."""
		# test_email was created in setUpClass
		payload = {
			"email": self.test_email,
			"given_name": "Should Not",
			"family_name": "Overwrite",
		}

		# This should not raise or create a duplicate
		_ensure_user_exists(payload, self.test_email)

		# Original first_name should be unchanged
		user = frappe.get_doc("User", self.test_email)
		self.assertEqual(user.first_name, "Test Agent")

	def test_22_jit_uses_email_prefix_when_no_name(self):
		"""JIT provisioning falls back to email prefix if given_name is missing."""
		jit_email = "fallback_user@openagrinet.org"

		if frappe.db.exists("User", jit_email):
			frappe.delete_doc("User", jit_email, force=True)
			frappe.db.commit()

		payload = {
			"email": jit_email,
			# No given_name or family_name
		}

		_ensure_user_exists(payload, jit_email)

		user = frappe.get_doc("User", jit_email)
		self.assertEqual(user.first_name, "fallback_user")
		self.assertEqual(user.last_name, "")

		# Cleanup
		frappe.delete_doc("User", jit_email, force=True)
		frappe.db.commit()

	# ------------------------------------------------------------------
	# Role Synchronization tests
	# ------------------------------------------------------------------

	def test_30_role_sync_adds_valid_roles(self):
		"""Role sync adds Keycloak roles that exist in Frappe."""
		# Ensure user has only protected roles initially
		user = frappe.get_doc("User", self.test_email)
		user.roles = []
		user.append("roles", {"role": "All"})
		user.append("roles", {"role": "Guest"})
		user.save(ignore_permissions=True)
		frappe.db.commit()

		payload = {"realm_access": {"roles": ["Bank Agent"]}}
		_sync_roles(self.test_email, payload)

		user.reload()
		current_roles = {r.role for r in user.roles}
		self.assertIn("Bank Agent", current_roles)

	def test_31_role_sync_removes_revoked_roles(self):
		"""Role sync removes roles no longer present in the Keycloak token."""
		# Give user Bank Agent and protected roles
		user = frappe.get_doc("User", self.test_email)
		user.roles = []
		user.append("roles", {"role": "All"})
		user.append("roles", {"role": "Guest"})
		user.append("roles", {"role": "Bank Agent"})
		user.save(ignore_permissions=True)
		frappe.db.commit()

		# Keycloak token now only has Development Agent (Bank Agent revoked)
		payload = {"realm_access": {"roles": ["Development Agent"]}}
		_sync_roles(self.test_email, payload)

		user.reload()
		current_roles = {r.role for r in user.roles}
		self.assertNotIn("Bank Agent", current_roles)
		self.assertIn("Development Agent", current_roles)

	def test_32_role_sync_preserves_protected_roles(self):
		"""Role sync never removes protected system roles (All, Guest, etc.)."""
		# Set up user with protected roles and Bank Agent
		user = frappe.get_doc("User", self.test_email)
		user.roles = []
		user.append("roles", {"role": "All"})
		user.append("roles", {"role": "Guest"})
		user.append("roles", {"role": "Bank Agent"})
		user.save(ignore_permissions=True)
		frappe.db.commit()

		# Keycloak token has only Bank Agent — no mention of All or Guest
		payload = {"realm_access": {"roles": ["Bank Agent"]}}
		_sync_roles(self.test_email, payload)

		user.reload()
		current_roles = {r.role for r in user.roles}
		self.assertIn("All", current_roles, "Protected role 'All' was removed")
		self.assertIn("Guest", current_roles, "Protected role 'Guest' was removed")
		self.assertIn("Bank Agent", current_roles)

	def test_33_role_sync_ignores_unknown_roles(self):
		"""Keycloak roles that don't exist in Frappe's tabRole are silently ignored."""
		user = frappe.get_doc("User", self.test_email)
		user.roles = []
		user.append("roles", {"role": "All"})
		user.append("roles", {"role": "Guest"})
		user.save(ignore_permissions=True)
		frappe.db.commit()

		payload = {"realm_access": {"roles": ["Nonexistent Role", "Made Up Role"]}}
		_sync_roles(self.test_email, payload)

		user.reload()
		current_roles = {r.role for r in user.roles}

		# Unknown roles should NOT be added
		self.assertNotIn("Nonexistent Role", current_roles)
		self.assertNotIn("Made Up Role", current_roles)

	def test_34_role_sync_no_change_when_already_synced(self):
		"""Role sync is a no-op when Frappe roles already match Keycloak."""
		# Set up user with Bank Agent and protected roles
		user = frappe.get_doc("User", self.test_email)
		user.roles = []
		user.append("roles", {"role": "All"})
		user.append("roles", {"role": "Guest"})
		user.append("roles", {"role": "Bank Agent"})
		user.save(ignore_permissions=True)
		frappe.db.commit()

		# Keycloak also says Bank Agent
		payload = {"realm_access": {"roles": ["Bank Agent"]}}
		_sync_roles(self.test_email, payload)

		user.reload()
		current_roles = {r.role for r in user.roles}
		self.assertIn("Bank Agent", current_roles)
		self.assertIn("All", current_roles)
		self.assertIn("Guest", current_roles)

	def test_35_role_sync_empty_keycloak_roles_removes_app_roles(self):
		"""Empty realm_access.roles with no Keycloak roles is a no-op (does not strip existing roles)."""
		# Give user Bank Agent and protected roles
		user = frappe.get_doc("User", self.test_email)
		user.roles = []
		user.append("roles", {"role": "All"})
		user.append("roles", {"role": "Guest"})
		user.append("roles", {"role": "Bank Agent"})
		user.save(ignore_permissions=True)
		frappe.db.commit()

		# Empty roles list — sync does an early return (no kc_roles)
		payload = {"realm_access": {"roles": []}}
		_sync_roles(self.test_email, payload)

		# Bank Agent should still be present since we short-circuit on empty kc_roles
		user.reload()
		current_roles = {r.role for r in user.roles}
		self.assertIn("Bank Agent", current_roles)

	# ------------------------------------------------------------------
	# HS256 backward compatibility tests
	# ------------------------------------------------------------------

	def test_40_hs256_still_works(self):
		"""Legacy HS256 tokens continue to work unchanged after RS256 integration."""
		payload = {
			"sub": self.test_email,
			"exp": datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=1),
		}
		token = _make_hs256_token(frappe.conf.encryption_key, payload)

		frappe.local.request = frappe._dict({"path": "/api/method/oan_a2c.api.v1.get_leads"})
		self._mock_headers["Authorization"] = f"Bearer {token}"

		validate_jwt_request()

		self.assertEqual(frappe.session.user, self.test_email)

	def test_41_hs256_does_not_trigger_role_sync(self):
		"""HS256 legacy tokens do NOT trigger Keycloak role sync."""
		# Set up user with only system roles
		user = frappe.get_doc("User", self.test_email)
		original_roles = {r.role for r in user.roles}

		payload = {
			"sub": self.test_email,
			"exp": datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=1),
			"roles": ["Bank Agent"],  # This is in the payload but should NOT trigger sync
		}
		token = _make_hs256_token(frappe.conf.encryption_key, payload)

		frappe.local.request = frappe._dict({"path": "/api/method/oan_a2c.api.v1.get_leads"})
		self._mock_headers["Authorization"] = f"Bearer {token}"

		validate_jwt_request()

		# Roles should NOT have changed — HS256 path doesn't sync
		user.reload()
		current_roles = {r.role for r in user.roles}
		self.assertEqual(current_roles, original_roles)

	# ------------------------------------------------------------------
	# whoami endpoint tests
	# ------------------------------------------------------------------

	def test_50_whoami_authenticated(self):
		"""whoami returns the user profile when authenticated."""
		frappe.set_user(self.test_email)

		response = whoami()

		self.assertEqual(response.get("status"), "success")
		user_block = response.get("user", {})
		self.assertEqual(user_block.get("email"), self.test_email)
		self.assertIn("roles", user_block)
		self.assertIn("bank", user_block)
		self.assertIn("full_name", user_block)

	def test_51_whoami_guest_rejected(self):
		"""whoami returns 401 for Guest users."""
		frappe.set_user("Guest")

		response = whoami()

		self.assertEqual(frappe.local.response.get("http_status_code"), 401)
		self.assertEqual(response.get("status"), "error")
