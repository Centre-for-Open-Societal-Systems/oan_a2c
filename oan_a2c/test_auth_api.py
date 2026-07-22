import traceback

import frappe

from oan_a2c.api.v1.auth import login, register_user


def main():
	print("1. Testing register_user")
	try:
		if frappe.db.exists("User", "test_auth_api@example.com"):
			frappe.delete_doc("User", "test_auth_api@example.com", ignore_permissions=True, force=True)
			frappe.db.commit()

		res = register_user(
			email="test_auth_api@example.com",
			full_name="Test Auth API",
			password="testpassword123",
			phone_number="0911000003",
		)
		print("register_user result:", res)
	except Exception:
		print("register_user failed:")
		traceback.print_exc()
		frappe.db.rollback()

	print("\n2. Testing login")
	try:
		res = login(email="test_auth_api@example.com", password="testpassword123")
		print("login result:", res)
	except Exception:
		print("login failed:")
		traceback.print_exc()
		frappe.db.rollback()
