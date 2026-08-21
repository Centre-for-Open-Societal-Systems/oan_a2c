from typing import Literal

import frappe
from frappe import _

from oan_a2c.a2c_marketplace.roles import FARMER_ROLE, DEVELOPMENT_AGENT_ROLE
from oan_a2c.api.utils import (
	handle_api_errors,
	parse_multi_value,
	require_role,
	success_response,
	to_tz_aware_iso,
	validate_request,
)
from oan_a2c.api.v1.loan_applications import BrowseProductsSchema
from pydantic import BaseModel, Field

class SaveProductSchema(BaseModel):
	loan_product: str = Field(..., min_length=1, max_length=140)


# Sort keys are an allowlist rather than free text: order_by is interpolated into
# SQL, so anything the client can name has to be something we chose.
#
# Every key sorts by the column the discovery card actually shows. "Amount"
# used to order by min_amount while the card displays Max Amount, and "Interest:
# High to Low" used max_interest_rate while the card displays the headline (min)
# rate -- the rows did move, just not by anything the farmer could see, which
# reads as sorting doing nothing.
#
# `name asc` breaks ties on every key. Without it MariaDB may order equal rows
# differently between queries, so paging through a catalog where most products
# share a rate or tenure silently repeats some products and skips others.
_SORT_COLUMNS = {
	"product_name": "product_name asc, name asc",
	"interest_low_high": "min_interest_rate asc, name asc",
	"interest_high_low": "min_interest_rate desc, name asc",
	"amount_low_high": "max_amount asc, name asc",
	"amount_high_low": "max_amount desc, name asc",
	"tenure_low_high": "tenure_months asc, name asc",
	"newest": "creation desc, name asc",
}


class FarmerCatalogSchema(BrowseProductsSchema):
	"""Browse params plus the filters the discovery UI actually offers.

	Anything the sidebar can select has to be filterable here, and anything not
	filterable here must not appear in the sidebar — a control that silently does
	nothing is worse than no control.
	"""

	category: str | None = Field(None, max_length=140)
	# The sidebar offers the exact tenures present in the catalog, so it needs an
	# exact filter. Comma-separated ("6,12") because the chips are a multi-select;
	# min_/max_tenure_months stay for callers that genuinely want a range.
	tenure_months: str | None = Field(None, max_length=140)
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

def _annotate_saved(products: list[dict]) -> None:
	"""Stamp `is_saved` on each product for the calling farmer, in one query.

	A farmer with no profile yet has no bookmarks rather than an error -- browsing
	the catalog before a consent binds a profile is a normal state.
	"""
	if not products:
		return

	profile_name = frappe.db.get_value("A2C Farmer Profile", {"user": frappe.session.user}, "name")
	saved: set[str] = set()
	if profile_name:
		saved = set(
			frappe.get_all(
				"A2C Saved Product",
				filters={
					"farmer_profile": profile_name,
					"loan_product": ["in", [p["name"] for p in products]],
				},
				pluck="loan_product",
			)
		)

	for product in products:
		product["is_saved"] = product["name"] in saved


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
@require_role([FARMER_ROLE, DEVELOPMENT_AGENT_ROLE])
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
	# Amount is an overlap test between what the farmer wants to borrow and what
	# the product lends, not a containment test on the product's own bounds.
	# Comparing the product's max_amount against the farmer's ceiling did the
	# opposite of what the slider promises: asking for "up to ETB 100,000" threw
	# away every product that lends up to ETB 300,000 -- the ones that most
	# clearly cover the request -- and kept the ones capped at ETB 1,000.
	if kwargs.get("min_amount") is not None:
		filters["max_amount"] = [">=", float(kwargs["min_amount"])]
	if kwargs.get("max_amount") is not None:
		filters["min_amount"] = ["<=", float(kwargs["max_amount"])]
	# The rate filter tests min_interest_rate because that is the headline rate
	# the discovery card shows. get_catalog_facets derives the slider ceiling from
	# the same column so every position on the slider changes the result set.
	if kwargs.get("max_interest_rate") is not None:
		filters["min_interest_rate"] = ["<=", float(kwargs["max_interest_rate"])]

	# Exact tenures win over the range bounds: a farmer who picked "6 Mon" and
	# "12 Mon" means those two, not "anything up to 12". As an upper bound,
	# selecting the longest tenure on offer matched the entire catalog, which is
	# indistinguishable from the filter being ignored.
	selected_tenures = parse_multi_value(kwargs.get("tenure_months"))
	if selected_tenures:
		try:
			tenure_values = [int(t) for t in selected_tenures]
		except ValueError:
			frappe.throw(
				_("tenure_months must be whole numbers of months."), frappe.ValidationError
			)
		filters["tenure_months"] = ["in", tenure_values]
	else:
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
		if kwargs.get("loan_product"):
			# Both narrow `name`. Intersect rather than overwrite, or naming a
			# product id would smuggle it past the category filter.
			matching = [m for m in matching if m == kwargs["loan_product"]]
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

	# Bookmark state travels with the product so the card can render it. Without
	# it the client has no way to know, and every card came back un-bookmarked on
	# reload however many the farmer had saved.
	_annotate_saved(products)

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
		# get_list, not get_all: A2C Loan Product is bank-scoped, and the farmer
		# branch of loan_product_scope_query also limits the catalog to Active
		# products. get_all skipped both, so a product archived after the farmer
		# bookmarked it kept coming back here in full -- visible on the saved list
		# and nowhere else, and still applyable from the card.
		products = frappe.get_list(
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
		# Same shape as list_catalog so one card component renders both lists.
		for product in products:
			product["is_saved"] = True

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
@require_role([FARMER_ROLE, DEVELOPMENT_AGENT_ROLE])
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
		fields=["name", "tenure_months", "min_amount", "max_amount", "min_interest_rate"],
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
	# Derived from min_interest_rate because that is what list_catalog filters and
	# what the card displays. Taken from max_interest_rate, the ceiling sat well
	# above every headline rate, so the whole upper half of the slider was inert --
	# dragging it changed nothing until it fell below the cheapest product.
	rates = [float(p["min_interest_rate"]) for p in products if p.get("min_interest_rate") is not None]

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
