import frappe
from frappe import _
from frappe.utils import cint, flt
from pydantic import BaseModel, Field

from oan_a2c.a2c_marketplace.roles import FARMER_ROLE
from oan_a2c.api.utils import (
	apply_status_transition,
	handle_api_errors,
	require_role,
	success_response,
	to_tz_aware_iso,
	validate_request,
)
from oan_a2c.api.v1.loan_applications import GetAllLoansSchema, LoanApplicationIDSchema, _get_app


class CreateFarmerApplicationSchema(BaseModel):
	loan_product: str = Field(..., min_length=1, max_length=140)
	requested_amount: float = Field(..., ge=1)
	loan_reason: str | None = Field(None, max_length=2000)
	consent_request: str | None = Field(None, max_length=140)


class UpdateFarmerApplicationSchema(BaseModel):
	application_id: str = Field(..., min_length=1, max_length=140)
	requested_amount: float | None = Field(None, ge=1)
	loan_reason: str | None = Field(None, max_length=2000)


@frappe.whitelist(allow_guest=False)
@validate_request(GetAllLoansSchema)
@handle_api_errors
@require_role([FARMER_ROLE])
def list_applications(**kwargs):
	"""Returns the farmer's own applications.

	The bank_scope_query hook for A2C Loan Application restricts this to applications
	matching the farmer's profile.
	"""
	frappe.has_permission("A2C Loan Application", "read", throw=True)

	page = kwargs.get("page") or 1
	page_size = kwargs.get("page_size") or 20
	offset = (page - 1) * page_size
	order_by = "creation desc"

	filters = {}
	if kwargs.get("status"):
		filters["status"] = kwargs["status"]

	count_res = frappe.get_list(
		"A2C Loan Application",
		filters=filters,
		fields=[{"COUNT": "*"}],
		ignore_permissions=False,
	)
	total_records = count_res[0].get("COUNT(*)") if count_res else 0

	records = frappe.get_list(
		"A2C Loan Application",
		filters=filters,
		fields=[
			"name as application_id",
			"status",
			"loan_amount",
			"requested_amount",
			"loan_product",
			"loan_product_name",
			"bank",
			"creation",
		],
		order_by=order_by,
		limit_start=offset,
		page_length=page_size,
		ignore_permissions=False,
	)

	for r in records:
		r["loan_amount"] = float(r["loan_amount"]) if r.get("loan_amount") else 0.0
		r["requested_amount"] = float(r["requested_amount"]) if r.get("requested_amount") else 0.0
		r["creation"] = to_tz_aware_iso(r["creation"])

	pagination = {
		"page": page,
		"limit": page_size,
		"total": total_records,
		"total_pages": -(-total_records // page_size),
		"has_next": offset + page_size < total_records,
	}

	return success_response(
		data=records, message="Applications retrieved successfully", pagination=pagination
	)


@frappe.whitelist(allow_guest=False)
@validate_request(LoanApplicationIDSchema)
@handle_api_errors
@require_role([FARMER_ROLE])
def get_application(**kwargs):
	"""Returns details of a specific application owned by the farmer."""
	application_id = kwargs.get("application_id")
	frappe.has_permission("A2C Loan Application", "read", doc=application_id, throw=True)

	doc = _get_app(application_id)

	data = {
		"application_id": doc.name,
		"bank": doc.bank,
		"first_name": doc.first_name,
		"last_name": doc.last_name,
		"region": doc.region,
		"woreda": doc.woreda,
		"kebele": doc.kebele,
		"language": doc.language,
		"phone_number": doc.phone_number,
		"id_type": doc.id_type,
		"id_number": doc.id_number,
		"farmer_id": doc.farmer_id,
		"consent_id": doc.consent_id,
		"loan_type": doc.loan_type,
		"loan_product": doc.loan_product,
		"loan_product_name": doc.loan_product_name,
		"requested_amount": flt(doc.requested_amount),
		"loan_amount": flt(doc.loan_amount),
		"loan_reason": doc.loan_reason,
		"status": doc.status,
		"current_step": cint(doc.current_step),
		"loan_officer": doc.loan_officer,
		"creation": to_tz_aware_iso(doc.creation),
		"date_of_birth": str(doc.date_of_birth) if doc.date_of_birth else None,
		"gender": doc.gender,
		"marital_status": doc.marital_status,
		"size_of_family": cint(doc.size_of_family),
		"number_of_children": cint(doc.number_of_children),
		"no_of_females_family": cint(doc.no_of_females_family),
		"no_of_males_family": cint(doc.no_of_males_family),
		"source_of_income": doc.source_of_income,
		"education_level": doc.education_level,
		"family_member_owns_land_independently": bool(doc.family_member_owns_land_independently),
		"total_farmland_size_as_landowner": flt(doc.total_farmland_size_as_landowner),
		"total_farmland_size_as_crop_sharing": flt(doc.total_farmland_size_as_crop_sharing),
		"total_farmland_size_as_rented": flt(doc.total_farmland_size_as_rented),
		"farmland_size_hectares": doc.farmland_size_hectares,
		"land_ownership_status": doc.land_ownership_status,
		"soil_fertility_minerals": doc.soil_fertility_minerals,
		"moisture_levels": doc.moisture_levels,
		"certification_id": doc.certification_id,
		"certification_photo_url": doc.certification_photo_url,
	}
	return success_response(data=data, message="Application retrieved successfully")


@frappe.whitelist(allow_guest=False, methods=["POST"])
@validate_request(UpdateFarmerApplicationSchema)
@handle_api_errors
@require_role([FARMER_ROLE])
def update_application(**kwargs):
	"""Allows a farmer to update their Active application."""
	application_id = kwargs.get("application_id")
	frappe.has_permission("A2C Loan Application", "write", doc=application_id, throw=True)

	doc = _get_app(application_id)
	if doc.status != "Active":
		frappe.throw(_("Only Active applications can be updated."), frappe.ValidationError)

	changed = False
	if kwargs.get("requested_amount") is not None:
		doc.requested_amount = kwargs["requested_amount"]
		doc.loan_amount = kwargs["requested_amount"]
		changed = True
	if kwargs.get("loan_reason") is not None:
		doc.loan_reason = kwargs["loan_reason"]
		changed = True

	if changed:
		doc.save()

	return success_response(message="Application updated successfully")


@frappe.whitelist(allow_guest=False, methods=["POST"])
@validate_request(CreateFarmerApplicationSchema)
@handle_api_errors
@require_role([FARMER_ROLE])
def create_application(**kwargs):
	"""Creates an Active application for the farmer."""
	user = frappe.session.user
	profile_name = frappe.db.get_value("A2C Farmer Profile", {"user": user}, "name")
	if not profile_name:
		frappe.throw(_("You must have a Farmer Profile to create an application."), frappe.ValidationError)

	product = frappe.get_doc("A2C Loan Product", kwargs["loan_product"])
	if product.status != "Active":
		frappe.throw(_("This loan product is not active."), frappe.ValidationError)

	profile = frappe.get_doc("A2C Farmer Profile", profile_name)

	# A caller may nominate the consent they just completed; otherwise fall back to
	# whichever consent is already bound to the profile. Either way the request is
	# verified to be this farmer's and approved -- a consent id is a claim about
	# identity verification, so it can never be taken on the client's word.
	consent_id = kwargs.get("consent_request")
	if consent_id:
		consent = frappe.db.get_value("A2C Consent Request", consent_id, ["status", "owner"], as_dict=True)
		if not consent:
			frappe.throw(_("Consent Request not found."), frappe.DoesNotExistError)
		if consent.owner != user:
			frappe.throw(_("Consent Request does not belong to you."), frappe.PermissionError)
		if consent.status != "Approved":
			frappe.throw(_("Consent Request is not approved."), frappe.ValidationError)
	else:
		consent_id = profile.consent_id

	# Self-service applications carry no lead. A lead is the Development Agent's
	# CRM record for a farmer they are working, and everything hanging off it --
	# audit events, visit schedules, credit information, the Verified gate -- is
	# part of that agent-operated pipeline. A farmer applying directly is not being
	# worked by an agent, so there is nothing for those records to describe.
	# `application_source` is what keeps the two pipelines apart; see
	# loan_application_scope_query.
	app = frappe.get_doc(
		{
			"doctype": "A2C Loan Application",
			"application_source": "Self Service",
			"farmer_profile": profile.name,
			"bank": product.bank,
			"loan_product": product.name,
			"requested_amount": kwargs["requested_amount"],
			"loan_amount": kwargs["requested_amount"],
			"loan_reason": kwargs.get("loan_reason"),
			"consent_id": consent_id,
			"status": "Active",
			"current_step": 1,
			"first_name": profile.first_name,
			"last_name": profile.last_name,
			"phone_number": profile.phone_number or frappe.db.get_value("User", user, "mobile_no"),
			"region": profile.region,
			"woreda": profile.woreda,
			"kebele": profile.kebele,
			"farmer_id": profile.farmer_id,
		}
	)
	app.insert(ignore_permissions=False)

	return success_response(data={"application_id": app.name}, message="Application created successfully")


@frappe.whitelist(allow_guest=False, methods=["POST"])
@validate_request(LoanApplicationIDSchema)
@handle_api_errors
@require_role([FARMER_ROLE])
def submit_application(**kwargs):
	"""Submits an Active application to the bank (transitions to In Transition)."""
	application_id = kwargs.get("application_id")
	frappe.has_permission("A2C Loan Application", "write", doc=application_id, throw=True)

	doc = _get_app(application_id)
	if doc.status != "Active":
		frappe.throw(_("Only Active applications can be submitted."), frappe.ValidationError)

	from oan_a2c.a2c_marketplace.stages import get_initial_pipeline_stage

	initial_stage = get_initial_pipeline_stage(doc.bank)
	if initial_stage:
		doc.stage_id = initial_stage["stage_id"]
		doc.stage_label = initial_stage["label"]

	apply_status_transition(doc, "In Transition")

	return success_response(message="Application submitted successfully")
