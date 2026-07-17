import frappe


def execute():
	email = "test_agent@coopbank.com"
	pwd = "test_agent@1234"

	if not frappe.db.exists("User", email):
		user = frappe.new_doc("User")
		user.email = email
		user.first_name = "Test Agent"
		user.insert(ignore_permissions=True)
		print(f"Created user {email}")
	else:
		print(f"User {email} already exists")

	from frappe.utils.password import update_password

	update_password(user=email, pwd=pwd)
	print("Password updated")

	user = frappe.get_doc("User", email)
	if not any(d.role == "System Manager" for d in user.roles):
		user.append("roles", {"role": "System Manager"})
		user.save(ignore_permissions=True)
		print("Added System Manager role")

	frappe.db.commit()
	print("User setup complete.")
