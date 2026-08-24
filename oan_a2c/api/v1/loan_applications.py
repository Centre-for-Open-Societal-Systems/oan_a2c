from typing import Literal

import frappe
from frappe import _
from frappe.utils import cint, flt, sanitize_html
from pydantic import BaseModel, Field, field_validator, model_validator

from oan_a2c.a2c_marketplace.doctype_schemas import MAX_LOAN_AMOUNT, MAX_QUERY_AMOUNT
from oan_a2c.a2c_marketplace.permissions import get_user_farmer_profile, is_farmer
from oan_a2c.a2c_marketplace.roles import ADMIN_ROLE, BANK_ADMIN_ROLE, BANK_AGENT_ROLE, DEVELOPMENT_AGENT_ROLE
from oan_a2c.api.utils import (
	SafeDate,
	SafeEmail,
	apply_status_transition,
	get_workflow_state_names,
	handle_api_errors,
	parse_multi_value,
	require_role,
	status_has_tag,
	success_response,
	to_tz_aware_iso,
	validate_request,
)


class GetBasicProfileSchema(BaseModel):
	lead_id: str | None = Field(None, max_length=140)
	include_consent_data: int | None = None


class UpdateBasicProfileSchema(BaseModel):
	lead_id: str | None = Field(None, max_length=140)
	email: SafeEmail = None
	region: str | None = Field(None, max_length=140)
	woreda: str | None = Field(None, max_length=140)
	kebele: str | None = Field(None, max_length=140)


class LoanApplicationIDSchema(BaseModel):
	application_id: str = Field(..., min_length=1, max_length=140)


class LeadIDSchema(BaseModel):
	lead_id: str = Field(..., min_length=1, max_length=140)


class GetAllLoansSchema(BaseModel):
	# Filters on the bank pipeline stage (A2C Loan Status Stage.stage_id), not the
	# archetype workflow status. Stages are defined per-bank, so unlike `archetype`
	# below there is no single global allowed-list to validate against here -- a
	# stage_id that doesn't exist (or belongs to another bank) just matches zero
	# rows, same as any other free-text filter (e.g. loan_type).
	status: str | None = Field(None, max_length=140)
	# Filters on the coarse workflow archetype (Active / In Transition / Completed /
	# Rejected / Cancelled). Separate from `status` above -- use this when the caller
	# wants the bucket, not the bank-specific pipeline stage.
	archetype: str | None = Field(None, max_length=140)
	# MAX_QUERY_AMOUNT, not MAX_LOAN_AMOUNT: these are search bounds over existing
	# applications, and api/v1/leads.py accepts credit-information amounts far above
	# the catalogue cap. Capping the filter lower would hide those rows from search.
	loan_amount: float | None = Field(None, ge=0, le=MAX_QUERY_AMOUNT)
	min_loan_amount: float | None = Field(None, ge=0, le=MAX_QUERY_AMOUNT)
	max_loan_amount: float | None = Field(None, ge=0, le=MAX_QUERY_AMOUNT)
	loan_type: str | None = Field(None, max_length=140)
	# Location is three Data fields on the application, not one `location` column.
	# A `location` filter used to be accepted here and put a nonexistent column into
	# the WHERE clause, which failed the entire query rather than being ignored.
	region: str | None = Field(None, max_length=140)
	woreda: str | None = Field(None, max_length=140)
	kebele: str | None = Field(None, max_length=140)
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

	@field_validator("archetype")
	@classmethod
	def validate_archetypes(cls, value: str | None):
		if value is None:
			return value
		allowed_statuses = get_workflow_state_names("A2C Loan Application")
		if allowed_statuses:
			requested = parse_multi_value(value)
			invalid = [status for status in requested if status not in allowed_statuses]
			if invalid:
				raise ValueError(
					"Invalid value '{0}'. Allowed values: {1}".format(invalid[0], ", ".join(allowed_statuses))
				)
		return value


class BrowseProductsSchema(BaseModel):
	search: str | None = Field(None, max_length=140)
	bank: str | None = Field(None, max_length=140)
	loan_product: str | None = Field(None, max_length=140)
	min_amount: float | None = Field(None, ge=0, le=MAX_LOAN_AMOUNT)
	max_amount: float | None = Field(None, ge=0, le=MAX_LOAN_AMOUNT)
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
	Retrieves the basic profile information of a farmer associated with a lead (for staff)
	or for the currently authenticated farmer.
	"""
	user = frappe.session.user
	if lead_id:
		frappe.has_permission("A2C Lead", "read", doc=lead_id, throw=True)
		lead_doc = _get_lead(lead_id)

		profile_name = lead_doc.farmer_profile

		# The lead caches its latest consent attempt, so this is a field read rather than
		# a sort over every consent request raised for the lead. Falls back to that scan
		# for leads whose cache predates backfill_lead_consent_id.
		consent_id = lead_doc.consent_id or frappe.db.get_value(
			"A2C Consent Request",
			{"reference_doctype": "A2C Lead", "reference_name": lead_id},
			"name",
			order_by="creation desc",
		)

		if not profile_name and not consent_id:
			frappe.throw(_("Farmer Profile not found for this lead"), frappe.ValidationError)
	else:
		if not is_farmer(user) or user == "Administrator":
			frappe.throw(_("lead_id is required"), frappe.ValidationError)

		profile_name = get_user_farmer_profile(user)
		consent_id = None
		if profile_name:
			consent_id = frappe.db.get_value("A2C Farmer Profile", profile_name, "consent_id")
		if not consent_id:
			consent_id = frappe.db.get_value(
				"A2C Consent Request",
				{"owner": user},
				"name",
				order_by="creation desc",
			)

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
	else:
		data["consent_request"] = None

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
	Updates the email and location details for a lead's farmer profile or the authenticated farmer.
	"""
	user = frappe.session.user
	lead_doc = None
	if lead_id:
		frappe.has_permission("A2C Lead", "write", doc=lead_id, throw=True)
		lead_doc = _get_lead(lead_id)
		if not lead_doc.farmer_profile:
			frappe.throw(_("Farmer Profile not found for this lead"), frappe.ValidationError)
		profile_name = lead_doc.farmer_profile
	else:
		if not is_farmer(user) or user == "Administrator":
			frappe.throw(_("lead_id is required"), frappe.ValidationError)
		profile_name = get_user_farmer_profile(user)
		if not profile_name:
			frappe.throw(_("Farmer Profile not found"), frappe.ValidationError)

	frappe.has_permission("A2C Farmer Profile", "write", doc=profile_name, throw=True)
	farmer_doc = frappe.get_doc("A2C Farmer Profile", profile_name)

	changed = False
	updates = {"email": email, "region": region, "woreda": woreda, "kebele": kebele}

	for field, value in updates.items():
		if value is not None:
			if farmer_doc.meta.has_field(field) and farmer_doc.get(field) != value:
				farmer_doc.set(field, value)
				changed = True
			if lead_doc and lead_doc.meta.has_field(field) and lead_doc.get(field) != value:
				lead_doc.set(field, value)
				changed = True

	if changed:
		farmer_doc.save(ignore_permissions=False)
		if lead_doc:
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

	data = {
		"application_id": doc.name,
		"lead_id": doc.lead_id,
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

	return success_response(data=data, message="Full profile retrieved successfully")


@frappe.whitelist(allow_guest=False)
@handle_api_errors
def get_loan_summary():
	frappe.has_permission("A2C Loan Application", "read", throw=True)

	meta = frappe.get_meta("A2C Loan Application")
	has_loan_officer = meta.has_field("loan_officer")

	fields = ["status", "stage_label", {"COUNT": "*"}]
	group_by = "status, stage_label"
	if has_loan_officer:
		fields.insert(2, "loan_officer")
		group_by += ", loan_officer"

	counts = frappe.get_list(
		"A2C Loan Application", fields=fields, group_by=group_by, ignore_permissions=False
	)

	from oan_a2c.a2c_marketplace.stages import ARCHETYPE_STATES

	# Two buckets, deliberately. `stages` is what the owning bank calls each step and
	# differs between tenants, so it cannot be keyed on by a caller that spans banks.
	# `by_status` is the archetype -- platform constants -- and is seeded with every
	# state so an absent one reads 0 rather than going missing from the payload and
	# rendering as a dash.
	summary = {"total": 0, "stages": {}, "by_status": dict.fromkeys(ARCHETYPE_STATES, 0)}
	my_applications = 0
	unassigned = 0

	user = frappe.session.user
	for row in counts:
		count = row.get("COUNT(*)", 0)
		summary["total"] += count

		# If the bank has defined a stage, use it, else fallback to the archetype status
		stage_name = row.stage_label or row.status
		if stage_name not in summary["stages"]:
			summary["stages"][stage_name] = 0
		summary["stages"][stage_name] += count

		# Rows carrying a status from before the archetype refactor would otherwise
		# add a key nobody expects, so unknown values are counted in the total and
		# the stage breakdown but never invent a new archetype bucket.
		if row.status in summary["by_status"]:
			summary["by_status"][row.status] += count

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
@validate_request(GetAllLoansSchema)
@handle_api_errors
def get_all_loans(**kwargs):
	"""
	Retrieves a paginated list of all loan applications matching given filter parameters.
	"""
	frappe.has_permission("A2C Loan Application", "read", throw=True)

	status = kwargs.get("status")
	archetype = kwargs.get("archetype")
	loan_amount = kwargs.get("loan_amount")
	min_loan_amount = kwargs.get("min_loan_amount")
	max_loan_amount = kwargs.get("max_loan_amount")
	loan_type = kwargs.get("loan_type")
	region = kwargs.get("region")
	woreda = kwargs.get("woreda")
	kebele = kwargs.get("kebele")
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
	#
	# `name asc` tiebreaks. This query is paginated and sort_by allows loan_amount,
	# where ties are common -- without a deterministic second key the order between
	# equal rows is undefined, so paging can repeat one application and skip another.
	sort_by = kwargs.get("sort_by") or "creation"
	sort_order = "asc" if kwargs.get("sort_order") == "asc" else "desc"
	order_by = f"{sort_by} {sort_order}, name asc"

	offset = (page - 1) * page_size

	filters = {}

	if status:
		# Free-text, no allowed-list: stages are per-bank, so an unknown/foreign
		# stage_id just matches zero rows rather than raising, same as loan_type.
		valid_stages = parse_multi_value(status)
		if valid_stages:
			filters["stage_id"] = ["in", valid_stages]

	if archetype:
		allowed_archetypes = get_workflow_state_names("A2C Loan Application")
		valid_archetypes = parse_multi_value(archetype, allowed_archetypes)
		if valid_archetypes:
			filters["status"] = ["in", valid_archetypes]

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

	# One field per level of the hierarchy, each ANDed. A prefix match keeps this
	# usable from a plain text box while still hitting an index.
	for location_field, location_value in (("region", region), ("woreda", woreda), ("kebele", kebele)):
		if location_value:
			filters[location_field] = ("like", f"{location_value}%")

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
			"stage_id",
			# What the owning bank calls the current step. Every list renders this as
			# the badge; `status` is the four-state archetype behind it.
			"stage_label",
			"current_step as step",
			"lead_id",
			# Already searchable via or_filters below -- absent from this list the
			# applicant column had nothing to render and every row showed a dash.
			"first_name",
			"last_name",
			"loan_amount",
			"loan_type",
			"loan_product",
			"loan_product_name",
			"region",
			"woreda",
			"kebele",
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
		audit_event.bank = doc.bank
		audit_event.event_type = "Document Uploaded"
		audit_event.event_title = "Document Uploaded"
		# ignore_permissions=True: A2C Lead Audit Event is an append-only trail that
		# must record the upload regardless of whether the uploader holds DocPerm on
		# it -- a farmer uploading their own document does not. The write is a fixed,
		# server-composed record about an action already authorised above, so there
		# is no user-controlled permission decision being skipped here.
		audit_event.event_description = description
		audit_event.insert(ignore_permissions=True)

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

	description = "{0}\n{1}".format(
		_("Deleted document: {0}").format(file_name), _("Updated by: {0}").format(frappe.session.user)
	)
	audit_event = frappe.new_doc("A2C Loan Application Audit Event")
	audit_event.loan_application = application_id
	audit_event.bank = frappe.db.get_value("A2C Loan Application", application_id, "bank")
	audit_event.event_type = "Document Deleted"
	audit_event.event_title = "Document Deleted"
	# ignore_permissions=True: see the note in upload_supporting_documents -- the
	# audit trail records the deletion even when the caller has no DocPerm on it.
	audit_event.event_description = description
	audit_event.insert(ignore_permissions=True)

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

	# bank-scope-exempt — reading the product's own bank to stamp the application;
	# not a cross-bank query.
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
	loan_app.status = "Active"

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
				"first_name": loan_app.first_name,
				"last_name": loan_app.last_name,
				"loan_type": loan_app.loan_type,
				"loan_amount": loan_app.loan_amount,
				"current_step": loan_app.current_step,
			},
		},
		message="Loan application created successfully",
	)


class UpdateLoanStatusSchema(BaseModel):
	application_id: str = Field(..., min_length=1, max_length=140)
	status: str = Field(..., min_length=1, max_length=140)
	reason: str | None = Field(None, max_length=2000)

	# Removed strict status validation because status can now be a stage_id or label.
	# Validation will happen in the endpoint using resolve_bank_stage.


@frappe.whitelist(allow_guest=False, methods=["POST"])
@validate_request(UpdateLoanStatusSchema)
@handle_api_errors
@require_role([ADMIN_ROLE, BANK_ADMIN_ROLE, BANK_AGENT_ROLE, DEVELOPMENT_AGENT_ROLE, "System Manager"])
def update_loan_status(**kwargs):
	"""
	Updates the status of a loan application. Cannot update if current status is terminal.
	Accepts an archetype status ('Completed') or a bank-defined stage ID/label.
	"""
	application_id = kwargs.get("application_id")
	status_input = kwargs.get("status")
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

	from oan_a2c.a2c_marketplace.stages import resolve_bank_stage

	resolved = resolve_bank_stage(doc.bank, status_input)

	target_archetype = resolved["archetype_state"]

	# The stage is written onto the document BEFORE the transition, never with db_set
	# afterwards.
	#
	# apply_status_transition saves the document, and that save fires on_update ->
	# stats_cache.on_application_change, which buckets the dashboard's `stage_counts`
	# on `stage_label`. db_set bypasses doc_events entirely, so writing the stage
	# after the save left the counter pointing at the stage the application was in
	# *before* this call. Worse, a move between two stages of the same archetype (the
	# common case: every default pipeline stage is `In Transition`) never moved the
	# counter at all, because apply_status_transition short-circuits when the target
	# archetype equals the current one. The farmer submit path already ordered these
	# correctly; this brings the bank path in line with it.
	before_stage = doc.stage_label or doc.status
	stage_changed = doc.stage_id != resolved["stage_id"] or doc.stage_label != resolved["stage_label"]

	current_state = doc.get("workflow_state") or doc.get("status")
	status_changed = current_state != target_archetype

	# apply_workflow reloads the document from the db, so in-memory changes are lost.
	# We must db_set the stage fields before calling apply_status_transition.
	doc.db_set("stage_id", resolved["stage_id"])
	doc.db_set("stage_label", resolved["stage_label"])

	# Apply the status change through the A2C Loan Application Workflow. The workflow enforces
	# legal transitions and per-role gating, and submits the doc (docstatus 1) on
	# Approve/Reject (Completed). Illegal/unauthorised targets raise ValidationError.
	apply_status_transition(doc, target_archetype)

	if stage_changed and not status_changed:
		# Pure stage move inside one archetype: apply_status_transition did nothing, so
		# the stage fields set above are still unsaved.
		if doc.docstatus == 0:
			# Normal path -- the save persists the stage and fires the stats hook.
			doc.save()
		else:
			# A submitted document (Completed / Rejected) cannot be saved again, so the
			# stage is written directly and the cached counter is corrected by hand.
			doc.db_set("stage_id", resolved["stage_id"])
			doc.db_set("stage_label", resolved["stage_label"])
			from oan_a2c.a2c_marketplace.stats_cache import on_stage_moved

			on_stage_moved(doc.bank, before_stage, doc.stage_label or doc.status)

	# Insert Loan Application Audit Event
	description = _("Changed to {0} ({1})").format(resolved["stage_label"], target_archetype)
	if reason:
		description += f"\nReason: {reason}"
	description += f"\nUpdated by: {frappe.session.user}"

	audit_event = frappe.new_doc("A2C Loan Application Audit Event")
	audit_event.loan_application = application_id
	audit_event.bank = doc.bank
	audit_event.event_type = "Status Changed"
	audit_event.event_title = "Status Updated"
	audit_event.event_description = description
	audit_event.insert()

	return success_response(message=_("Loan status updated to {0}.").format(resolved["stage_label"]))


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
