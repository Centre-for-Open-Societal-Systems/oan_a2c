import inspect

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils.password import check_password, update_password

from oan_a2c.api.auth import change_password


class TestChangePassword(IntegrationTestCase):
	def setUp(self):
		super().setUp()
		self.user = self.make_user("password_reuse_test@example.com")
		self.current_password = "CurrentPassword@123"
		update_password(user=self.user, pwd=self.current_password, logout_all_sessions=False)
		frappe.set_user(self.user)

	def tearDown(self):
		frappe.set_user("Administrator")
		super().tearDown()

	def make_user(self, email: str) -> str:
		if not frappe.db.exists("User", email):
			frappe.get_doc(
				{
					"doctype": "User",
					"email": email,
					"first_name": "Password Reuse Test",
					"send_welcome_email": 0,
					"enabled": 1,
				}
			).insert(ignore_permissions=True)
		return email

	def test_rejects_current_password_as_new_password(self):
		endpoint = inspect.unwrap(change_password)

		with self.assertRaisesRegex(
			frappe.ValidationError,
			"New password must be different from the current password",
		):
			endpoint(current_password=self.current_password, new_password=self.current_password)

		check_password(self.user, self.current_password)
