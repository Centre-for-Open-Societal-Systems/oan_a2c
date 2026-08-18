import unittest

import frappe

from oan_a2c.a2c_marketplace.roles import (
	ADMIN_ROLE,
	BANK_ADMIN_ROLE,
	BANK_AGENT_ROLE,
	DEVELOPMENT_AGENT_ROLE,
)
from oan_a2c.api.v1.seller.onboarding import (
	invite_team_member,
	list_users,
	reset_member_password,
	update_user,
)


def _create_test_bank(bank_code: str, bank_name: str) -> str:
	"""Always create a fresh bank — never reuse ambient data."""
	doc = frappe.get_doc(
		{
			"doctype": "A2C Participating Bank",
			"registered_city": "Test City",
			"kyc_document": "/private/files/test_kyc.pdf",
			"gro_name": "Test GRO",
			"ops_name": "Test Ops",
			"bank_code": bank_code,
			"bank_name": bank_name,
			"entity_type": "Commercial Bank",
			"registered_street": "123 Test St",
			"registered_region": "Addis Ababa",
			"registered_country": "Ethiopia",
			"registered_postal_code": "1000",
			"registered_email": f"{bank_code.lower()}@test.com",
			"registered_phone": "+251911000000",
			"status": "In Review",
		}
	)
	doc.insert(ignore_permissions=True)
	return doc.name


def _ensure_role(role: str) -> None:
	if not frappe.db.exists("Role", role):
		frappe.get_doc({"doctype": "Role", "role_name": role, "desk_access": 1}).insert(
			ignore_permissions=True
		)


def _create_test_user(email: str, full_name: str, role: str, bank: str | None = None) -> str:
	"""Always create a fresh user — never reuse ambient data."""
	_ensure_role(role)
	user = frappe.get_doc(
		{
			"doctype": "User",
			"email": email,
			"first_name": full_name,
			"send_welcome_email": 0,
			"roles": [{"role": role}],
		}
	)
	user.insert(ignore_permissions=True)

	if bank:
		frappe.get_doc(
			{
				"doctype": "User Permission",
				"user": email,
				"allow": "A2C Participating Bank",
				"for_value": bank,
				"is_default": 1,
			}
		).insert(ignore_permissions=True)

	return email


class TestUpdateUser(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		frappe.set_user("Administrator")
		suffix = frappe.generate_hash(length=6)

		cls.bank_a = _create_test_bank(f"TU_BANK_A_{suffix}", "Test Users Bank A")
		cls.bank_b = _create_test_bank(f"TU_BANK_B_{suffix}", "Test Users Bank B")

		cls.admin = _create_test_user(f"tu_admin_{suffix}@oan.test", "TU Admin", ADMIN_ROLE)
		cls.bank_admin_a = _create_test_user(
			f"tu_bankadmin_a_{suffix}@oan.test", "TU Bank Admin A", BANK_ADMIN_ROLE, cls.bank_a
		)
		cls.bank_agent_a = _create_test_user(
			f"tu_agent_a_{suffix}@oan.test", "TU Agent A", BANK_AGENT_ROLE, cls.bank_a
		)
		cls.bank_agent_a2 = _create_test_user(
			f"tu_agent_a2_{suffix}@oan.test", "TU Agent A2", BANK_AGENT_ROLE, cls.bank_a
		)
		cls.bank_admin_b = _create_test_user(
			f"tu_bankadmin_b_{suffix}@oan.test", "TU Bank Admin B", BANK_ADMIN_ROLE, cls.bank_b
		)
		cls.bank_agent_b = _create_test_user(
			f"tu_agent_b_{suffix}@oan.test", "TU Agent B", BANK_AGENT_ROLE, cls.bank_b
		)
		cls.dev_agent = _create_test_user(
			f"tu_devagent_{suffix}@oan.test", "TU Dev Agent", DEVELOPMENT_AGENT_ROLE
		)
		cls.farmer = _create_test_user(f"tu_farmer_{suffix}@oan.test", "TU Farmer", "A2C Farmer")

		# Records to tear down, in deletion order: users (and their permissions) before banks.
		cls._users = [
			cls.admin,
			cls.bank_admin_a,
			cls.bank_agent_a,
			cls.bank_agent_a2,
			cls.bank_admin_b,
			cls.bank_agent_b,
			cls.dev_agent,
			cls.farmer,
		]
		cls._banks = [cls.bank_a, cls.bank_b]

		frappe.db.commit()

	@classmethod
	def tearDownClass(cls):
		frappe.set_user("Administrator")
		for email in cls._users:
			for perm in frappe.get_all("User Permission", filters={"user": email}, pluck="name"):
				frappe.delete_doc("User Permission", perm, force=True, ignore_permissions=True)
			frappe.delete_doc("User", email, force=True, ignore_permissions=True)
		for bank in cls._banks:
			frappe.delete_doc("A2C Participating Bank", bank, force=True, ignore_permissions=True)
		frappe.db.commit()

	def setUp(self):
		frappe.local.response = frappe._dict()
		frappe.set_user("Administrator")

	# ------------------------------------------------------------------
	# A2C Administrator can manage anyone below level 1
	# ------------------------------------------------------------------

	def test_admin_can_update_bank_admin_name(self):
		frappe.set_user(self.admin)
		res = update_user(email=self.bank_admin_a, full_name="Updated Name")
		self.assertEqual(res.get("status"), "success")
		self.assertEqual(frappe.db.get_value("User", self.bank_admin_a, "first_name"), "Updated Name")

	def test_admin_can_disable_bank_agent(self):
		frappe.set_user(self.admin)
		res = update_user(email=self.bank_agent_a, enabled=False)
		self.assertEqual(res.get("status"), "success")
		self.assertEqual(frappe.db.get_value("User", self.bank_agent_a, "enabled"), 0)
		# re-enable so other tests are not affected
		frappe.db.set_value("User", self.bank_agent_a, "enabled", 1)

	def test_admin_can_promote_agent_to_bank_admin(self):
		frappe.set_user(self.admin)
		res = update_user(email=self.bank_agent_a2, role=BANK_ADMIN_ROLE)
		self.assertEqual(res.get("status"), "success")
		roles = frappe.get_roles(self.bank_agent_a2)
		self.assertIn(BANK_ADMIN_ROLE, roles)
		self.assertNotIn(BANK_AGENT_ROLE, roles)
		# restore
		u = frappe.get_doc("User", self.bank_agent_a2)
		u.roles = [r for r in u.roles if r.role != BANK_ADMIN_ROLE]
		u.append("roles", {"role": BANK_AGENT_ROLE})
		u.save(ignore_permissions=True)

	# ------------------------------------------------------------------
	# Bank Admin can manage Bank Agents in their own bank
	# ------------------------------------------------------------------

	def test_bank_admin_can_update_own_bank_agent(self):
		frappe.set_user(self.bank_admin_a)
		res = update_user(email=self.bank_agent_a, full_name="Agent Updated")
		self.assertEqual(res.get("status"), "success")

	def test_bank_admin_can_disable_own_bank_agent(self):
		frappe.set_user(self.bank_admin_a)
		res = update_user(email=self.bank_agent_a, enabled=False)
		self.assertEqual(res.get("status"), "success")
		self.assertEqual(frappe.db.get_value("User", self.bank_agent_a, "enabled"), 0)
		# re-enable
		frappe.db.set_value("User", self.bank_agent_a, "enabled", 1)

	# ------------------------------------------------------------------
	# Hierarchy violations → 403
	# ------------------------------------------------------------------

	def test_bank_admin_cannot_manage_a2c_administrator(self):
		frappe.set_user(self.bank_admin_a)
		res = update_user(email=self.admin, full_name="Hacked")
		self.assertEqual(frappe.local.response.get("http_status_code"), 403)
		self.assertEqual(res.get("status"), "error")

	def test_bank_admin_cannot_manage_another_bank_admin(self):
		frappe.set_user(self.bank_admin_a)
		res = update_user(email=self.bank_admin_b, full_name="Should Fail")
		self.assertEqual(frappe.local.response.get("http_status_code"), 403)
		self.assertEqual(res.get("status"), "error")

	def test_bank_admin_cannot_manage_agent_from_other_bank(self):
		frappe.set_user(self.bank_admin_a)
		res = update_user(email=self.bank_agent_b, full_name="Should Fail")
		self.assertEqual(frappe.local.response.get("http_status_code"), 403)
		self.assertEqual(res.get("status"), "error")

	def test_bank_agent_cannot_manage_anyone(self):
		frappe.set_user(self.bank_agent_a)
		res = update_user(email=self.bank_agent_a2, full_name="Should Fail")
		self.assertEqual(frappe.local.response.get("http_status_code"), 403)
		self.assertEqual(res.get("status"), "error")

	def test_bank_agent_cannot_manage_farmer(self):
		# Even though Farmer is a lower level, a Bank Agent is not a manager tier.
		frappe.set_user(self.bank_agent_a)
		res = update_user(email=self.farmer, enabled=False)
		self.assertEqual(frappe.local.response.get("http_status_code"), 403)
		self.assertEqual(res.get("status"), "error")

	def test_dev_agent_cannot_manage_farmer(self):
		# Dev Agent is level 3 but not a manager tier — platform roles are admin-only.
		frappe.set_user(self.dev_agent)
		res = update_user(email=self.farmer, enabled=False)
		self.assertEqual(frappe.local.response.get("http_status_code"), 403)
		self.assertEqual(res.get("status"), "error")

	def test_bank_admin_cannot_manage_farmer(self):
		# Farmer is a lower level but not a Bank Agent — outside Bank Admin's reach.
		frappe.set_user(self.bank_admin_a)
		res = update_user(email=self.farmer, enabled=False)
		self.assertEqual(frappe.local.response.get("http_status_code"), 403)
		self.assertEqual(res.get("status"), "error")

	def test_bank_admin_cannot_manage_dev_agent(self):
		frappe.set_user(self.bank_admin_a)
		res = update_user(email=self.dev_agent, full_name="Should Fail")
		self.assertEqual(frappe.local.response.get("http_status_code"), 403)
		self.assertEqual(res.get("status"), "error")

	def test_bank_admin_cannot_assign_platform_role(self):
		# Farmer/Dev Agent are lower level, but a Bank Admin may only assign Bank Agent.
		frappe.set_user(self.bank_admin_a)
		res = update_user(email=self.bank_agent_a, role=DEVELOPMENT_AGENT_ROLE)
		self.assertEqual(frappe.local.response.get("http_status_code"), 403)
		self.assertEqual(res.get("status"), "error")

	def test_bank_admin_cannot_assign_equal_or_higher_role(self):
		frappe.set_user(self.bank_admin_a)
		# Bank Admin cannot assign Bank Admin role (same level)
		res = update_user(email=self.bank_agent_a, role=BANK_ADMIN_ROLE)
		self.assertEqual(frappe.local.response.get("http_status_code"), 403)
		self.assertEqual(res.get("status"), "error")

	def test_admin_cannot_assign_admin_role(self):
		frappe.set_user(self.admin)
		# A2C Administrator cannot assign A2C Administrator (same level)
		res = update_user(email=self.bank_admin_a, role=ADMIN_ROLE)
		self.assertEqual(frappe.local.response.get("http_status_code"), 403)
		self.assertEqual(res.get("status"), "error")

	# ------------------------------------------------------------------
	# Self-modification blocked
	# ------------------------------------------------------------------

	def test_cannot_update_self(self):
		frappe.set_user(self.admin)
		res = update_user(email=self.admin, full_name="New Name")
		self.assertEqual(frappe.local.response.get("http_status_code"), 400)
		self.assertEqual(res.get("status"), "error")

	# ------------------------------------------------------------------
	# Validation errors
	# ------------------------------------------------------------------

	def test_invalid_role_rejected(self):
		frappe.set_user(self.admin)
		res = update_user(email=self.bank_agent_a, role="Fake Role")
		self.assertEqual(frappe.local.response.get("http_status_code"), 400)
		self.assertEqual(res.get("status"), "error")

	def test_nonexistent_user_returns_404(self):
		frappe.set_user(self.admin)
		res = update_user(email="nobody@oan.test")
		self.assertEqual(frappe.local.response.get("http_status_code"), 404)
		self.assertEqual(res.get("status"), "error")

	# ------------------------------------------------------------------
	# No-op (no fields) returns 200
	# ------------------------------------------------------------------

	def test_no_op_returns_success(self):
		frappe.set_user(self.admin)
		res = update_user(email=self.bank_agent_a)
		self.assertEqual(res.get("status"), "success")


class TestInviteTeamMember(unittest.TestCase):
	"""Bank Admins staff their bank with Agents — and only Agents."""

	@classmethod
	def setUpClass(cls):
		frappe.set_user("Administrator")
		cls.suffix = frappe.generate_hash(length=6)

		cls.bank = _create_test_bank(f"IU_BANK_{cls.suffix}", "Invite Users Bank")
		cls.bank_admin = _create_test_user(
			f"iu_bankadmin_{cls.suffix}@oan.test", "IU Bank Admin", BANK_ADMIN_ROLE, cls.bank
		)
		cls._users = [cls.bank_admin]

	@classmethod
	def tearDownClass(cls):
		frappe.set_user("Administrator")
		for email in cls._users:
			if not frappe.db.exists("User", email):
				continue
			for perm in frappe.get_all("User Permission", filters={"user": email}, pluck="name"):
				frappe.delete_doc("User Permission", perm, force=True, ignore_permissions=True)
			frappe.delete_doc("User", email, force=True, ignore_permissions=True)
		frappe.delete_doc("A2C Participating Bank", cls.bank, force=True, ignore_permissions=True)

	def setUp(self):
		frappe.local.response = frappe._dict()
		frappe.set_user("Administrator")

	def _invitee(self, tag: str) -> str:
		email = f"iu_{tag}_{self.suffix}@oan.test"
		type(self)._users.append(email)
		return email

	def test_invited_agent_starts_with_a_must_change_password(self):
		invitee = self._invitee("agent")
		frappe.set_user(self.bank_admin)

		res = invite_team_member(email=invitee, full_name="New Agent", password="TempIssued1")

		self.assertEqual(res.get("status"), "success")
		self.assertTrue(frappe.db.exists("User", invitee))
		self.assertIn(BANK_AGENT_ROLE, frappe.get_roles(invitee))
		# The admin chose this password, so it may not survive first use.
		self.assertEqual(frappe.db.get_value("User", invitee, "a2c_must_change_password"), 1)

	def test_role_defaults_to_bank_agent_when_omitted(self):
		invitee = self._invitee("default")
		frappe.set_user(self.bank_admin)

		res = invite_team_member(email=invitee, full_name="Default Role", password="TempIssued1")

		self.assertEqual(res.get("status"), "success")
		self.assertIn(BANK_AGENT_ROLE, frappe.get_roles(invitee))

	def test_bank_admin_cannot_invite_another_bank_admin(self):
		invitee = self._invitee("rogueadmin")
		frappe.set_user(self.bank_admin)

		res = invite_team_member(
			email=invitee, full_name="Rogue Admin", role=BANK_ADMIN_ROLE, password="TempIssued1"
		)

		self.assertEqual(frappe.local.response.get("http_status_code"), 400)
		self.assertEqual(res.get("code"), "VALIDATION_ERROR")
		# Rejected before any account exists — not created-then-demoted.
		self.assertFalse(frappe.db.exists("User", invitee))

	def test_bank_admin_cannot_invite_a_platform_role(self):
		invitee = self._invitee("devagent")
		frappe.set_user(self.bank_admin)

		res = invite_team_member(
			email=invitee, full_name="Rogue Dev", role=DEVELOPMENT_AGENT_ROLE, password="TempIssued1"
		)

		self.assertEqual(res.get("code"), "VALIDATION_ERROR")
		self.assertFalse(frappe.db.exists("User", invitee))

	def test_team_list_flags_members_who_have_not_set_a_password(self):
		invitee = self._invitee("listed")
		frappe.set_user(self.bank_admin)
		invite_team_member(email=invitee, full_name="Listed Agent", password="TempIssued1")

		frappe.local.response = frappe._dict()
		rows = list_users().get("data", {}).get("users", [])
		row = next((u for u in rows if u.get("name") == invitee), None)

		self.assertIsNotNone(row, "invited agent should appear in the bank's team list")
		self.assertTrue(row.get("must_change_password"))


class TestResetMemberPassword(unittest.TestCase):
	"""A Bank Admin can reissue an agent's password — within their own bank only."""

	@classmethod
	def setUpClass(cls):
		frappe.set_user("Administrator")
		suffix = frappe.generate_hash(length=6)

		cls.bank_a = _create_test_bank(f"RMP_BANK_A_{suffix}", "Reset Pwd Bank A")
		cls.bank_b = _create_test_bank(f"RMP_BANK_B_{suffix}", "Reset Pwd Bank B")

		cls.bank_admin_a = _create_test_user(
			f"rmp_admin_a_{suffix}@oan.test", "RMP Admin A", BANK_ADMIN_ROLE, cls.bank_a
		)
		cls.bank_admin_b = _create_test_user(
			f"rmp_admin_b_{suffix}@oan.test", "RMP Admin B", BANK_ADMIN_ROLE, cls.bank_b
		)
		cls.agent_a = _create_test_user(
			f"rmp_agent_a_{suffix}@oan.test", "RMP Agent A", BANK_AGENT_ROLE, cls.bank_a
		)
		cls.agent_b = _create_test_user(
			f"rmp_agent_b_{suffix}@oan.test", "RMP Agent B", BANK_AGENT_ROLE, cls.bank_b
		)

		cls._users = [cls.bank_admin_a, cls.bank_admin_b, cls.agent_a, cls.agent_b]
		cls._banks = [cls.bank_a, cls.bank_b]

	@classmethod
	def tearDownClass(cls):
		frappe.set_user("Administrator")
		for email in cls._users:
			frappe.db.delete("A2C User Refresh Token", {"user": email})
			for perm in frappe.get_all("User Permission", filters={"user": email}, pluck="name"):
				frappe.delete_doc("User Permission", perm, force=True, ignore_permissions=True)
			frappe.delete_doc("User", email, force=True, ignore_permissions=True)
		for bank in cls._banks:
			frappe.delete_doc("A2C Participating Bank", bank, force=True, ignore_permissions=True)

	def setUp(self):
		frappe.local.response = frappe._dict()
		frappe.set_user("Administrator")
		frappe.db.set_value("User", self.agent_a, "a2c_must_change_password", 0)
		for caller in (self.bank_admin_a, self.bank_admin_b, self.agent_a):
			frappe.cache().delete_value(f"rl:reset_member_pwd:{caller}")

	def test_reissuing_a_password_regates_the_account(self):
		frappe.set_user(self.bank_admin_a)

		res = reset_member_password(email=self.agent_a, password="ReIssued99")

		self.assertEqual(res.get("status"), "success")
		self.assertEqual(frappe.db.get_value("User", self.agent_a, "a2c_must_change_password"), 1)

	def test_reissuing_kills_any_live_session(self):
		"""The reason to reset is often a compromise — the old session must die."""
		frappe.set_user("Administrator")
		frappe.get_doc(
			{
				"doctype": "A2C User Refresh Token",
				"user": self.agent_a,
				"token_hash": frappe.generate_hash(length=64),
				"expiry": frappe.utils.add_days(frappe.utils.now_datetime(), 1),
			}
		).insert(ignore_permissions=True)

		frappe.set_user(self.bank_admin_a)
		reset_member_password(email=self.agent_a, password="ReIssued99")

		self.assertEqual(frappe.db.count("A2C User Refresh Token", {"user": self.agent_a}), 0)

	def test_bank_admin_cannot_reset_an_agent_from_another_bank(self):
		frappe.set_user(self.bank_admin_a)

		res = reset_member_password(email=self.agent_b, password="ReIssued99")

		self.assertEqual(frappe.local.response.get("http_status_code"), 403)
		self.assertEqual(res.get("status"), "error")
		self.assertEqual(frappe.db.get_value("User", self.agent_b, "a2c_must_change_password"), 0)

	def test_bank_admin_cannot_reset_another_bank_admin(self):
		frappe.set_user(self.bank_admin_a)

		res = reset_member_password(email=self.bank_admin_b, password="ReIssued99")

		self.assertEqual(frappe.local.response.get("http_status_code"), 403)
		self.assertEqual(res.get("status"), "error")

	def test_bank_agent_cannot_reset_anyone(self):
		frappe.set_user(self.agent_a)

		res = reset_member_password(email=self.agent_b, password="ReIssued99")

		self.assertEqual(frappe.local.response.get("http_status_code"), 403)
		self.assertEqual(res.get("status"), "error")
