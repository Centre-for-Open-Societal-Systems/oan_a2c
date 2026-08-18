from typing import Literal

import frappe
from frappe import _

from oan_a2c.a2c_marketplace.roles import FARMER_ROLE
from oan_a2c.api.utils import handle_api_errors, validate_request, success_response, require_role, to_tz_aware_iso
from oan_a2c.api.v1.loan_applications import BrowseProductsSchema
from pydantic import BaseModel, Field

class SaveProductSchema(BaseModel):
	loan_product: str = Field(..., min_length=1, max_length=140)


# Sort keys are an allowlist rather than free text: order_by is interpolated into
# SQL, so anything the client can name has to be something we chose.
_SORT_COLUMNS = {
	"product_name": "product_name asc",
	"interest_low_high": "min_interest_rate asc",
	"interest_high_low": "max_interest_rate desc",
	"amount_low_high": "min_amount asc",
	"amount_high_low": "max_amount desc",
	"tenure_low_high": "tenure_months asc",
	"newest": "creation desc",
}


class FarmerCatalogSchema(BrowseProductsSchema):
	"""Browse params plus the filters the discovery UI actually offers.

	Anything the sidebar can select has to be filterable here, and anything not
	filterable here must not appear in the sidebar — a control that silently does
	nothing is worse than no control.
	"""

	category: str | None = Field(None, max_length=140)
	min_tenure_months: int | None = Field(None, ge=0, le=600)
	max_tenure_months: int | None = Field(None, ge=0, le=600)
	max_interest_rate: float | None = Field(None, ge=0, le=100)
	sort_by: Literal[
		"product_name",
		"interest_low_high",
		"interest_high_low",
		"amount_low_high",
		"amount_high_low",
		"tenure_low_high",
		"newest",
	] = "product_name"

class PaginationSchema(BaseModel):
	limit: int = Field(20, ge=1, le=100)
	start: int = Field(0, ge=0)

def _products_in_category(category: str) -> list[str]:
	"""Loan product ids carrying `category`.

	bank-scope-exempt: A2C Term Relationship is bank-scoped, so get_list returns
	nothing for a bank-bound farmer. Reading it directly is safe here because the
	ids are only ever used to narrow the *product* query below, which is itself
	permission-filtered — a product the farmer may not see cannot be pulled into
	the result by naming it here.
	"""
	return frappe.get_all(
		"A2C Term Relationship",
		filters={"term_type": "Category", "term_category": category},
		pluck="loan_product",
	)


@frappe.whitelist(allow_guest=False)
@validate_request(FarmerCatalogSchema)
@handle_api_errors
@require_role([FARMER_ROLE])
def list_catalog(**kwargs):
	"""Active loan products across every bank, for a signed-in farmer.

	The permission_query_conditions hook on A2C Loan Product automatically limits
	farmers to status='Active', and because farmers are not bank-bound, they see
	offerings from all participating banks.
	"""
	filters = {"status": "Active"}
	if kwargs.get("bank"):
		filters["bank"] = kwargs["bank"]
	if kwargs.get("loan_product"):
		filters["name"] = kwargs["loan_product"]
	if kwargs.get("search"):
		filters["product_name"] = ["like", f"%{kwargs['search']}%"]
	if kwargs.get("min_amount") is not None:
		filters["min_amount"] = [">=", float(kwargs["min_amount"])]
	if kwargs.get("max_amount") is not None:
		filters["max_amount"] = ["<=", float(kwargs["max_amount"])]
	if kwargs.get("max_interest_rate") is not None:
		filters["min_interest_rate"] = ["<=", float(kwargs["max_interest_rate"])]
	if kwargs.get("min_tenure_months") is not None:
		filters["tenure_months"] = [">=", int(kwargs["min_tenure_months"])]
	if kwargs.get("max_tenure_months") is not None:
		# Two bounds on one column need the range form; the dict above would drop
		# whichever was written second.
		if "tenure_months" in filters:
			filters["tenure_months"] = [
				"between",
				[int(kwargs["min_tenure_months"]), int(kwargs["max_tenure_months"])],
			]
		else:
			filters["tenure_months"] = ["<=", int(kwargs["max_tenure_months"])]

	if kwargs.get("category"):
		matching = _products_in_category(kwargs["category"])
		if not matching:
			pagination = {
				"page": (kwargs["start"] // kwargs["limit"]) + 1,
				"limit": kwargs["limit"],
				"total": 0,
				"total_pages": 0,
				"has_next": False,
			}
			return success_response(
				data={"products": []}, message="Catalog retrieved successfully", pagination=pagination
			)
		filters["name"] = ["in", matching]

	limit = kwargs["limit"]
	start = kwargs["start"]

	products = frappe.get_list(
		"A2C Loan Product",
		filters=filters,
		fields=[
			"name",
			"product_name",
			"slug",
			"bank",
			"min_interest_rate",
			"max_interest_rate",
			"min_amount",
			"max_amount",
			"tenure_months",
		],
		order_by=_SORT_COLUMNS[kwargs["sort_by"]],
		limit_page_length=limit,
		limit_start=start,
	)

	count_res = frappe.get_list(
		"A2C Loan Product",
		filters=filters,
		fields=[{"COUNT": "*"}],
		ignore_permissions=False,
	)
	total = count_res[0].get("COUNT(*)") if count_res else 0
	pagination = {
		"page": (start // limit) + 1,
		"limit": limit,
		"total": total,
		"total_pages": -(-total // limit),
		"has_next": start + limit < total,
	}

	return success_response(
		data={"products": products},
		message="Catalog retrieved successfully",
		pagination=pagination,
	)


@frappe.whitelist(allow_guest=False, methods=["POST"])
@validate_request(SaveProductSchema)
@handle_api_errors
@require_role([FARMER_ROLE])
def save_product(**kwargs):
	"""Bookmarks a loan product for the farmer."""
	user = frappe.session.user
	profile_name = frappe.db.get_value("A2C Farmer Profile", {"user": user}, "name")
	if not profile_name:
		frappe.throw(_("You must have a Farmer Profile to bookmark products."), frappe.ValidationError)

	loan_product = kwargs["loan_product"]
	if not frappe.db.exists("A2C Loan Product", loan_product):
		frappe.throw(_("Loan Product not found."), frappe.NotFoundError)

	if not frappe.db.exists("A2C Saved Product", {"farmer_profile": profile_name, "loan_product": loan_product}):
		doc = frappe.get_doc({
			"doctype": "A2C Saved Product",
			"farmer_profile": profile_name,
			"loan_product": loan_product
		})
		doc.insert(ignore_permissions=False)

	return success_response(message="Product saved successfully")


@frappe.whitelist(allow_guest=False, methods=["POST"])
@validate_request(SaveProductSchema)
@handle_api_errors
@require_role([FARMER_ROLE])
def unsave_product(**kwargs):
	"""Removes a bookmarked loan product for the farmer."""
	user = frappe.session.user
	profile_name = frappe.db.get_value("A2C Farmer Profile", {"user": user}, "name")
	if not profile_name:
		frappe.throw(_("You must have a Farmer Profile to bookmark products."), frappe.ValidationError)

	loan_product = kwargs["loan_product"]
	saved = frappe.db.get_value("A2C Saved Product", {"farmer_profile": profile_name, "loan_product": loan_product}, "name")
	if saved:
		frappe.delete_doc("A2C Saved Product", saved, ignore_permissions=False)

	return success_response(message="Product removed from saved list")


@frappe.whitelist(allow_guest=False)
@validate_request(PaginationSchema)
@handle_api_errors
@require_role([FARMER_ROLE])
def get_saved_products(**kwargs):
	"""Returns the farmer's bookmarked loan products."""
	user = frappe.session.user
	profile_name = frappe.db.get_value("A2C Farmer Profile", {"user": user}, "name")
	if not profile_name:
		return success_response(data={"products": []}, message="No saved products", pagination={"page": 1, "limit": 20, "total": 0, "total_pages": 0, "has_next": False})

	limit = kwargs["limit"]
	start = kwargs["start"]

	# We fetch the links from A2C Saved Product and join with A2C Loan Product details
	saved_docs = frappe.get_all(
		"A2C Saved Product",
		filters={"farmer_profile": profile_name},
		pluck="loan_product",
		order_by="creation desc",
		limit_page_length=limit,
		limit_start=start,
	)

	total = frappe.db.count("A2C Saved Product", filters={"farmer_profile": profile_name})

	products = []
	if saved_docs:
		products = frappe.get_all(
			"A2C Loan Product",
			filters={"name": ["in", saved_docs]},
			fields=[
				"name",
				"product_name",
				"slug",
				"bank",
				"min_interest_rate",
				"max_interest_rate",
				"min_amount",
				"max_amount",
				"tenure_months",
			],
		)
		# Sort them to match the recent creation order from A2C Saved Product
		order_map = {name: i for i, name in enumerate(saved_docs)}
		products.sort(key=lambda p: order_map.get(p.name, 999))

	pagination = {
		"page": (start // limit) + 1,
		"limit": limit,
		"total": total,
		"total_pages": -(-total // limit),
		"has_next": start + limit < total,
	}

	return success_response(
		data={"products": products},
		message="Saved products retrieved successfully",
		pagination=pagination,
	)



@frappe.whitelist(allow_guest=False)
@handle_api_errors
@require_role([FARMER_ROLE])
def get_catalog_facets(**kwargs):
	"""Filter options for the discovery sidebar, derived from live catalog data.

	Every option returned here is backed by at least one product the caller can
	actually see, so the sidebar can never offer a filter that returns nothing --
	and can never offer one the catalog endpoint does not implement. An empty
	catalog yields empty facets rather than a fabricated default list.

	Note what is absent: there is no region facet, because A2C Loan Product has no
	region field. A product is offered by a bank, not into a place.
	"""
	frappe.has_permission("A2C Loan Product", "read", throw=True)

	# get_list applies loan_product_scope_query: Active only, across all banks.
	products = frappe.get_list(
		"A2C Loan Product",
		filters={"status": "Active"},
		fields=["name", "tenure_months", "min_amount", "max_amount", "min_interest_rate", "max_interest_rate"],
		limit_page_length=0,
	)

	if not products:
		return success_response(
			data={"categories": [], "tenures": [], "amount_range": None, "max_interest_rate": None},
			message="No active products",
		)

	product_names = [p["name"] for p in products]

	# bank-scope-exempt: A2C Term Relationship is bank-scoped and a farmer is
	# bank-bound, so get_list returns nothing. Restricted to product_names, which
	# came from the permission-filtered query above -- nothing invisible leaks in.
	term_rows = frappe.get_all(
		"A2C Term Relationship",
		filters={"loan_product": ["in", product_names], "term_type": "Category"},
		fields=["term_category"],
	)
	counts: dict[str, int] = {}
	for row in term_rows:
		if row.term_category:
			counts[row.term_category] = counts.get(row.term_category, 0) + 1
	categories = [
		{"name": name, "count": count}
		for name, count in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
	]

	tenures = sorted({int(p["tenure_months"]) for p in products if p.get("tenure_months")})

	min_amounts = [float(p["min_amount"]) for p in products if p.get("min_amount") is not None]
	max_amounts = [float(p["max_amount"]) for p in products if p.get("max_amount") is not None]
	rates = [float(p["max_interest_rate"]) for p in products if p.get("max_interest_rate") is not None]

	return success_response(
		data={
			"categories": categories,
			"tenures": tenures,
			"amount_range": {
				"min": min(min_amounts) if min_amounts else 0.0,
				"max": max(max_amounts) if max_amounts else 0.0,
			},
			"max_interest_rate": max(rates) if rates else None,
		},
		message="Catalog facets retrieved successfully",
	)
