import frappe
from frappe import _
from pydantic import BaseModel, Field

from oan_a2c.a2c_marketplace.permissions import bank_scoped
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


class GetProductSchema(BaseModel):
	product_id: str


@frappe.whitelist()
@validate_request(CreateProductSchema)
@handle_api_errors
@bank_scoped
def create_product(**kwargs):
	frappe.has_permission("A2C Loan Product", "create", throw=True)

	product_meta = kwargs.get("product_meta")

	doc = frappe.new_doc("A2C Loan Product")
	doc.product_name = kwargs.get("product_name")
	doc.bank = kwargs.get("bank")
	doc.min_interest_rate = kwargs.get("min_interest_rate")
	doc.max_interest_rate = kwargs.get("max_interest_rate")
	doc.min_amount = kwargs.get("min_amount")
	doc.max_amount = kwargs.get("max_amount")
	doc.tenure_months = kwargs.get("tenure_months")
	doc.description = kwargs.get("description")
	doc.status = "Draft"

	if product_meta:
		for meta in product_meta:
			doc.append("product_meta", {"meta_key": meta["meta_key"], "meta_value": meta["meta_value"]})

	doc.insert(ignore_permissions=False)
	return success_response(data={"message": _("Product created"), "product_id": doc.name})


@frappe.whitelist()
@validate_request(UpdateProductSchema)
@handle_api_errors
def update_product(**kwargs):
	product_id = kwargs.get("product_id")
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
def set_product_status(**kwargs):
	product_id = kwargs.get("product_id")
	status = kwargs.get("status")
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
	page: int = 1,
	page_size: int = 20,
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
		# get_list applies the A2C Term Relationship bank scope (it is bank-scoped),
		# so a caller cannot enumerate another bank's taxonomy via category search.
		cat_ids = frappe.get_list(
			"A2C Term Relationship",
			filters={"term_type": "Category", "term_category": ["like", f"%{category}%"]},
			pluck="loan_product",
		)
		matching_product_ids = set(cat_ids)

	if tag:
		tag_ids = frappe.get_list(
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
			pagination = {
				"page": page,
				"page_size": page_size,
				"total": 0,
				"total_pages": 0,
				"has_next": False,
			}
			return success_response(data={"products": []}, pagination=pagination)
		base_filters["name"] = ["in", list(matching_product_ids)]

	offset = (page - 1) * page_size

	# Total count for pagination (get_list enforces DocPerm + bank scope).
	count_res = frappe.get_list(
		"A2C Loan Product",
		filters=base_filters,
		fields=[{"COUNT": "*"}],
	)
	total_records = count_res[0].get("COUNT(*)") if count_res else 0

	# get_list (not get_all) enforces DocPerm AND runs the bank_scope_query hook,
	# so callers without read permission (e.g. Development Agent, which has no
	# DocPerm on the seller catalog) get zero rows, and bank isolation is applied
	# automatically -- no manual bank_filters() to forget.
	products = frappe.get_list(
		"A2C Loan Product",
		filters=base_filters,
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
		limit_page_length=page_size,
		limit_start=offset,
	)

	if products:
		product_names = [p["name"] for p in products]

		# Batch fetch categories for each product (get_list applies the bank scope).
		cat_rows = frappe.get_list(
			"A2C Term Relationship",
			filters={"loan_product": ["in", product_names], "term_type": "Category"},
			fields=["loan_product", "term_category"],
		)
		categories_map = {}
		for row in cat_rows:
			categories_map.setdefault(row.loan_product, []).append(row.term_category)

		# Batch fetch application counts. get_list enforces DocPerm + bank scope.
		app_counts = frappe.get_list(
			"A2C Loan Application",
			filters={"loan_product": ["in", product_names]},
			fields=["loan_product", {"COUNT": "*"}],
			group_by="loan_product",
		)
		counts_map = {row.loan_product: row.get("COUNT(*)") for row in app_counts}

		for p in products:
			p["categories"] = categories_map.get(p["name"], [])
			p["applications_count"] = counts_map.get(p["name"], 0)

	total_pages = -(-total_records // page_size)
	has_next = offset + page_size < total_records

	pagination = {
		"page": page,
		"page_size": page_size,
		"total": total_records,
		"total_pages": total_pages,
		"has_next": has_next,
	}

	return success_response(data={"products": products}, pagination=pagination)


@frappe.whitelist()
@validate_request(GetProductSchema)
@handle_api_errors
def get_product(**kwargs):
	product_id = kwargs.get("product_id")
	if not frappe.has_permission("A2C Loan Product", "read", product_id):
		frappe.throw(_("Not permitted"), frappe.PermissionError)

	doc = frappe.get_doc("A2C Loan Product", product_id)

	product_meta = []
	for meta in getattr(doc, "product_meta", []):
		product_meta.append({"meta_key": meta.meta_key, "meta_value": meta.meta_value})

	# get_list applies the bank scope on these bank-scoped taxonomy doctypes;
	# product_id was already authorized via has_permission above.
	categories = frappe.get_list(
		"A2C Term Relationship",
		filters={"loan_product": product_id, "term_type": "Category"},
		pluck="term_category",
	)

	tags = frappe.get_list(
		"A2C Term Relationship",
		filters={"loan_product": product_id, "term_type": "Tag"},
		pluck="term_tag",
	)

	lookups = frappe.get_list(
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
