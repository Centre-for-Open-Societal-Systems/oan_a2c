from typing import Any

import frappe
from frappe import _
from pydantic import BaseModel, Field

from oan_a2c.a2c_marketplace.permissions import bank_filters, get_user_bank
from oan_a2c.api.utils import handle_api_errors, success_response, validate_request


class ProductMetaSchema(BaseModel):
	meta_key: str
	meta_value: str


class CreateProductSchema(BaseModel):
	product_name: str
	min_interest_rate: float
	max_interest_rate: float | None = None
	min_amount: float | None = None
	max_amount: float
	tenure_months: int
	description: str | None = None
	product_meta: list[ProductMetaSchema] | None = None


class UpdateProductSchema(BaseModel):
	product_id: str
	product_name: str | None = None
	min_interest_rate: float | None = None
	max_interest_rate: float | None = None
	min_amount: float | None = None
	max_amount: float | None = None
	tenure_months: int | None = None
	description: str | None = None
	product_meta: list[ProductMetaSchema] | None = None


class SetProductStatusSchema(BaseModel):
	product_id: str
	status: str = Field(..., pattern="^(Draft|Active|Archived)$")


@frappe.whitelist()
@validate_request(CreateProductSchema)
@handle_api_errors
def create_product(
	product_name: str,
	min_interest_rate: float,
	max_amount: float,
	tenure_months: int,
	max_interest_rate: float | None = None,
	min_amount: float | None = None,
	description: str | None = None,
	product_meta: list | None = None,
):
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
			doc.append("product_meta", {"meta_key": meta["meta_key"], "meta_value": meta["meta_value"]})

	doc.insert(ignore_permissions=False)
	return success_response(data={"message": _("Product created"), "product_id": doc.name})


@frappe.whitelist()
@validate_request(UpdateProductSchema)
@handle_api_errors
def update_product(product_id: str, **kwargs):
	if not frappe.has_permission("A2C Loan Product", "write", product_id):
		frappe.throw(_("Not permitted"), frappe.PermissionError)

	doc = frappe.get_doc("A2C Loan Product", product_id)

	direct_fields = [
		"product_name",
		"min_interest_rate",
		"max_interest_rate",
		"min_amount",
		"max_amount",
		"tenure_months",
		"description",
	]
	for field in direct_fields:
		if field in kwargs and kwargs[field] is not None:
			setattr(doc, field, kwargs[field])

	if "product_meta" in kwargs and kwargs["product_meta"] is not None:
		doc.set("product_meta", [])
		for meta in kwargs["product_meta"]:
			doc.append("product_meta", {"meta_key": meta["meta_key"], "meta_value": meta["meta_value"]})

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
def list_products(
	status: str | None = None,
	search: str | None = None,
	category: str | None = None,
	tag: str | None = None,
	min_interest_rate: float | None = None,
	max_interest_rate: float | None = None,
	min_amount: float | None = None,
	max_amount: float | None = None,
	tenure_months: int | None = None,
	limit: int = 20,
	start: int = 0,
):
	base_filters = {}

	if status:
		base_filters["status"] = status

	if search:
		base_filters["product_name"] = ["like", f"%{search}%"]

	if min_interest_rate is not None:
		base_filters["min_interest_rate"] = [">=", float(min_interest_rate)]

	if max_interest_rate is not None:
		base_filters["max_interest_rate"] = ["<=", float(max_interest_rate)]

	if min_amount is not None:
		base_filters["min_amount"] = [">=", float(min_amount)]

	if max_amount is not None:
		base_filters["max_amount"] = ["<=", float(max_amount)]

	if tenure_months is not None:
		base_filters["tenure_months"] = int(tenure_months)

	# If category or tag filter is passed, find matching product IDs in 1 query
	if category or tag:
		rel_filters = {}
		if category:
			rel_filters["term_type"] = "Category"
			rel_filters["term_category"] = category
		if tag:
			rel_filters["term_type"] = "Tag"
			rel_filters["term_tag"] = tag

		matching_product_ids = frappe.get_all(
			"A2C Term Relationship",
			filters=rel_filters,
			pluck="loan_product",
		)
		if not matching_product_ids:
			return success_response(data={"products": [], "count": 0})
		base_filters["name"] = ["in", matching_product_ids]

	filters = bank_filters(base=base_filters)

	# frappe.get_all bypasses the bank_scope_query hook, so scope explicitly via bank_filters().
	products = frappe.get_all(
		"A2C Loan Product",
		filters=filters,
		fields=[
			"name",
			"product_name",
			"slug",
			"status",
			"min_interest_rate",
			"max_interest_rate",
			"min_amount",
			"max_amount",
			"tenure_months",
			"creation",
		],
		order_by="creation desc",
		limit_page_length=limit,
		limit_start=start,
	)

	return success_response(data={"products": products, "count": len(products)})
