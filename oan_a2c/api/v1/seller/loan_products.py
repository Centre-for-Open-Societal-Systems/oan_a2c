import frappe
from frappe import _
from pydantic import BaseModel, Field

from oan_a2c.a2c_marketplace.permissions import bank_filters
from oan_a2c.api.utils import bank_scoped, handle_api_errors, success_response, validate_request


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


class GetProductSchema(BaseModel):
	product_id: str


@frappe.whitelist()
@validate_request(CreateProductSchema)
@handle_api_errors
@bank_scoped
def create_product(
	product_name: str,
	min_interest_rate: float,
	max_amount: float,
	tenure_months: int,
	max_interest_rate: float | None = None,
	min_amount: float | None = None,
	description: str | None = None,
	product_meta: list | None = None,
	bank: str | None = None,
):
	frappe.has_permission("A2C Loan Product", "create", throw=True)

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

	# If category or tag filter is passed, find matching product IDs
	matching_product_ids = None

	if category:
		cat_ids = frappe.get_all(
			"A2C Term Relationship",
			filters={"term_type": "Category", "term_category": ["like", f"%{category}%"]},
			pluck="loan_product",
		)
		matching_product_ids = set(cat_ids)

	if tag:
		tag_ids = frappe.get_all(
			"A2C Term Relationship",
			filters={"term_type": "Tag", "term_tag": ["like", f"%{tag}%"]},
			pluck="loan_product",
		)
		if matching_product_ids is None:
			matching_product_ids = set(tag_ids)
		else:
			matching_product_ids.intersection_update(tag_ids)

	if category or tag:
		if not matching_product_ids:
			return success_response(data={"products": [], "count": 0})
		base_filters["name"] = ["in", list(matching_product_ids)]

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


@frappe.whitelist()
@validate_request(GetProductSchema)
@handle_api_errors
def get_product(product_id: str):
	if not frappe.has_permission("A2C Loan Product", "read", product_id):
		frappe.throw(_("Not permitted"), frappe.PermissionError)

	doc = frappe.get_doc("A2C Loan Product", product_id)

	product_meta = []
	for meta in getattr(doc, "product_meta", []):
		product_meta.append({"meta_key": meta.meta_key, "meta_value": meta.meta_value})

	categories = frappe.get_all(
		"A2C Term Relationship",
		filters={"loan_product": product_id, "term_type": "Category"},
		pluck="term_category",
	)

	tags = frappe.get_all(
		"A2C Term Relationship",
		filters={"loan_product": product_id, "term_type": "Tag"},
		pluck="term_tag",
	)

	lookups = frappe.get_all(
		"A2C Loan Product Attribute Lookup",
		filters={"loan_product": product_id},
		fields=["taxonomy", "term_id"],
	)
	attributes = {}
	for lookup in lookups:
		tax = lookup.taxonomy
		if tax not in attributes:
			attributes[tax] = []
		attributes[tax].append(lookup.term_id)

	product_data = {
		"name": doc.name,
		"product_name": doc.product_name,
		"slug": doc.slug,
		"status": doc.status,
		"min_interest_rate": doc.min_interest_rate,
		"max_interest_rate": doc.max_interest_rate,
		"min_amount": doc.min_amount,
		"max_amount": doc.max_amount,
		"tenure_months": doc.tenure_months,
		"description": doc.description,
		"bank": doc.bank,
		"creation": str(doc.creation) if doc.creation else None,
		"modified": str(doc.modified) if doc.modified else None,
		"product_meta": product_meta,
		"categories": categories,
		"tags": tags,
		"attributes": attributes,
	}

	return success_response(data={"product": product_data})
