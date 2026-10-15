import unittest

import frappe

from oan_a2c.a2c_marketplace.roles import BANK_AGENT_ROLE, DEVELOPMENT_AGENT_ROLE
from oan_a2c.api.auth import require_sso_for_development_agents


def _make_user(email: str, role: str) -> str:
	if not frappe.db.exists("User", email):
		frappe.get_doc(
			{
				"doctype": "User",
				"email": email,
				"first_name": "SSO Test",
				"send_welcome_email": 0,
				"roles": [{"role": role}],
			}
		).insert(ignore_permissions=True)
	return email


class TestDevelopmentAgentSSOOnly(unittest.TestCase):
	"""Development Agents sign in only through the OpenG2P IAM service."""

	@classmethod
	def setUpClass(cls):
		frappe.set_user("Administrator")
		cls.local_dev_agent = _make_user("sso_local_dev_agent@oan.test", DEVELOPMENT_AGENT_ROLE)
		cls.iam_dev_agent = _make_user(f"{'a' * 32}@iam.internal", DEVELOPMENT_AGENT_ROLE)
		cls.local_bank_agent = _make_user("sso_local_bank_agent@oan.test", BANK_AGENT_ROLE)

	@classmethod
	def tearDownClass(cls):
		frappe.set_user("Administrator")
		for user in (cls.local_dev_agent, cls.iam_dev_agent, cls.local_bank_agent):
			frappe.delete_doc("User", user, force=True, ignore_permissions=True)

	def test_local_development_agent_is_refused(self):
		self.assertEqual(
			require_sso_for_development_agents(self.local_dev_agent),
			"Development Agents must sign in through SSO",
		)

	def test_iam_development_agent_is_allowed(self):
		self.assertIsNone(require_sso_for_development_agents(self.iam_dev_agent))

	def test_other_local_roles_are_unaffected(self):
		self.assertIsNone(require_sso_for_development_agents(self.local_bank_agent))

	def test_guard_is_registered_on_both_a2c_namespaces(self):
		from oan_auth_service.api import middleware

		from oan_a2c.api import router

		router._REGISTERED = False
		router.ensure_routes_registered()

		for prefix in ("/api/v1", "/api/method/oan_a2c."):
			self.assertIs(
				middleware._NAMESPACES[prefix]["revocation_check"],
				require_sso_for_development_agents,
				prefix,
			)
