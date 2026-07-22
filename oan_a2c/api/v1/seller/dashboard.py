import frappe

from oan_a2c.a2c_marketplace.permissions import bank_filters
from oan_a2c.api.utils import handle_api_errors, success_response


@frappe.whitelist()
@handle_api_errors
def get_stats():
	# frappe.get_all bypasses the bank_scope_query permission hook, so scoping is
	# applied explicitly via bank_filters() — the single source of truth that
	# fails closed for users with no bank binding.
	filters = bank_filters()

	product_counts = frappe.get_all(
		"A2C Loan Product",
		filters=filters,
		fields=["status", {"COUNT": "name", "as": "count"}],
		group_by="status",
	)
	total_products = sum(item.count for item in product_counts)
	active_products = sum(item.count for item in product_counts if item.status == "Active")

	app_counts = frappe.get_all(
		"A2C Loan Application",
		filters=filters,
		fields=["status", {"COUNT": "name", "as": "count"}, {"SUM": "approved_amount", "as": "total_amount"}],
		group_by="status",
	)
	total_applications = sum(item.count for item in app_counts)
	pending_applications = sum(
		item.count for item in app_counts if item.status in ["Submitted", "Under Review"]
	)
	approved_applications = sum(item.count for item in app_counts if item.status == "Approved")
	total_approved_amount = sum((item.total_amount or 0) for item in app_counts if item.status == "Approved")

	return success_response(
		data={
			"stats": {
				"total_products": total_products,
				"active_products": active_products,
				"total_applications": total_applications,
				"pending_applications": pending_applications,
				"approved_applications": approved_applications,
				"total_approved_amount": total_approved_amount,
			}
		}
	)
