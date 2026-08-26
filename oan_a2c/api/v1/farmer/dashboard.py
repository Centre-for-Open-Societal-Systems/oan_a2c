import frappe

from oan_a2c.a2c_marketplace.roles import FARMER_ROLE
from oan_a2c.a2c_marketplace.stages import build_status_payloads
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

	farmer_profile = {}
	profile_name = frappe.db.get_value("A2C Farmer Profile", {"user": user}, "name")
	if profile_name:
		row = frappe.db.get_value("A2C Farmer Profile", profile_name, list(_PROFILE_FIELDS), as_dict=True)
		if row:
			farmer_profile = {k: row.get(k) for k in _PROFILE_FIELDS}
			farmer_profile["farmer_id"] = farmer_profile.get("farmer_id") or profile_name

	# No owner filter: loan_application_scope_query matches on farmer_profile, so
	# this returns the farmer's own applications including any a Development Agent
	# filed against their profile. Filtering on `owner` here would hide exactly
	# those, and disagree with the My Applications list.
	recent = frappe.get_list(
		"A2C Loan Application",
		fields=[
			"name",
			"bank",
			"loan_product_name",
			"requested_amount",
			"status",
			"stage_id",
			"stage_label",
			"creation",
		],
		limit_page_length=5,
		order_by="creation desc",
	)
	build_status_payloads(recent)
	recent_applications = [
		{
			"application_id": app.name,
			"bank": app.bank,
			"loan_product_name": app.loan_product_name,
			"requested_amount": app.requested_amount,
			"status": app.status,
			"stage_id": app.stage_id,
			"sequence": app.sequence,
			"is_terminal": app.is_terminal,
			"is_successful": app.is_successful,
			"creation": to_tz_aware_iso(app.creation),
		}
		for app in recent
	]

	return success_response(
		data={
			"farmer_profile": farmer_profile,
			"recent_applications": recent_applications,
		},
		message="Dashboard summary retrieved successfully",
	)
