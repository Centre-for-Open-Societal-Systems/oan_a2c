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
		if not frappe.db.exists("A2C Participating Bank", "TEST_STATS_BANK"):
			bank = frappe.get_doc(
				{
					"doctype": "A2C Participating Bank",
					"bank_name": "Test Stats Bank",
					"bank_code": "TEST_STATS_BANK",
					"status": "Active",
				}
			)
			bank.insert(ignore_permissions=True)
			frappe.db.sql(
				"UPDATE `tabA2C Participating Bank` SET name='TEST_STATS_BANK' WHERE name=%s", bank.name
			)
			frappe.db.commit()

	def setUp(self):
		frappe.set_user("Administrator")
		frappe.db.sql("DELETE FROM `tabA2C Loan Product` WHERE bank='TEST_STATS_BANK'")
		frappe.db.commit()
		frappe.cache().delete_keys("dashboard_stats:TEST_STATS_BANK:*")

	def test_total_products_excludes_archived(self):
		# Create 1 Draft, 1 Active, 1 Archived product
		frappe.get_doc(
			{
				"doctype": "A2C Loan Product",
				"product_name": "Test Draft",
				"bank": "TEST_STATS_BANK",
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
				"bank": "TEST_STATS_BANK",
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
				"bank": "TEST_STATS_BANK",
				"min_interest_rate": 5,
				"max_amount": 1000,
				"tenure_months": 12,
				"status": "Archived",
			}
		).insert(ignore_permissions=True)

		stats = _compute_from_db("TEST_STATS_BANK")
		self.assertEqual(stats["total_products"], 2)  # Draft + Active (Archived excluded)
		self.assertEqual(stats["active_products"], 1)  # Only Active
		self.assertNotIn("archived_products", stats)

		# Test status update from Active -> Archived
		p_active.status = "Archived"
		p_active.save(ignore_permissions=True)

		stats_after = _compute_from_db("TEST_STATS_BANK")
		self.assertEqual(stats_after["total_products"], 1)  # Only Draft remaining
		self.assertEqual(stats_after["active_products"], 0)

		# Test status update from Archived -> Active
		p_active.status = "Active"
		p_active.save(ignore_permissions=True)

		stats_resumed = _compute_from_db("TEST_STATS_BANK")
		self.assertEqual(stats_resumed["total_products"], 2)
		self.assertEqual(stats_resumed["active_products"], 1)
