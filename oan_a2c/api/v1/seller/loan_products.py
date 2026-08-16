import frappe
from frappe import _
from pydantic import BaseModel, Field, field_validator, model_validator

from oan_a2c.a2c_marketplace.permissions import (
	BankNotActive,
	bank_scoped,
	get_user_bank,
	is_bank_unbound,
)
from oan_a2c.a2c_marketplace.roles import BANK_ADMIN_ROLE
from oan_a2c.api.utils import handle_api_errors, success_response, to_tz_aware_iso, validate_request


def assert_bank_active(bank: str | None) -> None:
	"""Block catalog writes for a bank that isn't Active (e.g. In Review/Suspended).

	Bank binding alone (enforced by @bank_scoped) only proves the user belongs to a
	bank, not that the bank is approved to trade. A None bank is an unbound admin,
	who is exempt. Raises BankNotActive (a PermissionError subclass) so the API
	returns a distinct BANK_NOT_ACTIVE code with a KYC/approval hint instead of an
	opaque "Permission denied".
	"""
	if not bank:
		return
	status = frappe.db.get_value("A2C Participating Bank", bank, "status")
	if status != "Active":
		frappe.throw(
			_("Your bank is not active yet (currently {0}). Complete onboarding to manage products.").format(
				_(status or "Unknown")
			),
			BankNotActive,
		)


from oan_a2c.a2c_marketplace.doctype_schemas import ProductMetaSchema, SingleProductSchema


class CreateProductSchema(BaseModel):
	product_name: str | None = Field(None, min_length=1, max_length=140)
	min_interest_rate: float | None = Field(None, ge=0, le=20.0)
	max_interest_rate: float | None = Field(None, ge=0, le=20.0)
	min_amount: int | None = Field(None, ge=0, le=999999)
	max_amount: int | None = Field(None, ge=0, le=999999)
	tenure_months: int | None = Field(None, ge=1, le=1200)
	description: str | None = Field(None, max_length=2000)
	image: str | None = Field(None, max_length=500)
	product_meta: list[ProductMetaSchema] | None = None

	products: list[SingleProductSchema] | None = None

	@field_validator("min_interest_rate", "max_interest_rate")
	@classmethod
	def validate_decimals(cls, v):
		if v is not None and round(v, 2) != v:
			raise ValueError("Interest rate must have at most 2 decimal places.")
		return v

	@model_validator(mode="after")
	def check_payload(self):
		if self.products:
			if len(self.products) > 10:
				raise ValueError("A maximum of 10 products can be created at once in bulk.")
			return self
		if (
			not self.product_name
			or self.min_interest_rate is None
			or self.max_amount is None
			or self.tenure_months is None
		):
			raise ValueError("Either 'products' array or single product required fields must be provided")

		if self.min_interest_rate is not None and self.max_interest_rate is not None:
			if self.min_interest_rate > self.max_interest_rate:
				raise ValueError("min_interest_rate cannot be greater than max_interest_rate.")
		if self.min_amount is not None and self.max_amount is not None:
			if self.min_amount > self.max_amount:
				raise ValueError("min_amount cannot be greater than max_amount.")
		return self


class UpdateProductSchema(BaseModel):
	product_id: str = Field(..., min_length=1, max_length=140)
	product_name: str | None = Field(None, max_length=140)
	min_interest_rate: float | None = Field(None, ge=0, le=20.0)
	max_interest_rate: float | None = Field(None, ge=0, le=20.0)
	min_amount: int | None = Field(None, ge=0, le=999999)
	max_amount: int | None = Field(None, ge=0, le=999999)
	tenure_months: int | None = Field(None, ge=1, le=1200)
	description: str | None = Field(None, max_length=2000)
	image: str | None = Field(None, max_length=500)
	product_meta: list[ProductMetaSchema] | None = None

	@field_validator("min_interest_rate", "max_interest_rate")
	@classmethod
	def validate_decimals(cls, v):
		if v is not None and round(v, 2) != v:
			raise ValueError("Interest rate must have at most 2 decimal places.")
		return v

	@model_validator(mode="after")
	def validate_min_max_ordering(self):
		if self.min_interest_rate is not None and self.max_interest_rate is not None:
			if self.min_interest_rate > self.max_interest_rate:
				raise ValueError("min_interest_rate cannot be greater than max_interest_rate.")
		if self.min_amount is not None and self.max_amount is not None:
			if self.min_amount > self.max_amount:
				raise ValueError("min_amount cannot be greater than max_amount.")
		return self


class SetProductStatusSchema(BaseModel):
	product_id: str = Field(..., min_length=1, max_length=140)
	status: str = Field(..., pattern="^(Pending Approval|Active|Rejected)$")
	reason: str | None = Field(None, max_length=2000)

	@model_validator(mode="after")
	def validate_reason_required_for_approval_or_rejection(self):
		if self.status in ("Active", "Rejected") and not (self.reason and self.reason.strip()):
			raise ValueError(f"Please provide a reason when setting status to '{self.status}'.")
		return self


class GetProductSchema(BaseModel):
	product_id: str = Field(..., min_length=1, max_length=140)


class GetProductCommentSchema(BaseModel):
	product_id: str = Field(..., min_length=1, max_length=140)


@frappe.whitelist()
@validate_request(CreateProductSchema)
@handle_api_errors
@bank_scoped
def create_product(**kwargs):
	frappe.has_permission("A2C Loan Product", "create", throw=True)
	assert_bank_active(kwargs.get("bank"))

	products_data = kwargs.get("products") or [kwargs]
	created_ids = []
	bank = kwargs.get("bank")

	for p_data in products_data:
		doc = frappe.new_doc("A2C Loan Product")
		doc.product_name = p_data.get("product_name")
		doc.bank = bank
		doc.min_interest_rate = p_data.get("min_interest_rate")
		doc.max_interest_rate = p_data.get("max_interest_rate")
		doc.min_amount = p_data.get("min_amount")
		doc.max_amount = p_data.get("max_amount")
		doc.tenure_months = p_data.get("tenure_months")
		doc.description = p_data.get("description")
		doc.image = p_data.get("image")
		doc.status = "Pending Approval"

		p_meta = p_data.get("product_meta")
		if p_meta:
			for meta in p_meta:
				doc.append("product_meta", {"meta_key": meta["meta_key"], "meta_value": meta["meta_value"]})

		doc.insert(ignore_permissions=False)
		created_ids.append(doc.name)

	return success_response(data={"message": _("Products created"), "product_ids": created_ids})


@frappe.whitelist()
@validate_request(UpdateProductSchema)
@handle_api_errors
def update_product(**kwargs):
	product_id = kwargs.get("product_id")
	if not frappe.has_permission("A2C Loan Product", "write", product_id):
		frappe.throw(_("Not permitted"), frappe.PermissionError)

	if not is_bank_unbound():
		assert_bank_active(get_user_bank())

	doc = frappe.get_doc("A2C Loan Product", product_id)
	if kwargs.get("reason"):
		doc._status_reason = kwargs["reason"]

	direct_fields = [
		"product_name",
		"min_interest_rate",
		"max_interest_rate",
		"min_amount",
		"max_amount",
		"tenure_months",
		"description",
		"image",
	]
	for field in direct_fields:
		if field in kwargs and kwargs[field] is not None:
			setattr(doc, field, kwargs[field])

	if doc.min_interest_rate is not None and doc.max_interest_rate is not None:
		if float(doc.min_interest_rate) > float(doc.max_interest_rate):
			frappe.throw(
				_("min_interest_rate cannot be greater than max_interest_rate."), frappe.ValidationError
			)

	if doc.min_amount is not None and doc.max_amount is not None:
		if float(doc.min_amount) > float(doc.max_amount):
			frappe.throw(_("min_amount cannot be greater than max_amount."), frappe.ValidationError)

	doc.save(ignore_permissions=False)
	return success_response(
		data={
			"message": _("Product updated"),
			"product_id": doc.name,
			"status": doc.status,
		}
	)


@frappe.whitelist()
@validate_request(SetProductStatusSchema)
@handle_api_errors
def set_product_status(**kwargs):
	product_id = kwargs.get("product_id")
	status = kwargs.get("status")
	reason = kwargs.get("reason")
	if not frappe.has_permission("A2C Loan Product", "write", product_id):
		frappe.throw(_("Not permitted"), frappe.PermissionError)

	# Approval gate: activating a product is a Bank Admin / platform-admin action.
	# Bank Agents can draft and edit products (write) but cannot approve their own —
	# so the `Active` transition needs an explicit role check beyond `write`.
	if status == "Active" and not (is_bank_unbound() or BANK_ADMIN_ROLE in frappe.get_roles()):
		frappe.throw(_("Only a Bank Admin can approve (activate) a product."), frappe.PermissionError)

	if not is_bank_unbound():
		assert_bank_active(get_user_bank())

	doc = frappe.get_doc("A2C Loan Product", product_id)
	if reason:
		doc._status_reason = reason
	doc.status = status
	doc.save(ignore_permissions=False)

	return success_response(
		data={
			"message": _("Product status updated to {}").format(doc.status),
			"product_id": doc.name,
			"status": doc.status,
		}
	)


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
			"image",
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
		"image": doc.image,
		"bank": doc.bank,
		"creation": to_tz_aware_iso(doc.creation),
		"modified": to_tz_aware_iso(doc.modified),
		"product_meta": product_meta,
		"categories": categories,
		"tags": tags,
		"attributes": attributes,
	}

	return success_response(data={"product": product_data})


@frappe.whitelist()
@validate_request(GetProductCommentSchema)
@handle_api_errors
def get_product_comment(**kwargs):
	product_id = kwargs.get("product_id")
	if not frappe.has_permission("A2C Loan Product", "read", product_id):
		frappe.throw(_("Not permitted"), frappe.PermissionError)

	filters = {"loan_product": product_id}

	events = frappe.get_all(
		"A2C Loan Product Audit Event",
		filters=filters,
		fields=[
			"name",
			"creation",
			"event_type",
			"from_status",
			"to_status",
			"event_title",
			"event_description",
			"reason",
			"performed_by",
		],
		order_by="creation desc",
		limit_page_length=1,
	)

	for event in events:
		event.creation = to_tz_aware_iso(event.creation)

	return success_response(data={"comment": events})
