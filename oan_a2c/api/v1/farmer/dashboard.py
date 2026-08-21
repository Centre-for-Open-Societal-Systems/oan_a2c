import frappe

from oan_a2c.a2c_marketplace.roles import FARMER_ROLE
from oan_a2c.api.utils import (
	handle_api_errors,
	require_role,
	success_response,
	to_tz_aware_iso,
)

# Fields copied straight from A2C Farmer Profile. Kept as a list so the response
# cannot quietly grow a field the doctype does not have: everything here is
# stored data, and anything the dashboard wants beyond it needs a field first.
_PROFILE_FIELDS = (
	"first_name",
	"last_name",
	"farmer_id",
	"region",
	"woreda",
	"kebele",
	"farmland_size_hectares",
	"land_ownership_status",
	"source_of_income",
)


@frappe.whitelist(allow_guest=False)
@handle_api_errors
@require_role([FARMER_ROLE])
def get_dashboard_summary(**kwargs):
	"""Aggregated data for the farmer dashboard.

	Every value is read from the database. Where there is nothing to read the
	response says so with an empty collection rather than a placeholder -- a
	dashboard that invents plausible numbers is worse than one that shows none,
	because nobody can tell which is which.
	"""
	user = frappe.session.user

	farmer_profile = None
	profile_name = frappe.db.get_value("A2C Farmer Profile", {"user": user}, "name")
	if profile_name:
		row = frappe.db.get_value(
			"A2C Farmer Profile", profile_name, list(_PROFILE_FIELDS), as_dict=True
		)
		if row:
			farmer_profile = {k: row.get(k) for k in _PROFILE_FIELDS}
			farmer_profile["farmer_id"] = farmer_profile.get("farmer_id") or profile_name

			# Fallback to User table if the farmer profile is missing the names
			if not farmer_profile.get("first_name") and not farmer_profile.get("last_name"):
				user_row = frappe.db.get_value("User", user, ["first_name", "last_name"], as_dict=True)
				if user_row:
					farmer_profile["first_name"] = user_row.get("first_name")
					farmer_profile["last_name"] = user_row.get("last_name")
	
	if not farmer_profile:
		# If no profile exists, construct a fallback using User table so the UI isn't empty
		user_row = frappe.db.get_value("User", user, ["first_name", "last_name"], as_dict=True)
		if user_row:
			farmer_profile = {
				"first_name": user_row.get("first_name"),
				"last_name": user_row.get("last_name"),
				"farmer_id": None
			}

	# get_list applies loan_product_scope_query: Active only, across all banks.
	offers = frappe.get_list(
		"A2C Loan Product",
		filters={"status": "Active"},
		fields=[
			"name",
			"bank",
			"product_name",
			"max_amount",
			"min_interest_rate",
			"tenure_months",
		],
		limit_page_length=3,
		order_by="creation desc",
	)
	top_loan_offers = [
		{
			"id": p.name,
			"bank": p.bank,
			"loan_product_name": p.product_name,
			"max_loan_amount": p.max_amount,
			"interest_rate": p.min_interest_rate,
			"max_tenure_months": p.tenure_months,
		}
		for p in offers
	]

	# Loan *types* are the product taxonomy, not product names. Read from the
	# categories actually attached to products the farmer can see, so the list is
	# empty when the catalog has no taxonomy rather than falling back to invented
	# examples.
	visible = frappe.get_list(
		"A2C Loan Product", filters={"status": "Active"}, pluck="name", limit_page_length=0
	)
	available_loan_types = []
	if visible:
		# bank-scope-exempt: A2C Term Relationship is bank-scoped and a farmer is
		# bank-bound. Restricted to `visible`, which came from the permission-filtered
		# query above, so nothing the farmer may not see can enter the result.
		rows = frappe.get_all(
			"A2C Term Relationship",
			filters={"loan_product": ["in", visible], "term_type": "Category"},
			pluck="term_category",
		)
		available_loan_types = sorted({r for r in rows if r})

	# No owner filter: loan_application_scope_query matches on farmer_profile, so
	# this returns the farmer's own applications including any a Development Agent
	# filed against their profile. Filtering on `owner` here would hide exactly
	# those, and disagree with the My Applications list.
	recent = frappe.get_list(
		"A2C Loan Application",
		fields=["name", "bank", "loan_product_name", "requested_amount", "status", "creation"],
		limit_page_length=5,
		order_by="creation desc",
	)
	recent_applications = [
		{
			"application_id": app.name,
			"bank": app.bank,
			"loan_product_name": app.loan_product_name,
			"requested_amount": app.requested_amount,
			"status": app.status,
			"creation": to_tz_aware_iso(app.creation),
		}
		for app in recent
	]

	return success_response(
		data={
			"farmer_profile": farmer_profile,
			"top_loan_offers": top_loan_offers,
			"available_loan_types": available_loan_types,
			"recent_applications": recent_applications,
		},
		message="Dashboard summary retrieved successfully",
	)
