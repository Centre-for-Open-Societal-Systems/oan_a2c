import frappe
from frappe import _
from frappe.utils import cint, flt
from pydantic import BaseModel, Field

from oan_a2c.a2c_marketplace.roles import FARMER_ROLE
from oan_a2c.api.utils import handle_api_errors, validate_request, success_response, to_tz_aware_iso, require_role, apply_status_transition
from oan_a2c.api.v1.loan_applications import GetAllLoansSchema, LoanApplicationIDSchema, _get_app
from oan_a2c.api.v1.farmer.consent import get_or_create_self_service_lead

class CreateFarmerApplicationSchema(BaseModel):
	loan_product: str = Field(..., min_length=1, max_length=140)
	requested_amount: float = Field(..., ge=1)
	loan_reason: str | None = Field(None, max_length=2000)

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
			# Terms the application was made under. Snapshotted at creation, so a
			# later edit to the product cannot rewrite what the farmer applied for.
			"interest_rate",
			"tenure_months",
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
		# Left as None rather than defaulted to 0: an application created before
		# terms were snapshotted has no rate, and "0%" is not a truthful stand-in
		# for one. The client renders a placeholder for null.
		r["interest_rate"] = flt(r["interest_rate"]) if r.get("interest_rate") else None
		r["tenure_months"] = cint(r["tenure_months"]) if r.get("tenure_months") else None

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
		"loan_product": doc.loan_product,
		"loan_product_name": doc.loan_product_name,
		"requested_amount": float(doc.requested_amount) if doc.requested_amount else 0.0,
		"loan_amount": float(doc.loan_amount) if doc.loan_amount else 0.0,
		"loan_reason": doc.loan_reason,
		"status": doc.status,
		"creation": to_tz_aware_iso(doc.creation),
		"interest_rate": flt(doc.interest_rate) if doc.interest_rate else None,
		"tenure_months": cint(doc.tenure_months) if doc.tenure_months else None,
	}
	return success_response(data=data, message="Application retrieved successfully")


@frappe.whitelist(allow_guest=False, methods=["POST"])
@validate_request(UpdateFarmerApplicationSchema)
@handle_api_errors
@require_role([FARMER_ROLE])
def update_application(**kwargs):
	"""Allows a farmer to update their Draft application."""
	application_id = kwargs.get("application_id")
	frappe.has_permission("A2C Loan Application", "write", doc=application_id, throw=True)
	
	doc = _get_app(application_id)
	if doc.status != "Draft":
		frappe.throw(_("Only Draft applications can be updated."), frappe.ValidationError)
		
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
	"""Creates a Draft application for the farmer."""
	user = frappe.session.user
	profile_name = frappe.db.get_value("A2C Farmer Profile", {"user": user}, "name")
	if not profile_name:
		frappe.throw(_("You must have a Farmer Profile to create an application."), frappe.ValidationError)
	
	product = frappe.get_doc("A2C Loan Product", kwargs["loan_product"])
	if product.status != "Active":
		frappe.throw(_("This loan product is not active."), frappe.ValidationError)

	profile = frappe.get_doc("A2C Farmer Profile", profile_name)

	# The A2C Lead that consent and the rest of the pipeline are anchored on already
	# exists: the apply page calls farmer.consent.start_consent to mint it *before*
	# the consent step, because consent needs a lead_id and is itself what creates
	# the farmer profile this endpoint requires. Reuse it rather than inserting a
	# second lead — a duplicate here would be a lead with no consent attached.
	lead = get_or_create_self_service_lead(user)

	# Backfill the profile link if this lead predates it (the consent webhook sets
	# farmer_profile, but only on the lead the consent ran against).
	if not lead.farmer_profile:
		lead.db_set("farmer_profile", profile.name)

	# Create Application
	app = frappe.get_doc({
		"doctype": "A2C Loan Application",
		"lead_id": lead.name,
		"farmer_profile": profile.name,
		"bank": product.bank,
		"loan_product": product.name,
		"requested_amount": kwargs["requested_amount"],
		"loan_amount": kwargs["requested_amount"],
		# The offer the farmer accepted, copied onto the application. The product
		# stays editable by its bank, so reading terms back through the live
		# product would silently restate an application in someone else's numbers.
		# min_interest_rate is the headline rate the catalog card advertises.
		"interest_rate": product.min_interest_rate,
		"tenure_months": product.tenure_months,
		"loan_reason": kwargs.get("loan_reason"),
		"status": "Draft",
		"current_step": 1,
		"first_name": profile.first_name,
		"last_name": profile.last_name,
		"phone_number": lead.phone_number,
		"region": profile.region,
		"woreda": profile.woreda,
		"kebele": profile.kebele,
		"farmer_id": profile.farmer_id,
		"consent_id": profile.consent_id,
	})
	app.insert(ignore_permissions=False)
	
	return success_response(
		data={"application_id": app.name},
		message="Application created successfully"
	)

@frappe.whitelist(allow_guest=False, methods=["POST"])
@validate_request(LoanApplicationIDSchema)
@handle_api_errors
@require_role([FARMER_ROLE])
def submit_application(**kwargs):
	"""Submits a Draft application to the bank (transitions to Processing)."""
	application_id = kwargs.get("application_id")
	frappe.has_permission("A2C Loan Application", "write", doc=application_id, throw=True)
	
	doc = _get_app(application_id)
	if doc.status != "Draft":
		frappe.throw(_("Only Draft applications can be submitted."), frappe.ValidationError)
		
	# The workflow patch (update_loan_workflow_for_farmer) adds A2C Farmer to the
	# Draft -> Processing transition.
	apply_status_transition(doc, "Processing")
	
	return success_response(message="Application submitted successfully")

