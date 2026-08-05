import unittest

import frappe

from oan_a2c.a2c_marketplace.stats_cache import (
	_compute_from_db,
	compute_and_set,
	get_stats_for_bank,
	on_product_change,
)


class TestStatsCache(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		frappe.set_user("Administrator")
		suffix = frappe.generate_hash(length=6)
		cls.bank = frappe.get_doc(
			{
				"doctype": "A2C Participating Bank",
				"bank_name": f"Test Stats Bank {suffix}",
				"bank_code": f"TEST_STATS_{suffix}",
				"status": "In Review",
				"entity_type": "Commercial Bank",
				"registered_email": f"stats_{suffix}@test.com",
				"registered_phone": "+251911000000",
				"registered_city": "Addis Ababa",
				"registered_country": "Ethiopia",
			}
		).insert(ignore_permissions=True)
		cls.bank_name = cls.bank.name
		frappe.db.commit()

	@classmethod
	def tearDownClass(cls):
		frappe.set_user("Administrator")
		frappe.delete_doc("A2C Participating Bank", cls.bank_name, force=True)
		frappe.db.commit()

	def setUp(self):
		frappe.set_user("Administrator")
		frappe.db.delete("A2C Loan Product", {"bank": self.bank_name})
		frappe.db.commit()
		frappe.cache().delete_keys(f"dashboard_stats:{self.bank_name}:*")

	def test_total_products_excludes_archived(self):
		# Create 1 Draft, 1 Active, 1 Archived product
		frappe.get_doc(
			{
				"doctype": "A2C Loan Product",
				"product_name": "Test Draft",
				"bank": self.bank_name,
				"min_interest_rate": 5,
				"max_amount": 1000,
				"tenure_months": 12,
				"status": "Draft",
			}
		).insert(ignore_permissions=True)

		p_active = frappe.get_doc(
			{
				"doctype": "A2C Loan Product",
				"product_name": "Test Active",
				"bank": self.bank_name,
				"min_interest_rate": 5,
				"max_amount": 1000,
				"tenure_months": 12,
				"status": "Active",
			}
		).insert(ignore_permissions=True)

		frappe.get_doc(
			{
				"doctype": "A2C Loan Product",
				"product_name": "Test Archived",
				"bank": self.bank_name,
				"min_interest_rate": 5,
				"max_amount": 1000,
				"tenure_months": 12,
				"status": "Archived",
			}
		).insert(ignore_permissions=True)

		stats = _compute_from_db(self.bank_name)
		self.assertEqual(stats["total_products"], 2)  # Draft + Active (Archived excluded)
		self.assertEqual(stats["active_products"], 1)  # Only Active
		self.assertNotIn("archived_products", stats)

		# Test status update from Active -> Archived
		p_active.status = "Archived"
		p_active.save(ignore_permissions=True)

		stats_after = _compute_from_db(self.bank_name)
		self.assertEqual(stats_after["total_products"], 1)  # Only Draft remaining
		self.assertEqual(stats_after["active_products"], 0)

		# Test status update from Archived -> Active
		p_active.status = "Active"
		p_active.save(ignore_permissions=True)

		stats_resumed = _compute_from_db(self.bank_name)
		self.assertEqual(stats_resumed["total_products"], 2)
		self.assertEqual(stats_resumed["active_products"], 1)
