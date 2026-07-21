import frappe
from frappe import _
from oan_a2c.api.utils import handle_api_errors, success_response

@frappe.whitelist()
@handle_api_errors
def get_stats():
	total_products = frappe.db.count("A2C Loan Product")
	active_products = frappe.db.count("A2C Loan Product", {"status": "Active"})
	
	total_applications = frappe.db.count("A2C Loan Application")
	pending_applications = frappe.db.count("A2C Loan Application", {"status": ["in", ["Submitted", "Under Review"]]})
	approved_applications = frappe.db.count("A2C Loan Application", {"status": "Approved"})
	
	approved_amount_data = frappe.get_all(
		"A2C Loan Application",
		filters={"status": "Approved"},
		fields=["sum(approved_amount) as total_approved_amount"]
	)
	total_approved_amount = approved_amount_data[0].total_approved_amount if approved_amount_data else 0
	
	return success_response(data={"stats": {
		"total_products": total_products,
		"active_products": active_products,
		"total_applications": total_applications,
		"pending_applications": pending_applications,
		"approved_applications": approved_applications,
		"total_approved_amount": total_approved_amount or 0
	}})
