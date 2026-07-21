import frappe
from frappe import _
from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
from oan_a2c.api.utils import handle_api_errors, success_response, validate_request
from oan_a2c.a2c_marketplace.permissions import get_user_bank

class ProductMetaSchema(BaseModel):
	meta_key: str
	meta_value: str

class CreateProductSchema(BaseModel):
	product_name: str
	min_interest_rate: float
	max_interest_rate: Optional[float] = None
	min_amount: Optional[float] = None
	max_amount: float
	tenure_months: int
	description: Optional[str] = None
	product_meta: Optional[List[ProductMetaSchema]] = []

class UpdateProductSchema(BaseModel):
	product_name: Optional[str] = None
	min_interest_rate: Optional[float] = None
	max_interest_rate: Optional[float] = None
	min_amount: Optional[float] = None
	max_amount: Optional[float] = None
	tenure_months: Optional[int] = None
	description: Optional[str] = None
	product_meta: Optional[List[ProductMetaSchema]] = None

class SetProductStatusSchema(BaseModel):
	status: str = Field(..., pattern="^(Draft|Active|Archived)$")

@frappe.whitelist()
@validate_request(CreateProductSchema)
@handle_api_errors
def create_product(product_name: str, min_interest_rate: float, max_amount: float, tenure_months: int, 
				   max_interest_rate: float = None, min_amount: float = None, description: str = None, 
				   product_meta: list = None):
	bank = get_user_bank()
	if not bank:
		frappe.throw(_("User is not associated with any bank."), frappe.PermissionError)

	doc = frappe.new_doc("A2C Loan Product")
	doc.product_name = product_name
	doc.bank = bank
	doc.min_interest_rate = min_interest_rate
	doc.max_interest_rate = max_interest_rate
	doc.min_amount = min_amount
	doc.max_amount = max_amount
	doc.tenure_months = tenure_months
	doc.description = description
	doc.status = "Draft"

	if product_meta:
		for meta in product_meta:
			doc.append("product_meta", {
				"meta_key": meta["meta_key"],
				"meta_value": meta["meta_value"]
			})
	
	doc.insert(ignore_permissions=False)
	return success_response(data={"message": _("Product created"), "product_id": doc.name})

@frappe.whitelist()
@validate_request(UpdateProductSchema)
@handle_api_errors
def update_product(product_id: str, **kwargs):
	if not frappe.has_permission("A2C Loan Product", "write", product_id):
		frappe.throw(_("Not permitted"), frappe.PermissionError)
		
	doc = frappe.get_doc("A2C Loan Product", product_id)
	
	direct_fields = ["product_name", "min_interest_rate", "max_interest_rate", "min_amount", "max_amount", "tenure_months", "description"]
	for field in direct_fields:
		if field in kwargs and kwargs[field] is not None:
			setattr(doc, field, kwargs[field])
			
	if "product_meta" in kwargs and kwargs["product_meta"] is not None:
		doc.set("product_meta", [])
		for meta in kwargs["product_meta"]:
			doc.append("product_meta", {
				"meta_key": meta["meta_key"],
				"meta_value": meta["meta_value"]
			})
			
	doc.save(ignore_permissions=False)
	return success_response(data={"message": _("Product updated"), "product_id": doc.name})

@frappe.whitelist()
@validate_request(SetProductStatusSchema)
@handle_api_errors
def set_product_status(product_id: str, status: str):
	if not frappe.has_permission("A2C Loan Product", "write", product_id):
		frappe.throw(_("Not permitted"), frappe.PermissionError)
		
	doc = frappe.get_doc("A2C Loan Product", product_id)
	doc.status = status
	doc.save(ignore_permissions=False)
	
	return success_response(data={"message": _("Product status updated to {}").format(status)})

@frappe.whitelist()
@handle_api_errors
def list_products():
	products = frappe.get_all(
		"A2C Loan Product", 
		fields=["name", "product_name", "status", "min_interest_rate", "max_interest_rate", "min_amount", "max_amount", "tenure_months"],
		order_by="creation desc"
	)
	return success_response(data={"products": products})

@frappe.whitelist()
@handle_api_errors
def get_product(product_id: str):
	if not frappe.has_permission("A2C Loan Product", "read", product_id):
		frappe.throw(_("Not permitted"), frappe.PermissionError)
		
	doc = frappe.get_doc("A2C Loan Product", product_id)
	
	meta_dict = {m.meta_key: m.meta_value for m in doc.get("product_meta", [])}
	
	categories = frappe.get_all(
		"A2C Term Relationship",
		filters={"loan_product": product_id, "term_type": "Category"},
		fields=["term_category"]
	)
	
	tags = frappe.get_all(
		"A2C Term Relationship",
		filters={"loan_product": product_id, "term_type": "Tag"},
		fields=["term_tag"]
	)
	
	attributes_raw = frappe.get_all(
		"A2C Loan Product Attribute Lookup",
		filters={"loan_product": product_id},
		fields=["taxonomy", "term_id"]
	)
	
	attributes_dict = {}
	for attr in attributes_raw:
		if attr.taxonomy not in attributes_dict:
			attributes_dict[attr.taxonomy] = []
		attributes_dict[attr.taxonomy].append(attr.term_id)
		
	product_data = {
		"id": doc.name,
		"product_name": doc.product_name,
		"bank": doc.bank,
		"status": doc.status,
		"min_interest_rate": doc.min_interest_rate,
		"max_interest_rate": doc.max_interest_rate,
		"min_amount": doc.min_amount,
		"max_amount": doc.max_amount,
		"tenure_months": doc.tenure_months,
		"description": doc.description,
		"meta": meta_dict,
		"categories": [c.term_category for c in categories],
		"tags": [t.term_tag for t in tags],
		"attributes": attributes_dict
	}
	
	return success_response(data={"product": product_data})
