import frappe
from frappe import _
from pydantic import BaseModel
from typing import List, Dict
from oan_a2c.api.utils import handle_api_errors, success_response, validate_request

class SetTermsSchema(BaseModel):
	product_id: str
	term_ids: List[str]

class SetAttributesSchema(BaseModel):
	product_id: str
	attributes: Dict[str, List[str]]

@frappe.whitelist()
@handle_api_errors
def get_categories():
	categories = frappe.get_all("A2C Term Category", fields=["name as term_id", "parent_category"])
	for cat in categories:
		term = frappe.get_value("A2C Term Category", cat.term_id, "term")
		cat.term_name = frappe.get_value("A2C Term", term, "term_name") if term else cat.term_id
	return success_response(data={"categories": categories})

@frappe.whitelist()
@handle_api_errors
def get_tags():
	tags = frappe.get_all("A2C Term Tag", fields=["name as term_id"])
	for tag in tags:
		term = frappe.get_value("A2C Term Tag", tag.term_id, "term")
		tag.term_name = frappe.get_value("A2C Term", term, "term_name") if term else tag.term_id
	return success_response(data={"tags": tags})

@frappe.whitelist()
@validate_request(SetTermsSchema)
@handle_api_errors
def set_product_categories(product_id: str, term_ids: list):
	if not frappe.has_permission("A2C Loan Product", "write", product_id):
		frappe.throw(_("Not permitted"), frappe.PermissionError)
		
	product = frappe.get_doc("A2C Loan Product", product_id)
	
	frappe.db.delete("A2C Term Relationship", {
		"loan_product": product_id,
		"term_type": "Category"
	})
	
	for term_id in term_ids:
		if not frappe.db.exists("A2C Term Category", term_id):
			continue
			
		rel = frappe.new_doc("A2C Term Relationship")
		rel.loan_product = product_id
		rel.term_type = "Category"
		rel.term_category = term_id
		rel.bank = product.bank
		rel.insert(ignore_permissions=False)
		
	return success_response(data={"message": _("Categories updated")})

@frappe.whitelist()
@validate_request(SetTermsSchema)
@handle_api_errors
def set_product_tags(product_id: str, term_ids: list):
	if not frappe.has_permission("A2C Loan Product", "write", product_id):
		frappe.throw(_("Not permitted"), frappe.PermissionError)
		
	product = frappe.get_doc("A2C Loan Product", product_id)
	
	frappe.db.delete("A2C Term Relationship", {
		"loan_product": product_id,
		"term_type": "Tag"
	})
	
	for term_id in term_ids:
		if not frappe.db.exists("A2C Term Tag", term_id):
			continue
			
		rel = frappe.new_doc("A2C Term Relationship")
		rel.loan_product = product_id
		rel.term_type = "Tag"
		rel.term_tag = term_id
		rel.bank = product.bank
		rel.insert(ignore_permissions=False)
		
	return success_response(data={"message": _("Tags updated")})

@frappe.whitelist()
@validate_request(SetAttributesSchema)
@handle_api_errors
def set_product_attributes(product_id: str, attributes: dict):
	if not frappe.has_permission("A2C Loan Product", "write", product_id):
		frappe.throw(_("Not permitted"), frappe.PermissionError)
		
	product = frappe.get_doc("A2C Loan Product", product_id)
	
	frappe.db.delete("A2C Loan Product Attribute Lookup", {
		"loan_product": product_id
	})
	
	for taxonomy, term_ids in attributes.items():
		for term_id in term_ids:
			if not frappe.db.exists("A2C Term", term_id):
				continue
				
			lookup = frappe.new_doc("A2C Loan Product Attribute Lookup")
			lookup.loan_product = product_id
			lookup.bank = product.bank
			lookup.taxonomy = taxonomy
			lookup.term_id = term_id
			lookup.accepting = 1
			lookup.insert(ignore_permissions=True)
			
	return success_response(data={"message": _("Attributes updated")})
