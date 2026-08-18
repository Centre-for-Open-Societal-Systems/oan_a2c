
class TestBankScopeRuntime(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		import frappe
		from oan_a2c.a2c_marketplace.roles import FARMER_ROLE, BANK_AGENT_ROLE

		cls.h = frappe.generate_hash(length=8)
		cls.bank = f"Bank-{cls.h}"
		frappe.get_doc({"doctype": "A2C Participating Bank", "bank_name": cls.bank, "bank_code": cls.bank, "status": "Active"}).insert(ignore_permissions=True)

		cls.bank_agent = f"agent-{cls.h}@example.com"
		frappe.get_doc({"doctype": "User", "email": cls.bank_agent, "first_name": "Agent", "roles": [{"role": BANK_AGENT_ROLE}]}).insert(ignore_permissions=True)
		frappe.get_doc({"doctype": "User Permission", "user": cls.bank_agent, "allow": "A2C Participating Bank", "for_value": cls.bank}).insert(ignore_permissions=True)

		cls.farmer_a = f"farmer-a-{cls.h}@example.com"
		frappe.get_doc({"doctype": "User", "email": cls.farmer_a, "first_name": "FarmerA", "roles": [{"role": FARMER_ROLE}]}).insert(ignore_permissions=True)
		cls.profile_a = frappe.get_doc({"doctype": "A2C Farmer Profile", "user": cls.farmer_a, "first_name": "F", "last_name": "A"}).insert(ignore_permissions=True)

		cls.farmer_b = f"farmer-b-{cls.h}@example.com"
		frappe.get_doc({"doctype": "User", "email": cls.farmer_b, "first_name": "FarmerB", "roles": [{"role": FARMER_ROLE}]}).insert(ignore_permissions=True)
		cls.profile_b = frappe.get_doc({"doctype": "A2C Farmer Profile", "user": cls.farmer_b, "first_name": "F", "last_name": "B"}).insert(ignore_permissions=True)

		cls.farmer_no_profile = f"farmer-none-{cls.h}@example.com"
		frappe.get_doc({"doctype": "User", "email": cls.farmer_no_profile, "first_name": "FarmerNone", "roles": [{"role": FARMER_ROLE}]}).insert(ignore_permissions=True)

		cls.prod = frappe.get_doc({"doctype": "A2C Loan Product", "product_name": f"Prod-{cls.h}", "bank": cls.bank, "min_interest_rate": 5, "max_amount": 1000, "tenure_months": 12, "status": "Active"}).insert(ignore_permissions=True)

		# Application for Farmer A (Draft)
		cls.app_a_draft = frappe.get_doc({"doctype": "A2C Loan Application", "bank": cls.bank, "loan_product": cls.prod.name, "requested_amount": 100, "loan_amount": 100, "status": "Draft", "first_name": "A", "last_name": "B", "phone_number": "111", "farmer_profile": cls.profile_a.name}).insert(ignore_permissions=True)
		# Application for Farmer A (Processing)
		cls.app_a_proc = frappe.get_doc({"doctype": "A2C Loan Application", "bank": cls.bank, "loan_product": cls.prod.name, "requested_amount": 100, "loan_amount": 100, "status": "Processing", "first_name": "A", "last_name": "B", "phone_number": "222", "farmer_profile": cls.profile_a.name}).insert(ignore_permissions=True)
		# Application for Farmer B (Draft)
		cls.app_b = frappe.get_doc({"doctype": "A2C Loan Application", "bank": cls.bank, "loan_product": cls.prod.name, "requested_amount": 100, "loan_amount": 100, "status": "Draft", "first_name": "C", "last_name": "D", "phone_number": "333", "farmer_profile": cls.profile_b.name}).insert(ignore_permissions=True)

	@classmethod
	def tearDownClass(cls):
		import frappe
		frappe.db.rollback()

	def test_farmer_sees_own_applications(self):
		import frappe
		frappe.set_user(self.farmer_a)
		apps = frappe.get_list("A2C Loan Application", pluck="name")
		self.assertIn(self.app_a_draft.name, apps)
		self.assertIn(self.app_a_proc.name, apps)
		self.assertNotIn(self.app_b.name, apps)

	def test_farmer_sees_zero_of_another_farmer(self):
		import frappe
		frappe.set_user(self.farmer_b)
		apps = frappe.get_list("A2C Loan Application", pluck="name")
		self.assertNotIn(self.app_a_draft.name, apps)
		self.assertNotIn(self.app_a_proc.name, apps)
		self.assertIn(self.app_b.name, apps)

	def test_farmer_with_no_profile_gets_empty_list(self):
		import frappe
		frappe.set_user(self.farmer_no_profile)
		apps = frappe.get_list("A2C Loan Application", pluck="name")
		self.assertEqual(apps, [])

	def test_bank_user_sees_no_draft(self):
		import frappe
		frappe.set_user(self.bank_agent)
		apps = frappe.get_list("A2C Loan Application", pluck="name")
		self.assertNotIn(self.app_a_draft.name, apps)
		self.assertNotIn(self.app_b.name, apps)
		self.assertIn(self.app_a_proc.name, apps)
