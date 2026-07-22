import traceback

import frappe

from oan_a2c.api.v1.seller.onboarding import (
	deactivate_user,
	get_bank_status,
	invite_user,
	list_users,
	register_bank,
	request_activation,
	save_org_contacts,
	update_user_profile,
)


def main():
	# Set up a test user
	test_user = "test_seller_api@example.com"
	if not frappe.db.exists("User", test_user):
		user = frappe.get_doc(
			{"doctype": "User", "email": test_user, "first_name": "Test Seller API", "send_welcome_email": 0}
		)
		user.insert(ignore_permissions=True)

	frappe.set_user(test_user)
	frappe.session.user = test_user

	print("1. Testing register_bank")
	try:
		bank_code = "TB002"
		if frappe.db.exists("A2C Participating Bank", bank_code):
			frappe.delete_doc("A2C Participating Bank", bank_code, ignore_permissions=True, force=True)
			frappe.db.sql(
				"DELETE FROM `tabUser Permission` WHERE `allow`='A2C Participating Bank' AND `for_value`='TB002'"
			)
			frappe.db.commit()

		kwargs = {
			"bank_name": "Test Bank 2",
			"bank_code": bank_code,
			"entity_type": "Commercial Bank",
			"registered_street": "123 Street",
			"registered_city": "Addis Ababa",
			"registered_country": "Ethiopia",
			"registered_postal_code": "1000",
			"registered_email": "testbank2@example.com",
			"registered_phone": "0911000000",
		}
		res = register_bank(**kwargs)
		print("register_bank result:", res)
	except Exception:
		print("register_bank failed:")
		traceback.print_exc()
		frappe.db.rollback()

	print("\n2. Testing save_org_contacts")
	try:
		res = save_org_contacts(
			**{
				"gro_name": "John Doe",
				"gro_mobile": "0911000001",
				"ops_name": "Jane Doe",
				"ops_mobile": "0911000002",
			}
		)
		print("save_org_contacts result:", res)
	except Exception:
		print("save_org_contacts failed:")
		traceback.print_exc()
		frappe.db.rollback()

	print("\n3. Testing request_activation")
	try:
		res = request_activation()
		print("request_activation result:", res)
	except Exception:
		print("request_activation failed:")
		traceback.print_exc()
		frappe.db.rollback()

	print("\n3b. Testing get_bank_status")
	try:
		res = get_bank_status()
		print("get_bank_status result:", res)
	except Exception:
		print("get_bank_status failed:")
		traceback.print_exc()
		frappe.db.rollback()

	print("\n4. Testing invite_user")
	try:
		res = invite_user(email="test_invite@example.com", full_name="Test Invite", role="Bank Agent")
		print("invite_user result:", res)
	except Exception:
		print("invite_user failed:")
		traceback.print_exc()
		frappe.db.rollback()

	print("\n5. Testing list_users")
	try:
		res = list_users()
		print("list_users result:", res)
	except Exception:
		print("list_users failed:")
		traceback.print_exc()
		frappe.db.rollback()

	print("\n6. Testing update_user_profile")
	try:
		res = update_user_profile(
			email="test_invite@example.com", full_name="Test Invite Updated", role="Bank Product Manager"
		)
		print("update_user_profile result:", res)
	except Exception:
		print("update_user_profile failed:")
		traceback.print_exc()
		frappe.db.rollback()

	print("\n7. Testing deactivate_user")
	try:
		res = deactivate_user(email="test_invite@example.com")
		print("deactivate_user result:", res)
	except Exception:
		print("deactivate_user failed:")
		traceback.print_exc()
		frappe.db.rollback()
