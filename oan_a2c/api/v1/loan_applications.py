from typing import Literal

import frappe
from frappe import _
from frappe.utils import cint, flt, sanitize_html
from pydantic import BaseModel, Field, model_validator

from oan_a2c.api.utils import (
	SafeDate,
	SafeEmail,
	apply_status_transition,
	handle_api_errors,
	parse_multi_value,
	success_response,
	to_tz_aware_iso,
	validate_request,
)


class GetBasicProfileSchema(BaseModel):
	lead_id: str = Field(..., min_length=1, max_length=140)
	include_consent_data: int | None = None


class UpdateBasicProfileSchema(BaseModel):
	lead_id: str = Field(..., min_length=1, max_length=140)
	email: SafeEmail = None
	region: str | None = Field(None, max_length=140)
	woreda: str | None = Field(None, max_length=140)
	kebele: str | None = Field(None, max_length=140)


class LoanApplicationIDSchema(BaseModel):
	application_id: str = Field(..., min_length=1, max_length=140)


class LeadIDSchema(BaseModel):
	lead_id: str = Field(..., min_length=1, max_length=140)


class GetAllLoansSchema(BaseModel):
	status: str | None = Field(None, max_length=140)
	loan_amount: float | None = Field(None, ge=0, le=999999999999.0)
	min_loan_amount: float | None = Field(None, ge=0, le=999999999999.0)
	max_loan_amount: float | None = Field(None, ge=0, le=999999999999.0)
	loan_type: str | None = Field(None, max_length=140)
	location: str | None = Field(None, max_length=140)
	phone_number: str | None = Field(None, max_length=50)
	loan_officer: str | None = Field(None, max_length=140)
	from_date: SafeDate = None
	to_date: SafeDate = None
	page: int | None = Field(None, ge=1)
	page_size: int | None = Field(None, ge=1, le=100)
	lead_id: str | None = Field(None, max_length=140)
	search_query: str | None = Field(None, max_length=140)
	sort_by: Literal["loan_amount", "creation"] | None = None
	sort_order: Literal["asc", "desc"] | None = None

	@model_validator(mode="after")
	def validate_loan_amount_range(self):
		if self.min_loan_amount is not None and self.max_loan_amount is not None:
			if self.min_loan_amount > self.max_loan_amount:
				raise ValueError("min_loan_amount cannot be greater than max_loan_amount.")
		return self


class BrowseProductsSchema(BaseModel):
	search: str | None = Field(None, max_length=140)
	bank: str | None = Field(None, max_length=140)
	loan_product: str | None = Field(None, max_length=140)
	min_amount: float | None = Field(None, ge=0, le=999999999999.0)
	max_amount: float | None = Field(None, ge=0, le=999999999999.0)
	limit: int = Field(20, ge=1, le=100)
	start: int = Field(0, ge=0)

	@model_validator(mode="after")
	def validate_amount_range(self):
		if self.min_amount is not None and self.max_amount is not None:
			if self.min_amount > self.max_amount:
				raise ValueError("min_amount cannot be greater than max_amount.")
		return self


class DownloadSupportingDocumentSchema(BaseModel):
	file_id: str = Field(..., min_length=1, max_length=140)
	view: int | None = None


class DeleteSupportingDocumentSchema(BaseModel):
	application_id: str = Field(..., min_length=1, max_length=140)
	file_id: str = Field(..., min_length=1, max_length=140)


class UpdateLoanStatusSchema(BaseModel):
	application_id: str = Field(..., min_length=1, max_length=140)
	status: Literal["Draft", "Processing", "Approved", "Rejected"]
	reason: str | None = Field(None, max_length=2000)


class UpdateLoanStepSchema(BaseModel):
	application_id: str = Field(..., min_length=1, max_length=140)
	step: int = Field(..., ge=1, le=4)


class AssignLoanOfficerSchema(BaseModel):
	application_id: str = Field(..., min_length=1, max_length=140)
	loan_officer: str = Field(..., min_length=1, max_length=140)


def _get_app(application_id):
	if not frappe.db.exists("A2C Loan Application", application_id):
		frappe.throw(_("Loan Application {0} not found").format(application_id), frappe.DoesNotExistError)
	return frappe.get_doc("A2C Loan Application", application_id)


def _get_lead(lead_id):
	if not frappe.db.exists("A2C Lead", lead_id):
		frappe.throw(_("A2C Lead {0} not found").format(lead_id), frappe.DoesNotExistError)
	return frappe.get_doc("A2C Lead", lead_id)


def _get_consent_details(consent_id: str) -> dict:
	"""Helper to retrieve and format consent request details and fields."""
	frappe.has_permission("A2C Consent Request", "read", doc=consent_id, throw=True)
	res_fields = (
		frappe.db.get_value(
			"A2C Consent Request",
			consent_id,
			["websub_delivered_at", "consent_type", "purpose", "validity_from", "validity_to"],
			as_dict=True,
		)
		or {}
	)
	# websub_delivered_at is a Datetime: emit a system-tz-aware ISO 8601 string
	# (…T…+HH:MM) to match every other timestamp this API returns, instead of a
	# bare, offset-less DB string.
	if res_fields.get("websub_delivered_at"):
		res_fields["websub_delivered_at"] = to_tz_aware_iso(res_fields["websub_delivered_at"])
	# validity_from/validity_to are Date fields (no time-of-day) — a plain
	# YYYY-MM-DD string is correct and carries no timezone.
	for key in ["validity_from", "validity_to"]:
		if res_fields.get(key):
			res_fields[key] = str(res_fields[key])

	requested_data_fields = frappe.get_all(
		"A2C Consent Data", filters={"parent": consent_id}, fields=["field_name", "field_value"]
	)
	res_fields["requested_data_fields"] = requested_data_fields
	return res_fields


@frappe.whitelist(allow_guest=False)
@validate_request(GetBasicProfileSchema)
@handle_api_errors
def get_basic_profile(lead_id: str | None = None, include_consent_data: bool | None = None):
	"""
	Retrieves the basic profile information of a farmer associated with a lead.
	"""
	frappe.has_permission("A2C Lead", "read", doc=lead_id, throw=True)
	lead_doc = _get_lead(lead_id)

	profile_name = lead_doc.farmer_profile
	consent_id = frappe.db.get_value(
		"A2C Consent Request", {"lead": lead_id}, "name", order_by="creation desc"
	)

	if not profile_name and not consent_id:
		frappe.throw(_("Farmer Profile not found for this lead"), frappe.ValidationError)

	data = {"farmer_profile_created": bool(profile_name)}

	if profile_name:
		frappe.has_permission("A2C Farmer Profile", "read", doc=profile_name, throw=True)
		profile = frappe.get_doc("A2C Farmer Profile", profile_name)
		data.update(
			{
				"first_name": profile.first_name,
				"last_name": profile.last_name,
				"phone_number": profile.phone_number,
				"email": profile.email,
				"region": profile.region,
				"woreda": profile.woreda,
				"kebele": profile.kebele,
			}
		)
		consent_id = profile.consent_id or consent_id

	if consent_id:
		consent_doc_data = (
			frappe.db.get_value(
				"A2C Consent Request", consent_id, ["status", "otp_verified_at"], as_dict=True
			)
			or {}
		)
		data["consent_request"] = {
			"name": consent_id,
			"status": consent_doc_data.get("status"),
			"otp_verified": bool(consent_doc_data.get("otp_verified_at")),
		}
		if include_consent_data:
			data.update(_get_consent_details(consent_id))

	return success_response(data=data, message="Basic profile retrieved successfully")


@frappe.whitelist(allow_guest=False, methods=["POST"])
@validate_request(UpdateBasicProfileSchema)
@handle_api_errors
def update_basic_profile(
	lead_id: str | None = None,
	email: str | None = None,
	region: str | None = None,
	woreda: str | None = None,
	kebele: str | None = None,
):
	"""
	Updates the email and location details for a lead's farmer profile.
	"""
	frappe.has_permission("A2C Lead", "write", doc=lead_id, throw=True)
	lead_doc = _get_lead(lead_id)
	if not lead_doc.farmer_profile:
		frappe.throw(_("Farmer Profile not found for this lead"), frappe.ValidationError)

	frappe.has_permission("A2C Farmer Profile", "write", doc=lead_doc.farmer_profile, throw=True)
	farmer_doc = frappe.get_doc("A2C Farmer Profile", lead_doc.farmer_profile)

	changed = False
	updates = {"email": email, "region": region, "woreda": woreda, "kebele": kebele}

	for field, value in updates.items():
		if value is not None:
			if farmer_doc.meta.has_field(field) and farmer_doc.get(field) != value:
				farmer_doc.set(field, value)
				changed = True
			if lead_doc.meta.has_field(field) and lead_doc.get(field) != value:
				lead_doc.set(field, value)
				changed = True

	if changed:
		farmer_doc.save(ignore_permissions=False)
		lead_doc.save(ignore_permissions=False)

	return success_response(
		data={
			"email": farmer_doc.email,
			"region": farmer_doc.region,
			"woreda": farmer_doc.woreda,
			"kebele": farmer_doc.kebele,
		},
		message="Basic profile updated successfully",
	)


@frappe.whitelist(allow_guest=False)
@validate_request(LoanApplicationIDSchema)
@handle_api_errors
def get_full_profile(**kwargs):
	"""
	Retrieves the full profile details of a loan application.
	"""
	application_id = kwargs.get("application_id")
	frappe.has_permission("A2C Loan Application", "read", doc=application_id, throw=True)
	doc = _get_app(application_id)
	farmer_profile = frappe.db.get_value("A2C Lead", doc.lead_id, "farmer_profile")

	data = {
		"application_id": doc.name,
		"lead_id": doc.lead_id,
		"farmer_profile": farmer_profile,
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
		"loan_amount": float(doc.loan_amount) if doc.loan_amount else 0.0,
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
		"total_farmland_size_as_landowner": float(doc.total_farmland_size_as_landowner)
		if doc.total_farmland_size_as_landowner
		else 0.0,
		"total_farmland_size_as_crop_sharing": float(doc.total_farmland_size_as_crop_sharing)
		if doc.total_farmland_size_as_crop_sharing
		else 0.0,
		"total_farmland_size_as_rented": float(doc.total_farmland_size_as_rented)
		if doc.total_farmland_size_as_rented
		else 0.0,
		"farmland_size_hectares": doc.farmland_size_hectares,
		"land_ownership_status": doc.land_ownership_status,
		"soil_fertility_minerals": doc.soil_fertility_minerals,
		"moisture_levels": doc.moisture_levels,
		"certification_id": doc.certification_id,
		"certification_photo_url": doc.certification_photo_url,
	}

	return success_response(data=data, message="Full profile retrieved successfully")


@frappe.whitelist(allow_guest=False)
@handle_api_errors
def get_loan_summary():
	frappe.has_permission("A2C Loan Application", "read", throw=True)

	meta = frappe.get_meta("A2C Loan Application")
	has_loan_officer = meta.has_field("loan_officer")

	fields = ["status", {"COUNT": "*"}]
	group_by = "status"
	if has_loan_officer:
		fields.insert(1, "loan_officer")
		group_by = "status, loan_officer"

	counts = frappe.get_list(
		"A2C Loan Application", fields=fields, group_by=group_by, ignore_permissions=False
	)

	summary = {"total": 0, "processing": 0, "approved": 0, "rejected": 0}
	my_applications = 0
	unassigned = 0

	user = frappe.session.user
	for row in counts:
		count = row.get("COUNT(*)", 0)
		summary["total"] += count

		if row.status == "Processing":
			summary["processing"] += count
		elif row.status == "Approved":
			summary["approved"] += count
		elif row.status == "Rejected":
			summary["rejected"] += count

		if has_loan_officer:
			if row.loan_officer == user:
				my_applications += count
			elif not row.loan_officer:
				unassigned += count

	summary["tab_counts"] = {"all": summary["total"]}
	if has_loan_officer:
		summary["tab_counts"]["my"] = my_applications
		summary["tab_counts"]["unassigned"] = unassigned

	return success_response(data=summary, message="Loan summary retrieved successfully")


@frappe.whitelist(allow_guest=False)
@handle_api_errors
def get_loan_metadata():
	"""
	Retrieves status dropdown option lists for loan applications.
	"""
	frappe.has_permission("A2C Loan Application", "read", throw=True)

	meta = frappe.get_meta("A2C Loan Application")
	status_field = meta.get_field("status")

	statuses = (
		[s for s in status_field.options.split("\n") if s] if status_field and status_field.options else []
	)

	return success_response(data={"statuses": statuses}, message="Loan metadata retrieved successfully")


@frappe.whitelist(allow_guest=False)
@validate_request(BrowseProductsSchema)
@handle_api_errors
def browse_products(**kwargs):
	"""Browse Active loan products to attach one to a farmer's application.

	This is the Development Agent's product-discovery path. The seller endpoint
	(seller/loan_products.list_products) is for a bank managing its own catalog and
	shows only that bank's products (incl. Drafts); a Development Agent has no write
	access there. Here we read A2C Loan Product via get_list, which applies both
	DocPerm (Dev Agent has read) and the bank_scope_query hook:

	  - Development Agent is bank-unbound -> sees Active products across ALL banks.
	  - A bank user hitting this still sees only their own bank (hook scopes them).

	Only Active products are returned -- Drafts/Archived are not offerable to farmers.
	"""
	frappe.has_permission("A2C Loan Product", "read", throw=True)

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
		order_by="product_name asc",
		limit_page_length=limit,
		limit_start=start,
	)

	total = len(frappe.get_list("A2C Loan Product", filters=filters, pluck="name", limit_page_length=0))
	pagination = {
		"page": (start // limit) + 1,
		"limit": limit,
		"total": total,
		"total_pages": -(-total // limit),
		"has_next": start + limit < total,
	}

	return success_response(
		data={"products": products},
		message="Products retrieved successfully",
		pagination=pagination,
	)


@frappe.whitelist(allow_guest=False)
@validate_request(GetAllLoansSchema)
@handle_api_errors
def get_all_loans(**kwargs):
	"""
	Retrieves a paginated list of all loan applications matching given filter parameters.
	"""
	frappe.has_permission("A2C Loan Application", "read", throw=True)

	status = kwargs.get("status")
	loan_amount = kwargs.get("loan_amount")
	min_loan_amount = kwargs.get("min_loan_amount")
	max_loan_amount = kwargs.get("max_loan_amount")
	loan_type = kwargs.get("loan_type")
	location = kwargs.get("location")
	phone_number = kwargs.get("phone_number")
	from_date = kwargs.get("from_date")
	to_date = kwargs.get("to_date")
	loan_officer = kwargs.get("loan_officer")
	page = kwargs.get("page") or 1
	page_size = kwargs.get("page_size") or 20
	lead_id = kwargs.get("lead_id")
	search_query = kwargs.get("search_query")

	# Sorting: sort_by is Literal-constrained to safe columns, so it can't inject
	# into order_by. Defaults preserve the prior "newest first" behavior.
	sort_by = kwargs.get("sort_by") or "creation"
	sort_order = "asc" if kwargs.get("sort_order") == "asc" else "desc"
	order_by = f"{sort_by} {sort_order}"

	offset = (page - 1) * page_size

	filters = {}

	if status:
		allowed_statuses = ("Draft", "Processing", "Approved", "Rejected")
		valid_statuses = parse_multi_value(status, allowed_statuses)
		if valid_statuses:
			filters["status"] = ["in", valid_statuses]

	if lead_id:
		filters["lead_id"] = lead_id

	if min_loan_amount is not None and max_loan_amount is not None:
		filters["loan_amount"] = ("between", [flt(min_loan_amount), flt(max_loan_amount)])
	elif min_loan_amount is not None:
		filters["loan_amount"] = (">=", flt(min_loan_amount))
	elif max_loan_amount is not None:
		filters["loan_amount"] = ("<=", flt(max_loan_amount))
	elif loan_amount is not None:
		filters["loan_amount"] = flt(loan_amount)

	if loan_type:
		# loan_type on A2C Loan Application is a free-text Data field (no Select options),
		# so accept the provided value(s) as-is. Single value or comma-separated multi-value.
		valid_loan_types = parse_multi_value(loan_type)
		if valid_loan_types:
			filters["loan_type"] = ["in", valid_loan_types]

	if location:
		filters["location"] = ("like", f"{location}%")

	if phone_number:
		filters["phone_number"] = ("like", f"{phone_number}%")

	# Assignment tab filter: exactly three options, matching get_loan_summary's
	# tab_counts -- "all" (no filter), "my" (loans where the caller is the officer),
	# "unassigned" (no officer). Any other value is ignored (treated as "all").
	if loan_officer:
		tab = str(loan_officer).strip().lower()
		if tab == "my":
			filters["loan_officer"] = frappe.session.user
		elif tab == "unassigned":
			filters["loan_officer"] = ["in", ["", None]]

	if from_date and to_date:
		filters["creation"] = ("between", [from_date, f"{to_date} 23:59:59"])
	elif from_date:
		filters["creation"] = (">=", from_date)
	elif to_date:
		filters["creation"] = ("<=", f"{to_date} 23:59:59")

	or_filters = []
	if search_query:
		search_query_param = f"%{search_query}%"
		or_filters.append(["name", "like", search_query_param])
		or_filters.append(["phone_number", "like", search_query_param])
		or_filters.append(["farmer_id", "like", search_query_param])
		or_filters.append(["first_name", "like", search_query_param])
		or_filters.append(["last_name", "like", search_query_param])
		or_filters.append(["loan_product_name", "like", search_query_param])
		or_filters.append(["loan_product", "like", search_query_param])

	# Count via get_list (not frappe.db.count) so the bank_scope_query hook is
	# applied identically to the records query below — otherwise the total can
	# report cross-bank rows that aren't in the returned page.
	count_res = frappe.get_list(
		"A2C Loan Application",
		filters=filters,
		or_filters=or_filters or None,
		fields=[{"COUNT": "*"}],
		ignore_permissions=False,
	)
	total_records = count_res[0].get("COUNT(*)") if count_res else 0

	records = frappe.get_list(
		"A2C Loan Application",
		filters=filters,
		or_filters=or_filters or None,
		fields=[
			"name as application_id",
			"status",
			"current_step as step",
			"lead_id",
			"loan_amount",
			"loan_type",
			"loan_product",
			"loan_product_name",
			"location",
			"phone_number",
			"creation",
		],
		order_by=order_by,
		limit_start=offset,
		page_length=page_size,
		ignore_permissions=False,
	)

	for r in records:
		r["loan_amount"] = float(r["loan_amount"]) if r.get("loan_amount") else 0.0
		r["step"] = cint(r.get("step"))
		r["creation"] = to_tz_aware_iso(r["creation"])

	total_pages = -(-total_records // page_size)
	has_next = offset + page_size < total_records

	pagination = {
		"page": page,
		"limit": page_size,
		"total": total_records,
		"total_pages": total_pages,
		"has_next": has_next,
	}

	return success_response(
		data=records, message="Loan applications retrieved successfully", pagination=pagination
	)


@frappe.whitelist(allow_guest=False, methods=["POST"])
@validate_request(LoanApplicationIDSchema)
@handle_api_errors
def upload_supporting_documents(**kwargs):
	"""
	Uploads private supporting document files for a specific loan application.
	"""
	application_id = kwargs.get("application_id")

	frappe.has_permission("A2C Loan Application", "write", doc=application_id, throw=True)
	doc = _get_app(application_id)

	if not frappe.request.files:
		frappe.throw(_("No files found in request"), frappe.ValidationError)

	MAX_FILE_COUNT = 5
	if len(frappe.request.files) > MAX_FILE_COUNT:
		frappe.throw(
			_("Maximum {0} files can be uploaded at a time.").format(MAX_FILE_COUNT), frappe.ValidationError
		)

	uploaded_files = []
	ALLOWED_EXTENSIONS = (".pdf", ".png", ".jpg", ".jpeg")
	MAX_FILE_SIZE = 5 * 1024 * 1024

	for _key, file_storage in frappe.request.files.items():
		filename = file_storage.filename.lower()
		if not filename.endswith(ALLOWED_EXTENSIONS):
			frappe.throw(
				_("Invalid file type for {0}. Only PDF, PNG, and JPG are allowed.").format(filename),
				frappe.ValidationError,
			)

		file_storage.seek(0, 2)
		file_size = file_storage.tell()
		file_storage.seek(0)
		if file_size > MAX_FILE_SIZE:
			frappe.throw(_("File {0} exceeds the 5MB size limit.").format(filename), frappe.ValidationError)

		content = file_storage.read()

		# Content sniffing (magic bytes validation) to prevent extension spoofing
		content_prefix = content[:8]
		is_pdf = content_prefix.startswith(b"%PDF")
		is_png = content_prefix.startswith(b"\x89PNG\r\n\x1a\n")
		is_jpeg = content_prefix.startswith(b"\xff\xd8\xff")

		if filename.endswith(".pdf") and not is_pdf:
			frappe.throw(
				_("File {0} is not a valid PDF file.").format(file_storage.filename), frappe.ValidationError
			)
		elif filename.endswith((".jpg", ".jpeg")) and not is_jpeg:
			frappe.throw(
				_("File {0} is not a valid JPEG/JPG image.").format(file_storage.filename),
				frappe.ValidationError,
			)
		elif filename.endswith(".png") and not is_png:
			frappe.throw(
				_("File {0} is not a valid PNG image.").format(file_storage.filename), frappe.ValidationError
			)
		file_doc = frappe.get_doc(
			{
				"doctype": "File",
				"file_name": file_storage.filename,
				"content": content,
				"attached_to_doctype": "A2C Loan Application",
				"attached_to_name": doc.name,
				"is_private": 1,
			}
		)
		file_doc.insert(ignore_permissions=False)
		uploaded_files.append(
			{"name": file_doc.name, "file_url": file_doc.file_url, "file_name": file_doc.file_name}
		)

	if uploaded_files:
		filenames = ", ".join(f["file_name"] for f in uploaded_files)
		description = _("Uploaded {0} document(s): {1}\nUpdated by: {2}").format(
			len(uploaded_files), filenames, frappe.session.user
		)
		audit_event = frappe.new_doc("A2C Loan Application Audit Event")
		audit_event.loan_application = application_id
		audit_event.event_type = "Document Uploaded"
		audit_event.event_title = "Document Uploaded"
		audit_event.event_description = description
		audit_event.insert()

	return success_response(
		data={"uploaded_files": uploaded_files}, message="Supporting documents uploaded successfully"
	)


@frappe.whitelist(allow_guest=False)
@validate_request(LoanApplicationIDSchema)
@handle_api_errors
def get_supporting_documents(**kwargs):
	"""
	Retrieves list information for all files uploaded under a loan application.
	"""
	application_id = kwargs.get("application_id")

	frappe.has_permission("A2C Loan Application", "read", doc=application_id, throw=True)
	_get_app(application_id)

	files = frappe.get_list(
		"File",
		filters={"attached_to_doctype": "A2C Loan Application", "attached_to_name": application_id},
		fields=["name", "file_name", "file_url", "creation"],
		ignore_permissions=False,
	)

	for f in files:
		f["creation"] = to_tz_aware_iso(f["creation"])

	return success_response(data=files, message="Supporting documents retrieved successfully")


@frappe.whitelist(allow_guest=False)
@validate_request(DownloadSupportingDocumentSchema)
@handle_api_errors
def download_supporting_document(**kwargs):
	"""
	Downloads or streams the content of an uploaded private supporting document.
	"""
	file_id = kwargs.get("file_id")
	view = kwargs.get("view")

	file_doc = None
	if frappe.db.exists("File", file_id):
		file_doc = frappe.get_doc("File", file_id)

	if file_doc:
		if file_doc.attached_to_doctype and file_doc.attached_to_name:
			frappe.has_permission(
				file_doc.attached_to_doctype, "read", doc=file_doc.attached_to_name, throw=True
			)
		else:
			frappe.has_permission("File", "read", doc=file_doc, throw=True)
	else:
		frappe.has_permission("File", "read", throw=True)
		frappe.throw(_("File not found"), frappe.DoesNotExistError)

	frappe.local.response.filename = file_doc.file_name
	frappe.local.response.filecontent = file_doc.get_content()
	frappe.local.response.type = "download"
	if view:
		frappe.local.response.display_content_as = "inline"


@frappe.whitelist(allow_guest=False, methods=["POST"])
@validate_request(DeleteSupportingDocumentSchema)
@handle_api_errors
def delete_supporting_document(**kwargs):
	"""
	Deletes an attached supporting document from a loan application.
	"""
	application_id = kwargs.get("application_id")
	file_id = kwargs.get("file_id")

	frappe.has_permission("A2C Loan Application", "write", doc=application_id, throw=True)
	_get_app(application_id)

	if not frappe.db.exists(
		"File",
		{"name": file_id, "attached_to_doctype": "A2C Loan Application", "attached_to_name": application_id},
	):
		frappe.throw(_("File not found or not attached to this application"), frappe.DoesNotExistError)

	file_name = frappe.db.get_value("File", file_id, "file_name")
	frappe.delete_doc("File", file_id, ignore_permissions=False)

	description = _("Deleted document: {0}\nUpdated by: {1}").format(file_name, frappe.session.user)
	audit_event = frappe.new_doc("A2C Loan Application Audit Event")
	audit_event.loan_application = application_id
	audit_event.event_type = "Document Deleted"
	audit_event.event_title = "Document Deleted"
	audit_event.event_description = description
	audit_event.insert()

	return success_response(message=_("Document deleted successfully."))


@frappe.whitelist(allow_guest=False, methods=["POST"])
@validate_request(LeadIDSchema)
@handle_api_errors
def create_loan_application(**kwargs):
	"""
	Creates an A2C Loan Application by copying data from the Lead's linked Farmer Profile and Credit Information.
	"""
	lead_id = kwargs.get("lead_id")
	frappe.has_permission("A2C Lead", "read", doc=lead_id, throw=True)
	frappe.has_permission("A2C Loan Application", "create", throw=True)
	lead_doc = _get_lead(lead_id)

	# Acquire a database-level transaction row/gap lock via raw SQL FOR UPDATE to prevent TOCTOU
	# race conditions during concurrent API requests.
	# Alternative unique constraints cannot be enforced on the database layer because some values
	# (such as lead_id) are not guaranteed to be unique under database schemas without custom migration scripts.
	# Lock-only query (returns nothing to the caller); the permission-checked read
	# is the frappe.get_list below. Locking across banks is safe. bank-scope-exempt
	frappe.db.sql(
		"SELECT name FROM `tabA2C Loan Application` WHERE lead_id = %s FOR UPDATE", (lead_id,)
	)  # bank-scope-exempt
	existing = frappe.get_list(
		"A2C Loan Application",
		filters={"lead_id": lead_id},
		fields=["name"],
		limit=1,
		ignore_permissions=False,
	)
	if existing:
		frappe.throw(_("Loan application already exists for this lead"), frappe.ValidationError)

	farmer_profile_name = lead_doc.get("farmer_profile")
	if not farmer_profile_name:
		frappe.throw(
			_("No Farmer Profile found for this lead. Webhook consent might not be completed."),
			frappe.ValidationError,
		)

	frappe.has_permission("A2C Farmer Profile", "read", doc=farmer_profile_name, throw=True)
	farmer_profile = frappe.get_doc("A2C Farmer Profile", farmer_profile_name)

	credit_infos = frappe.get_list(
		"A2C Credit Information",
		filters={"lead": lead_id},
		fields=["loan_type", "loan_amount", "purpose_message", "loan_product"],
		order_by="creation desc",
		limit=1,
		ignore_permissions=False,
	)

	if not credit_infos:
		frappe.throw(
			_(
				"Credit Information is missing for this lead. A loan application requires a valid loan amount."
			),
			frappe.ValidationError,
		)

	credit_info = credit_infos[0]

	# bank is mandatory on the loan application and is derived from the chosen
	# product. Without a product we cannot attribute the application to a bank.
	loan_product = credit_info.get("loan_product")
	if not loan_product:
		frappe.throw(
			_("Credit Information for this lead has no loan product, so the bank cannot be determined."),
			frappe.ValidationError,
		)

	# bank-scope-exempt — reading the product's own bank to stamp the application; not a cross-bank query.
	bank = frappe.db.get_value("A2C Loan Product", loan_product, "bank")
	if not bank:
		frappe.throw(
			_("Loan Product {0} is not linked to a bank.").format(loan_product), frappe.ValidationError
		)

	loan_app = frappe.new_doc("A2C Loan Application")
	loan_app.lead_id = lead_id
	loan_app.farmer_profile = farmer_profile.name

	# Dynamically copy all matching fields from Farmer Profile to Loan Application
	fields_to_ignore = {"name", "owner", "creation", "modified", "modified_by", "idx", "docstatus"}
	for field in farmer_profile.meta.fields:
		if field.fieldname not in fields_to_ignore and loan_app.meta.has_field(field.fieldname):
			loan_app.set(field.fieldname, farmer_profile.get(field.fieldname))

	loan_app.loan_type = credit_info.loan_type
	loan_app.loan_amount = flt(credit_info.loan_amount)
	loan_app.requested_amount = flt(credit_info.loan_amount)
	loan_app.loan_reason = credit_info.purpose_message
	loan_app.loan_product = loan_product
	loan_app.loan_product_name = frappe.db.get_value("A2C Loan Product", loan_product, "product_name")
	loan_app.bank = bank
	loan_app.status = "Draft"

	loan_app.insert(ignore_permissions=False)

	# NOTE: the lead is intentionally NOT advanced here. Lead status transitions go through the
	# A2C Lead Workflow (Active -> Verified -> Processed), driven by the frontend via
	# update_lead_status. There is no Active -> Processed shortcut, so loan creation does not
	# move the lead; the client applies the workflow actions explicitly.

	return success_response(
		data={
			"application_id": loan_app.name,
			"lead_status": lead_doc.status,
			"application": {
				"name": loan_app.name,
				"status": loan_app.status,
				"farmer_profile": loan_app.farmer_profile,
				"first_name": loan_app.first_name,
				"last_name": loan_app.last_name,
				"loan_type": loan_app.loan_type,
				"loan_amount": loan_app.loan_amount,
				"current_step": loan_app.current_step,
			},
		},
		message="Loan application created successfully",
	)


@frappe.whitelist(allow_guest=False, methods=["POST"])
@validate_request(UpdateLoanStatusSchema)
@handle_api_errors
def update_loan_status(**kwargs):
	"""
	Updates the status of a loan application. Cannot update if current status is Rejected or Approved.
	"""
	application_id = kwargs.get("application_id")
	status = kwargs.get("status")
	reason = kwargs.get("reason")

	if reason:
		reason = sanitize_html(reason)

	# Read (not write) is the gate here: Bank Agent is a read-only role on loan
	# applications but is authorised to change *status* only. Authorisation for the
	# transition itself is enforced by the workflow's per-role `allowed` gate below,
	# and read is bank-scoped (bank_scope_doc), so an agent can only act on his own
	# bank's loans. ignore_permissions lets the workflow save/submit despite the
	# role lacking write.
	frappe.has_permission("A2C Loan Application", "read", doc=application_id, throw=True)
	doc = _get_app(application_id)
	doc.flags.ignore_permissions = True

	# Apply the status change through the A2C Loan Application Workflow. The workflow enforces
	# legal transitions and per-role gating, and submits the doc (docstatus 1) on
	# Approve/Reject. Illegal/unauthorised targets raise ValidationError.
	apply_status_transition(doc, status)

	# Insert Loan Application Audit Event
	description = _("Changed to {0}").format(status)
	if reason:
		description += f"\nReason: {reason}"
	description += f"\nUpdated by: {frappe.session.user}"

	audit_event = frappe.new_doc("A2C Loan Application Audit Event")
	audit_event.loan_application = application_id
	audit_event.event_type = "Status Changed"
	audit_event.event_title = "Status Updated"
	audit_event.event_description = description
	audit_event.insert()

	return success_response(message=_("Loan status updated to {0}.").format(status))


@frappe.whitelist(allow_guest=False, methods=["POST"])
@validate_request(UpdateLoanStepSchema)
@handle_api_errors
def update_loan_step(**kwargs):
	"""
	Updates the current step of a loan application.
	"""
	application_id = kwargs.get("application_id")
	step = kwargs.get("step")

	frappe.has_permission("A2C Loan Application", "write", doc=application_id, throw=True)
	doc = _get_app(application_id)

	doc.current_step = step
	doc.save(ignore_permissions=False)
	return success_response(
		data={"application_id": doc.name, "current_step": doc.current_step},
		message=f"Loan application step updated to {doc.current_step}",
	)


@frappe.whitelist(allow_guest=False, methods=["POST"])
@validate_request(AssignLoanOfficerSchema)
@handle_api_errors
def assign_loan_officer(**kwargs):
	"""
	Assigns a loan officer to a loan application.
	"""
	application_id = kwargs.get("application_id")
	loan_officer = kwargs.get("loan_officer")

	frappe.has_permission("A2C Loan Application", "write", doc=application_id, throw=True)

	if not frappe.db.exists("User", {"email": loan_officer, "enabled": 1}):
		if not frappe.db.exists("User", {"name": loan_officer, "enabled": 1}):
			frappe.throw(
				_("User '{0}' is not a valid active user").format(loan_officer), frappe.DoesNotExistError
			)

	doc = _get_app(application_id)
	doc.loan_officer = loan_officer
	doc.save(ignore_permissions=False)

	officer_name = (
		frappe.db.get_value("User", {"email": loan_officer}, "full_name")
		or frappe.db.get_value("User", loan_officer, "full_name")
		or loan_officer
	)

	return success_response(
		data={
			"application_id": doc.name,
			"loan_officer": doc.loan_officer,
			"loan_officer_name": officer_name,
		},
		message="Loan officer assigned successfully.",
	)
