import frappe
from frappe import _
from typing import Optional
from pydantic import BaseModel, Field
from oan_a2c.api.utils import handle_api_errors, success_response, validate_request

class UpdateStatusSchema(BaseModel):
	application_id: str
	status: str = Field(..., pattern="^(Submitted|Under Review|Approved|Rejected|Disbursed)$")
	
class AssignApplicantSchema(BaseModel):
	application_id: str
	assigned_to: str

@frappe.whitelist()
@handle_api_errors
def list_applicants(limit: int = 20, start: int = 0, status: str = None):
	filters = {}
	if status:
		filters["status"] = status
		
	# query hooks handle the bank tenant isolation
	applications = frappe.get_all(
		"A2C Loan Application",
		filters=filters,
		fields=["name", "creation", "status", "requested_amount", "loan_product", "customer_name"],
		limit_start=start,
		limit_page_length=limit,
		order_by="creation desc"
	)
	return success_response(data={"applicants": applications})

@frappe.whitelist()
@handle_api_errors
def get_applicants_for_product(product_id: str, limit: int = 20, start: int = 0):
	if not frappe.has_permission("A2C Loan Product", "read", product_id):
		frappe.throw(_("Not permitted"), frappe.PermissionError)
		
	product = frappe.get_value("A2C Loan Product", product_id, "product_name")
		
	applications = frappe.get_all(
		"A2C Loan Application",
		filters={"loan_product": product},
		fields=["name", "creation", "status", "requested_amount", "customer_name"],
		limit_start=start,
		limit_page_length=limit,
		order_by="creation desc"
	)
	return success_response(data={"applicants": applications})

@frappe.whitelist()
@handle_api_errors
def search_applicants(query: str, limit: int = 20, start: int = 0):
	# search by name or customer_name
	applications = frappe.get_all(
		"A2C Loan Application",
		filters=[
			["name", "like", f"%{query}%"]
		],
		or_filters=[
			["customer_name", "like", f"%{query}%"]
		],
		fields=["name", "creation", "status", "requested_amount", "loan_product", "customer_name"],
		limit_start=start,
		limit_page_length=limit,
		order_by="creation desc"
	)
	return success_response(data={"applicants": applications})

@frappe.whitelist()
@validate_request(UpdateStatusSchema)
@handle_api_errors
def update_status(application_id: str, status: str):
	if not frappe.has_permission("A2C Loan Application", "write", application_id):
		frappe.throw(_("Not permitted"), frappe.PermissionError)
		
	doc = frappe.get_doc("A2C Loan Application", application_id)
	doc.status = status
	doc.save()
	
	return success_response(data={"message": _("Status updated to {}").format(status)})

@frappe.whitelist()
@validate_request(AssignApplicantSchema)
@handle_api_errors
def assign_applicant(application_id: str, assigned_to: str):
	if not frappe.has_permission("A2C Loan Application", "write", application_id):
		frappe.throw(_("Not permitted"), frappe.PermissionError)
		
	from frappe.desk.form.assign_to import add as add_assign
	
	add_assign({
		"assign_to": [assigned_to],
		"doctype": "A2C Loan Application",
		"name": application_id,
		"description": "Assigned via Seller API"
	})
	
	return success_response(data={"message": _("Applicant assigned successfully")})

@frappe.whitelist()
@handle_api_errors
def unassign_applicant(application_id: str, unassign_from: str):
	if not frappe.has_permission("A2C Loan Application", "write", application_id):
		frappe.throw(_("Not permitted"), frappe.PermissionError)
		
	from frappe.desk.form.assign_to import remove as remove_assign
	
	remove_assign("A2C Loan Application", application_id, unassign_from)
	
	return success_response(data={"message": _("Applicant unassigned successfully")})
