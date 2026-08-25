from typing import Literal

import frappe
from frappe import _
from pydantic import BaseModel, Field, model_validator

from oan_a2c.a2c_marketplace.doctype_schemas import (
	MAX_INTEREST_RATE,
	MAX_LOAN_AMOUNT,
	MAX_TENURE_MONTHS,
)
from oan_a2c.a2c_marketplace.permissions import get_user_bank, is_platform_admin
from oan_a2c.a2c_marketplace.roles import BANK_ROLES
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
# Every entry ends with `name asc` as a tiebreaker. Without it, rows sharing a sort
# value (many products carry the same rate or tenure) have no defined order between
# them, and MariaDB is free to return them differently on each query -- so paging
# through the catalogue can show one product twice and skip another entirely.
_SORT_COLUMNS = {
	"product_name": "product_name asc, name asc",
	"interest_low_high": "min_interest_rate asc, name asc",
	"interest_high_low": "max_interest_rate desc, name asc",
	"amount_low_high": "min_amount asc, name asc",
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

	status: str | None = Field(None, max_length=140)
	category: str | None = Field(None, max_length=140)
	tag: str | None = Field(None, max_length=140)
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

	@model_validator(mode="after")
	def validate_tenure_range(self):
		if self.min_tenure_months is not None and self.max_tenure_months is not None:
			if self.min_tenure_months > self.max_tenure_months:
				raise ValueError("min_tenure_months cannot be greater than max_tenure_months.")
		return self


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
	# ids only ever narrow the permission-filtered product query below
	return frappe.get_all(  # bank-scope-exempt: see docstring
		"A2C Term Relationship",
		filters={"term_type": "Category", "term_category": category},
		pluck="loan_product",
	)


def _products_with_tag(tag: str) -> list[str]:
	"""Loan product ids carrying `tag`.

	bank-scope-exempt: A2C Term Relationship is bank-scoped, see _products_in_category docstring.
	"""
	return frappe.get_all(  # bank-scope-exempt: see docstring
		"A2C Term Relationship",
		filters={"term_type": "Tag", "term_tag": tag},
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


def _enrich_products_with_bank_info(products: list[dict]) -> None:
	"""Batch resolve bank display names and logos for product rows."""
	bank_ids = list({p["bank"] for p in products if p.get("bank")})
	if not bank_ids:
		return

	bank_rows = frappe.get_all(
		"A2C Participating Bank",
		filters={"name": ["in", bank_ids]},
		fields=["name", "bank_name", "logo"],
	)
	bank_map = {row.name: row for row in bank_rows}

	for p in products:
		bank_info = bank_map.get(p.get("bank"))
		p["bank_name"] = bank_info.bank_name if bank_info else None
		p["bank_logo"] = bank_info.logo if bank_info else None


def _enrich_products_with_categories(products: list[dict]) -> None:
	"""Batch resolve category for product rows.

	bank-scope-exempt: A2C Term Relationship is bank-scoped, so get_list returns
	nothing for a bank-bound farmer. Reading it directly with get_all is safe here
	because product_names came from a get_list that already applied
	loan_product_scope_query, so this only decorates rows the caller may see.
	"""
	if not products:
		return
	product_names = [p["name"] for p in products if p.get("name")]
	if not product_names:
		return
	cat_rows = frappe.get_all(  # bank-scope-exempt: see docstring
		"A2C Term Relationship",
		filters={"loan_product": ["in", product_names], "term_type": "Category"},
		fields=["loan_product", "term_category"],
	)
	category_map = {row.loan_product: row.term_category for row in cat_rows if row.term_category}

	for p in products:
		p["category"] = category_map.get(p.get("name"))


def _enrich_products_with_saved_flag(products: list[dict]) -> None:
	"""Flag which of these rows the calling user has bookmarked.

	One batched query for the whole page, not one per row. Without it the catalog
	returns no bookmark state at all, so every star on a freshly loaded page draws
	empty however many products the user has saved -- and the "Bookmarked only"
	filter shows a page of loans none of which look bookmarked.

	bank-scope-exempt: A2C Saved Product is bank-scoped, so get_list returns
	nothing for a bank-bound user. Reading it directly is safe here because the
	query is pinned to the session user's own rows and only ever annotates
	products that the permission-filtered query above already returned -- it can
	neither expose another user's bookmarks nor pull an unreadable product in.
	"""
	product_ids = [p["name"] for p in products]
	if not product_ids:
		return

	# own rows only; used to annotate an already permission-filtered page
	saved = set(
		frappe.get_all(  # bank-scope-exempt: see docstring
			"A2C Saved Product",
			filters={"user": frappe.session.user, "loan_product": ["in", product_ids]},
			pluck="loan_product",
		)
	)

	for p in products:
		p["is_saved"] = p["name"] in saved


def _enrich_products_with_application_counts(products: list[dict]) -> None:
	"""Batch attach applications_count per product if caller is a bank user or admin."""
	if not products:
		return
	user = frappe.session.user
	user_roles = set(frappe.get_roles(user))
	is_bank_user = (
		bool(user_roles.intersection(BANK_ROLES)) or bool(get_user_bank(user)) or is_platform_admin(user)
	)
	if not is_bank_user:
		return

	product_names = [p["name"] for p in products if p.get("name")]
	if not product_names:
		return

	# get_list applies DocPerm and loan_application_scope_query (bank scope)
	app_counts = frappe.get_list(
		"A2C Loan Application",
		filters={"loan_product": ["in", product_names]},
		fields=["loan_product", {"COUNT": "*"}],
		group_by="loan_product",
	)
	counts_map = {row.loan_product: row.get("COUNT(*)") for row in app_counts}

	for p in products:
		p["applications_count"] = counts_map.get(p.get("name"), 0)


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

	user = frappe.session.user
	user_roles = set(frappe.get_roles(user))
	is_bank_user = (
		bool(user_roles.intersection(BANK_ROLES)) or bool(get_user_bank(user)) or is_platform_admin(user)
	)

	filters = {}
	if is_bank_user:
		if kwargs.get("status"):
			filters["status"] = kwargs["status"]
		else:
			filters["status"] = ["!=", "Archived"]
	else:
		filters["status"] = "Active"

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
	# Amount filtering is an OVERLAP test, not a containment test.
	#
	# The caller is saying "I want to borrow somewhere in this range"; a product
	# matches when the range it offers overlaps the range they asked for. The
	# comparison is therefore crossed -- their floor against the product's ceiling,
	# their ceiling against the product's floor:
	#
	#     product.max_amount >= requested_min  AND  product.min_amount <= requested_max
	#
	# Filtering the same-named columns instead (product.min_amount >= requested_min)
	# asks for products whose whole range sits *inside* the requested one, which
	# excludes exactly the products that can fund the request: a farmer looking for
	# 5,000-10,000 would see nothing from a product offering 1,000-200,000.
	if kwargs.get("min_amount") is not None:
		filters["max_amount"] = [">=", float(kwargs["min_amount"])]
	if kwargs.get("max_amount") is not None:
		filters["min_amount"] = ["<=", float(kwargs["max_amount"])]
	if kwargs.get("max_interest_rate") is not None:
		# Maps onto min_interest_rate, not max_interest_rate, deliberately: a
		# product qualifies if its best-case (lowest) rate could still meet the
		# caller's budget ceiling. The worst-case rate is a matter for the actual
		# credit offer later, not for whether the product surfaces in discovery.
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
		if not _narrow_by_names(filters, _products_in_category(kwargs["category"])):
			return _empty_catalog_page(kwargs)

	if kwargs.get("tag"):
		if not _narrow_by_names(filters, _products_with_tag(kwargs["tag"])):
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
			"status",
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

	_enrich_products_with_bank_info(products)
	_enrich_products_with_categories(products)
	_enrich_products_with_saved_flag(products)
	_enrich_products_with_application_counts(products)

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
	# get_list, not db.exists: db.exists is a raw, permission-agnostic lookup and
	# would let a product outside the caller's normal visibility (wrong bank, or
	# Draft/Archived for a Farmer) be bookmarked anyway -- and its 200-vs-404
	# response would double as an existence oracle for docnames the caller has no
	# business knowing about. get_list re-applies loan_product_scope_query, so a
	# product the caller couldn't otherwise see is treated as not found here too.
	if not frappe.get_list("A2C Loan Product", filters={"name": loan_product, "status": "Active"}, limit_page_length=1):
		frappe.throw(_("Loan Product not found."), frappe.DoesNotExistError)

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

	# Full saved order first, unpaginated on purpose. A product saved months ago
	# may since have gone Archived (or otherwise dropped out of the caller's
	# visibility), and loan_product_scope_query is what filters that out below --
	# paginating on A2C Saved Product before applying that filter let an invisible
	# row consume a page slot, desyncing `total`/`has_next` from what the client
	# actually receives (BUG-02). Filtering first keeps both sides honest.
	saved_order = frappe.get_all(
		"A2C Saved Product",
		filters={"user": user},
		pluck="loan_product",
		order_by="creation desc, name asc",
	)

	visible_names = set()
	if saved_order:
		# get_list, not get_all: re-applies loan_product_scope_query, so a
		# saved product that is Archived, or otherwise no longer visible to this
		# caller, is excluded here rather than merely from the page's fields.
		visible_names = set(
			frappe.get_list("A2C Loan Product", filters={"name": ["in", saved_order]}, pluck="name")
		)
	visible_order = [name for name in saved_order if name in visible_names]

	total = len(visible_order)
	page_names = visible_order[start : start + limit]

	products = []
	if page_names:
		products = frappe.get_list(
			"A2C Loan Product",
			filters={"name": ["in", page_names], "status": "Active"},
			fields=[
				"name",
				"product_name",
				"slug",
				"status",
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
		order_map = {name: i for i, name in enumerate(page_names)}
		products.sort(key=lambda p: order_map.get(p.name, 999))
		_enrich_products_with_bank_info(products)
		_enrich_products_with_categories(products)
		_enrich_products_with_application_counts(products)
		# True by construction -- this list was built from the bookmark table. Stated
		# rather than queried so the field is present on every product payload a
		# client can receive, not only on the catalog's.
		for p in products:
			p["is_saved"] = True

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

	category_rows = frappe.get_all("A2C Term Category", fields=["name", "term"])
	tag_rows = frappe.get_all("A2C Term Tag", fields=["name", "term"])

	term_ids = list({row.term or row.name for row in category_rows + tag_rows})
	term_name_map = {}
	if term_ids:
		terms = frappe.get_all(
			"A2C Term",
			filters={"name": ["in", term_ids]},
			fields=["name", "term_name"],
		)
		term_name_map = {t.name: t.term_name for t in terms}

	categories = [
		{
			"id": c.name,
			"name": term_name_map.get(c.term or c.name) or c.name,
		}
		for c in category_rows
	]

	tags = [
		{
			"id": t.name,
			"name": term_name_map.get(t.term or t.name) or t.name,
		}
		for t in tag_rows
	]

	# Fetch active banks and unique regions
	active_banks = frappe.get_all(
		"A2C Participating Bank",
		filters={"status": "Active"},
		fields=["name", "bank_name", "logo", "registered_region"],
	)
	regions = sorted(list({b.registered_region for b in active_banks if b.registered_region}))

	# The tenures actually on offer, not a span the sidebar would have to invent
	# chips inside. `tenure_months` is one value per product, so the distinct set is
	# exactly the set of choices that can return something -- which is the rule this
	# module's ListCatalogSchema docstring states: a control that silently does
	# nothing is worse than no control.
	#
	# get_list, not get_all: A2C Loan Product is bank-scoped, and get_all bypasses
	# both loan_product_scope_query and DocPerm. Going through get_list means a
	# farmer's chips come from Active products across every bank, and a bank user's
	# from their own -- the same rows the list underneath them will contain.
	#
	# `status: Active` mirrors list_catalog's own base filter, so the sidebar cannot
	# offer a tenure that only Draft or Archived products carry.
	tenures = sorted(
		{
			t
			for t in frappe.get_list(
				"A2C Loan Product",
				filters={"status": "Active"},
				pluck="tenure_months",
				distinct=True,
			)
			if t
		}
	)

	return success_response(
		data={
			"categories": categories,
			"tags": tags,
			"regions": regions,
			"banks": active_banks,
			# The tenures on offer. This was hardcoded to [] and the sidebar renders its
			# tenure section only when the list is non-empty, so the control never
			# appeared; dropping the key outright then crashed the sidebar outright.
			"tenures": tenures,
			# The schema's bounds, not the catalog's -- MAX_TENURE_MONTHS is 1200, so
			# this is a validation range for min_tenure_months / max_tenure_months, not
			# something to build chips out of. `tenures` above is the data.
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
