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

from oan_a2c.a2c_marketplace.doctype_schemas import (
	MAX_INTEREST_RATE,
	MAX_LOAN_AMOUNT,
	MAX_TENURE_MONTHS,
)
from pydantic import BaseModel, Field

from oan_a2c.a2c_marketplace.doctype_schemas import (
	MAX_INTEREST_RATE,
	MAX_LOAN_AMOUNT,
	MAX_TENURE_MONTHS,
)
from oan_a2c.api.utils import (
	handle_api_errors,
	success_response,
	to_tz_aware_iso,
	validate_request,
)
from oan_a2c.api.v1.loan_applications import BrowseProductsSchema


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
	region: str | None = Field(None, max_length=140)
	is_saved: bool | None = Field(None)
	min_tenure_months: int | None = Field(None, ge=0, le=MAX_TENURE_MONTHS)
	max_tenure_months: int | None = Field(None, ge=0, le=MAX_TENURE_MONTHS)
	max_interest_rate: float | None = Field(None, ge=0, le=MAX_INTEREST_RATE)
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
	# ids only ever narrow the permission-filtered product query below
	return frappe.get_all(  # bank-scope-exempt: see docstring
		"A2C Term Relationship",
		filters={"term_type": "Category", "term_category": category},
		pluck="loan_product",
	)


def _empty_catalog_page(kwargs):
	"""A well-formed empty page.

	A filter that provably matches nothing short-circuits before the product query
	rather than sending an empty `IN ()` to the database, but the response shape
	must stay identical to a populated page so clients need no special case.
	"""
	return success_response(
		data={"products": []},
		message="Catalog retrieved successfully",
		pagination={
			"page": (kwargs["start"] // kwargs["limit"]) + 1,
			"limit": kwargs["limit"],
			"total": 0,
			"total_pages": 0,
			"has_next": False,
		},
	)


def _narrow_by_names(filters: dict, candidates: list[str]) -> bool:
	"""Intersect the `name` filter with `candidates`; False if nothing survives.

	Several filters (category, is_saved, an explicit loan_product) all constrain
	`name`, and a dict holds one value per key -- so each must intersect with what
	is already there instead of overwriting it. The scalar case matters: an
	explicit `loan_product` sets `name` to a bare string, and treating that as
	"nothing set yet" silently drops the caller's filter.
	"""
	existing = filters.get("name")
	if existing is None:
		surviving = list(candidates)
	elif isinstance(existing, str):
		surviving = [existing] if existing in candidates else []
	else:
		# ["in", [...]] from an earlier narrowing.
		surviving = [n for n in existing[1] if n in set(candidates)]

	if not surviving:
		return False
	filters["name"] = ["in", surviving]
	return True


@frappe.whitelist(allow_guest=False)
@validate_request(FarmerCatalogSchema)
@handle_api_errors

def list_catalog(**kwargs):
	"""Active loan products across every bank, for any signed-in user.

	Not farmer-only: browsing the catalog is open to anyone signed in. What each
	caller sees is still decided by loan_product_scope_query -- a farmer sees
	Active products across every bank, a bank user sees only their own bank.
	"""
	frappe.has_permission("A2C Loan Product", "read", throw=True)

	filters = {"status": "Active"}
	if kwargs.get("bank"):
		filters["bank"] = kwargs["bank"]

	if kwargs.get("region"):
		banks_in_region = frappe.get_all(
			"A2C Participating Bank",
			filters={"registered_region": kwargs["region"], "status": "Active"},
			pluck="name",
		)
		if not banks_in_region:
			return _empty_catalog_page(kwargs)
		if "bank" in filters:
			if filters["bank"] not in banks_in_region:
				return _empty_catalog_page(kwargs)
		else:
			filters["bank"] = ["in", banks_in_region]

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
		if not _narrow_by_names(filters, _products_in_category(kwargs["category"])):
			return _empty_catalog_page(kwargs)

	if kwargs.get("is_saved"):
		saved = frappe.get_all(
			"A2C Saved Product",
			filters={"user": frappe.session.user},
			pluck="loan_product",
		)
		if not _narrow_by_names(filters, saved):
			return _empty_catalog_page(kwargs)

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
			"image as image_url",
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
def save_product(**kwargs):
	"""Bookmarks a loan product for the calling user.

	Keyed on the User, not on A2C Farmer Profile: bookmarking is a browsing
	convenience open to anyone signed in, and a profile only exists once consent
	has bound one -- requiring it would mean nobody could save a product until
	after they had already applied for one.
	"""
	user = frappe.session.user
	loan_product = kwargs["loan_product"]
	if not frappe.db.exists("A2C Loan Product", loan_product):
		frappe.throw(_("Loan Product not found."), frappe.NotFoundError)

	if frappe.db.exists("A2C Saved Product", {"user": user, "loan_product": loan_product}):
		# Saving twice is the same outcome as saving once, so a double-tap is a
		# success rather than a duplicate error.
		return success_response(message="Product saved successfully")

	doc = frappe.get_doc({"doctype": "A2C Saved Product", "user": user, "loan_product": loan_product})
	frappe.db.savepoint("save_product")
	try:
		doc.insert(ignore_permissions=False)
	except frappe.DuplicateEntryError:
		# Lost the race against a concurrent save of the same product; the unique
		# index on (user, loan_product) held, and the caller's intent is satisfied.
		frappe.db.rollback(save_point="save_product")

	return success_response(message="Product saved successfully")


@frappe.whitelist(allow_guest=False, methods=["POST"])
@validate_request(SaveProductSchema)
@handle_api_errors
def unsave_product(**kwargs):
	"""Removes a bookmarked loan product for the calling user."""
	user = frappe.session.user
	loan_product = kwargs["loan_product"]
	saved = frappe.db.get_value("A2C Saved Product", {"user": user, "loan_product": loan_product}, "name")
	if saved:
		frappe.delete_doc("A2C Saved Product", saved, ignore_permissions=False)

	return success_response(message="Product removed from saved list")


@frappe.whitelist(allow_guest=False)
@validate_request(PaginationSchema)
@handle_api_errors
def get_saved_products(**kwargs):
	"""Returns the calling user's bookmarked loan products."""
	user = frappe.session.user
	limit = kwargs["limit"]
	start = kwargs["start"]

	# We fetch the links from A2C Saved Product and join with A2C Loan Product details.
	# `name asc` tiebreaks: bookmarks saved in the same second would otherwise have no
	# defined order, and this query is paginated.
	saved_docs = frappe.get_all(
		"A2C Saved Product",
		filters={"user": user},
		pluck="loan_product",
		order_by="creation desc, name asc",
		limit_page_length=limit,
		limit_start=start,
	)

	total = frappe.db.count("A2C Saved Product", filters={"user": user})

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
				"image as image_url",
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

def get_catalog_facets(**kwargs):
	"""Static filter options for the discovery sidebar, using global definitions."""
	frappe.has_permission("A2C Loan Product", "read", throw=True)

	categories = frappe.get_all("A2C Term Category", pluck="name")
	tags = frappe.get_all("A2C Term Tag", pluck="name")

	# Fetch unique regions from active banks
	bank_regions = frappe.get_all(
		"A2C Participating Bank", filters={"status": "Active"}, pluck="registered_region", distinct=True
	)
	regions = sorted(list(set(r for r in bank_regions if r)))

	return success_response(
		data={
			"categories": [{"name": c, "count": None} for c in categories],
			"tags": tags,
			"regions": regions,
			"tenures": [],  # Kept for backward compatibility if frontend maps it
			"tenure_range": {"min": 1, "max": MAX_TENURE_MONTHS},
			"amount_range": {
				"min": 0.0,
				"max": float(MAX_LOAN_AMOUNT),
			},
			"max_interest_rate": float(MAX_INTEREST_RATE),
		},
		message="Catalog facets retrieved successfully",
	)


class GetBankDetailsSchema(BaseModel):
	bank: str = Field(..., min_length=1, max_length=140)


# The storefront view of a bank: the fields a borrower needs to decide whether to
# apply. Everything else on A2C Participating Bank -- contacts, onboarding state,
# internal notes -- is deliberately absent, and this list is the allowlist rather
# than a denylist so a field added to the doctype later cannot leak by default.
_BANK_PUBLIC_FIELDS = (
	"name",
	"bank_name",
	"bank_code",
	"brand_name",
	"entity_type",
	"website",
	"logo",
	"registered_region",
	"registered_country",
	"status",
)


@frappe.whitelist(allow_guest=False)
@validate_request(GetBankDetailsSchema)
@handle_api_errors
def get_bank_details(**kwargs):
	"""Storefront detail for one Active bank, for any signed-in user.

	Reads via frappe.db.get_value rather than get_doc, which bypasses DocPerm on
	A2C Participating Bank -- farmers hold none. That exemption is safe only
	because of the two constraints below, so neither may be relaxed without
	revisiting it: the response is built from a fixed public-field allowlist, and
	an inactive bank is indistinguishable from a missing one, so this cannot be
	used to enumerate banks that are not yet live on the marketplace.
	"""
	bank_name = kwargs.get("bank")

	bank_info = frappe.db.get_value(
		"A2C Participating Bank",
		bank_name,
		list(_BANK_PUBLIC_FIELDS),
		as_dict=True,
	)

	if not bank_info or bank_info.status != "Active":
		frappe.throw(_("Bank not found"), frappe.DoesNotExistError)

	return success_response(
		data={
			"bank": bank_info.name,
			"bank_name": bank_info.bank_name,
			"bank_code": bank_info.bank_code,
			"brand_name": bank_info.brand_name,
			"entity_type": bank_info.entity_type,
			"website": bank_info.website,
			"logo_url": bank_info.logo,
			"registered_region": bank_info.registered_region,
			"registered_country": bank_info.registered_country,
		},
		message="Bank details retrieved successfully",
	)
