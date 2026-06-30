import frappe
from frappe import _
from frappe.utils import cint, flt
from functools import wraps
import json

from oan_a2c.api.utils import success_response, handle_api_errors, parse_multi_value, validate_request, SafeDate, SafeEmail, apply_status_transition
from pydantic import BaseModel, Field, field_validator
from typing import Optional, Literal

class GetBasicProfileSchema(BaseModel):
    lead_id: str = Field(..., min_length=1)
    include_consent_data: Optional[int] = None

class UpdateBasicProfileSchema(BaseModel):
    lead_id: str = Field(..., min_length=1)
    email: SafeEmail = None
    location: Optional[str] = None

class LoanApplicationIDSchema(BaseModel):
    application_id: str = Field(..., min_length=1)

class LeadIDSchema(BaseModel):
    lead_id: str = Field(..., min_length=1)

class GetAllLoansSchema(BaseModel):
    status: Optional[str] = None
    loan_amount: Optional[float] = None
    min_loan_amount: Optional[float] = None
    max_loan_amount: Optional[float] = None
    loan_type: Optional[str] = None
    location: Optional[str] = None
    phone_number: Optional[str] = None
    loan_officer: Optional[str] = None
    from_date: SafeDate = None
    to_date: SafeDate = None
    page: Optional[int] = Field(None, ge=1)
    page_size: Optional[int] = Field(None, ge=1, le=100)
    lead_id: Optional[str] = None
    search_query: Optional[str] = None

class DownloadSupportingDocumentSchema(BaseModel):
    file_id: str = Field(..., min_length=1)
    view: Optional[int] = None

class DeleteSupportingDocumentSchema(BaseModel):
    application_id: str = Field(..., min_length=1)
    file_id: str = Field(..., min_length=1)

class UpdateLoanStatusSchema(BaseModel):
    application_id: str = Field(..., min_length=1)
    status: Literal["Draft", "Processing", "Approved", "Rejected"]

class UpdateLoanStepSchema(BaseModel):
    application_id: str = Field(..., min_length=1)
    step: int = Field(..., ge=1, le=4)


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
    res_fields = frappe.db.get_value(
        "A2C Consent Request",
        consent_id,
        ["websub_delivered_at", "consent_type", "purpose", "validity_from", "validity_to"],
        as_dict=True
    ) or {}
    for key in ["websub_delivered_at", "validity_from", "validity_to"]:
        if res_fields.get(key):
            res_fields[key] = str(res_fields[key])

    requested_data_fields = frappe.get_all(
        "A2C Consent Data",
        filters={"parent": consent_id},
        fields=["field_name", "field_value"]
    )
    res_fields["requested_data_fields"] = requested_data_fields
    return res_fields

@frappe.whitelist(allow_guest=False)
@validate_request(GetBasicProfileSchema)
@handle_api_errors
def get_basic_profile(lead_id=None, include_consent_data=None):
    """
    Retrieves the basic profile information of a farmer associated with a lead.
    """
    frappe.has_permission("A2C Lead", "read", doc=lead_id, throw=True)
    lead_doc = _get_lead(lead_id)

    profile_name = lead_doc.farmer_profile
    consent_id = frappe.db.get_value("A2C Consent Request", {"lead": lead_id}, "name", order_by="creation desc")

    if not profile_name and not consent_id:
        frappe.throw(_("Farmer Profile not found for this lead"), frappe.ValidationError)

    data = {
        "farmer_profile_created": bool(profile_name)
    }

    if profile_name:
        frappe.has_permission("A2C Farmer Profile", "read", doc=profile_name, throw=True)
        profile = frappe.get_doc("A2C Farmer Profile", profile_name)
        data.update({
            "first_name": profile.first_name,
            "last_name": profile.last_name,
            "phone_number": profile.phone_number,
            "email": profile.email,
            "email": profile.email,
            "region": profile.region,
            "woreda": profile.woreda,
            "kebele": profile.kebele
        })
        consent_id = profile.consent_id or consent_id

    if consent_id:
        consent_doc_data = frappe.db.get_value("A2C Consent Request", consent_id, ["status", "otp_verified_at"], as_dict=True) or {}
        data["consent_request"] = {
            "name": consent_id,
            "status": consent_doc_data.get("status"),
            "otp_verified": bool(consent_doc_data.get("otp_verified_at"))
        }
        if include_consent_data:
            data.update(_get_consent_details(consent_id))

    return success_response(data=data, message="Basic profile retrieved successfully")

@frappe.whitelist(allow_guest=False, methods=["POST"])
@validate_request(UpdateBasicProfileSchema)
@handle_api_errors
def update_basic_profile(lead_id=None, email=None, location=None):
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
    updates = {
        "email": email,
        "location": location
    }
    
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
        frappe.db.commit()
        
    return success_response(
        data={
            "email": farmer_doc.email,
            "location": farmer_doc.location
        },
        message="Basic profile updated successfully"
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
        "phone_number": doc.phone_number,
        "farmer_id": doc.farmer_id,
        "consent_id": doc.consent_id,
        "loan_type": doc.loan_type,
        "loan_amount": float(doc.loan_amount) if doc.loan_amount else 0.0,
        "loan_reason": doc.loan_reason,
        "status": doc.status,
        "current_step": cint(doc.current_step),
        "loan_officer": doc.loan_officer,
        "creation": str(doc.creation),
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
        "total_farmland_size_as_landowner": float(doc.total_farmland_size_as_landowner) if doc.total_farmland_size_as_landowner else 0.0,
        "total_farmland_size_as_crop_sharing": float(doc.total_farmland_size_as_crop_sharing) if doc.total_farmland_size_as_crop_sharing else 0.0,
        "total_farmland_size_as_rented": float(doc.total_farmland_size_as_rented) if doc.total_farmland_size_as_rented else 0.0,
        "farmland_size_hectares": doc.farmland_size_hectares,
        "land_ownership_status": doc.land_ownership_status,
        "soil_fertility_minerals": doc.soil_fertility_minerals,
        "moisture_levels": doc.moisture_levels,
        "certification_id": doc.certification_id,
        "certification_photo_url": doc.certification_photo_url
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
        "A2C Loan Application",
        fields=fields,
        group_by=group_by,
        ignore_permissions=False
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

    summary["tab_counts"] = {
        "all": summary["total"]
    }
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
    
    statuses = [s for s in status_field.options.split("\n") if s] if status_field and status_field.options else []
    
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

    offset = (page - 1) * page_size

    filters = {}

    if status:
        allowed_statuses = ("Draft", "Processing", "Approved", "Rejected")
        valid_statuses = parse_multi_value(status, allowed_statuses)
        if valid_statuses:
            filters['status'] = ["in", valid_statuses]

    if lead_id:
        filters['lead_id'] = lead_id

    if min_loan_amount is not None and max_loan_amount is not None:
        filters['loan_amount'] = ("between", [flt(min_loan_amount), flt(max_loan_amount)])
    elif min_loan_amount is not None:
        filters['loan_amount'] = (">=", flt(min_loan_amount))
    elif max_loan_amount is not None:
        filters['loan_amount'] = ("<=", flt(max_loan_amount))
    elif loan_amount is not None:
        filters['loan_amount'] = flt(loan_amount)

    if loan_type:
        # loan_type on A2C Loan Application is a free-text Data field (no Select options),
        # so accept the provided value(s) as-is. Single value or comma-separated multi-value.
        valid_loan_types = parse_multi_value(loan_type)
        if valid_loan_types:
            filters['loan_type'] = ["in", valid_loan_types]

    if location:
        filters['location'] = ("like", f"%{location}%")

    if phone_number:
        filters['phone_number'] = ("like", f"%{phone_number}%")

    # Filter by assigned Loan Officer (User). Single user, comma-separated users, or the literal
    # "unassigned" for loans with no officer (matching the unassigned tab in get_loan_summary).
    # Not allowlist-validated; an unknown user simply yields no matches.
    if loan_officer:
        officers = [o.strip() for o in str(loan_officer).split(",") if o.strip()]
        if any(o.lower() == "unassigned" for o in officers):
            named = [o for o in officers if o.lower() != "unassigned"]
            filters['loan_officer'] = ["in", (named + [""]) if named else ["", None]]
        elif officers:
            filters['loan_officer'] = ["in", officers]

    if from_date and to_date:
        filters['creation'] = ("between", [from_date, f"{to_date} 23:59:59"])
    elif from_date:
        filters['creation'] = (">=", from_date)
    elif to_date:
        filters['creation'] = ("<=", f"{to_date} 23:59:59")

    or_filters = []
    if search_query:
        search_query_param = f"%{search_query}%"
        or_filters.append(["name", "like", search_query_param])
        or_filters.append(["phone_number", "like", search_query_param])
        or_filters.append(["farmer_id", "like", search_query_param])
        or_filters.append(["first_name", "like", search_query_param])
        or_filters.append(["last_name", "like", search_query_param])

    if or_filters:
        count_res = frappe.get_list(
            "A2C Loan Application",
            filters=filters,
            or_filters=or_filters,
            fields=[{"COUNT": "*"}],
            ignore_permissions=False
        )
        total_records = count_res[0].get("COUNT(*)") if count_res else 0
    else:
        total_records = frappe.db.count("A2C Loan Application", filters=filters)

    records = frappe.get_list(
        "A2C Loan Application",
        filters=filters,
        or_filters=or_filters or None,
        fields=["name as application_id", "status", "current_step as step", "lead_id", "loan_amount", "loan_type", "location", "phone_number", "creation"],
        order_by="creation DESC",
        limit_start=offset,
        page_length=page_size,
        ignore_permissions=False
    )

    for r in records:
        r["loan_amount"] = float(r["loan_amount"]) if r.get("loan_amount") else 0.0
        r["step"] = cint(r.get("step"))
        r["creation"] = str(r["creation"])

    total_pages = -(-total_records // page_size)
    has_next = offset + page_size < total_records

    pagination = {
        "page": page,
        "limit": page_size,
        "total": total_records,
        "total_pages": total_pages,
        "has_next": has_next
    }

    return success_response(
        data=records,
        message="Loan applications retrieved successfully",
        pagination=pagination
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
        frappe.throw(_("Maximum {0} files can be uploaded at a time.").format(MAX_FILE_COUNT), frappe.ValidationError)

    uploaded_files = []
    ALLOWED_EXTENSIONS = ('.pdf', '.png', '.jpg', '.jpeg')
    MAX_FILE_SIZE = 5 * 1024 * 1024

    for key, file_storage in frappe.request.files.items():
        filename = file_storage.filename.lower()
        if not filename.endswith(ALLOWED_EXTENSIONS):
            frappe.throw(_("Invalid file type for {0}. Only PDF, PNG, and JPG are allowed.").format(filename), frappe.ValidationError)
            
        file_storage.seek(0, 2)
        file_size = file_storage.tell()
        file_storage.seek(0)
        if file_size > MAX_FILE_SIZE:
            frappe.throw(_("File {0} exceeds the 5MB size limit.").format(filename), frappe.ValidationError)
            
        content = file_storage.read()
        
        # Content sniffing (magic bytes validation) to prevent extension spoofing
        content_prefix = content[:8]
        is_pdf = content_prefix.startswith(b'%PDF')
        is_png = content_prefix.startswith(b'\x89PNG\r\n\x1a\n')
        is_jpeg = content_prefix.startswith(b'\xff\xd8\xff')
        
        if filename.endswith('.pdf') and not is_pdf:
            frappe.throw(_("File {0} is not a valid PDF file.").format(file_storage.filename), frappe.ValidationError)
        elif filename.endswith(('.jpg', '.jpeg')) and not is_jpeg:
            frappe.throw(_("File {0} is not a valid JPEG/JPG image.").format(file_storage.filename), frappe.ValidationError)
        elif filename.endswith('.png') and not is_png:
            frappe.throw(_("File {0} is not a valid PNG image.").format(file_storage.filename), frappe.ValidationError)
        file_doc = frappe.get_doc({
            "doctype": "File",
            "file_name": file_storage.filename,
            "content": content,
            "attached_to_doctype": "A2C Loan Application",
            "attached_to_name": doc.name,
            "is_private": 1
        })
        file_doc.insert(ignore_permissions=False)
        uploaded_files.append({
            "name": file_doc.name,
            "file_url": file_doc.file_url,
            "file_name": file_doc.file_name
        })

    frappe.db.commit()
    return success_response(data=uploaded_files, message="Supporting documents uploaded successfully")

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
        filters={
            "attached_to_doctype": "A2C Loan Application",
            "attached_to_name": application_id
        },
        fields=["name", "file_name", "file_url", "creation"],
        ignore_permissions=False
    )
    
    for f in files:
        f["creation"] = str(f["creation"])

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
            frappe.has_permission(file_doc.attached_to_doctype, "read", doc=file_doc.attached_to_name, throw=True)
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
    
    if not frappe.db.exists("File", {
        "name": file_id,
        "attached_to_doctype": "A2C Loan Application",
        "attached_to_name": application_id
    }):
        frappe.throw(_("File not found or not attached to this application"), frappe.DoesNotExistError)
        
    frappe.delete_doc("File", file_id, ignore_permissions=False)
    frappe.db.commit()
    
    return success_response(message="File deleted successfully")

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
    frappe.db.sql("SELECT name FROM `tabA2C Loan Application` WHERE lead_id = %s FOR UPDATE", (lead_id,))
    existing = frappe.get_list("A2C Loan Application", filters={"lead_id": lead_id}, fields=["name"], limit=1, ignore_permissions=False)
    if existing:
        frappe.throw(_("Loan application already exists for this lead"), frappe.ValidationError)
    
    farmer_profile_name = lead_doc.get("farmer_profile")
    if not farmer_profile_name:
        frappe.throw(_("No Farmer Profile found for this lead. Webhook consent might not be completed."), frappe.ValidationError)
    
    frappe.has_permission("A2C Farmer Profile", "read", doc=farmer_profile_name, throw=True)
    farmer_profile = frappe.get_doc("A2C Farmer Profile", farmer_profile_name)

    credit_infos = frappe.get_list(
        "A2C Credit Information", 
        filters={"lead": lead_id}, 
        fields=["loan_type", "loan_amount", "purpose_message"],
        order_by="creation desc",
        limit=1,
        ignore_permissions=False
    )
    
    if not credit_infos:
        frappe.throw(_("Credit Information is missing for this lead. A loan application requires a valid loan amount."), frappe.ValidationError)

    loan_app = frappe.new_doc("A2C Loan Application")
    loan_app.lead_id = lead_id
    loan_app.farmer_profile = farmer_profile.name
    
    loan_app.first_name = farmer_profile.first_name
    loan_app.last_name = farmer_profile.last_name
    loan_app.location = farmer_profile.location
    loan_app.phone_number = farmer_profile.phone_number 
    loan_app.farmer_id = farmer_profile.farmer_id
    loan_app.consent_id = farmer_profile.consent_id

    # Populate farmer profile details into loan application
    loan_app.date_of_birth = farmer_profile.date_of_birth
    loan_app.gender = farmer_profile.gender
    loan_app.marital_status = farmer_profile.marital_status
    loan_app.size_of_family = farmer_profile.size_of_family
    loan_app.number_of_children = farmer_profile.number_of_children
    loan_app.no_of_females_family = farmer_profile.no_of_females_family
    loan_app.no_of_males_family = farmer_profile.no_of_males_family
    loan_app.source_of_income = farmer_profile.source_of_income
    loan_app.education_level = farmer_profile.education_level
    loan_app.family_member_owns_land_independently = farmer_profile.family_member_owns_land_independently
    loan_app.total_farmland_size_as_landowner = farmer_profile.total_farmland_size_as_landowner
    loan_app.total_farmland_size_as_crop_sharing = farmer_profile.total_farmland_size_as_crop_sharing
    loan_app.total_farmland_size_as_rented = farmer_profile.total_farmland_size_as_rented
    loan_app.farmland_size_hectares = farmer_profile.farmland_size_hectares
    loan_app.land_ownership_status = farmer_profile.land_ownership_status
    loan_app.soil_fertility_minerals = farmer_profile.soil_fertility_minerals
    loan_app.moisture_levels = farmer_profile.moisture_levels
    loan_app.certification_id = farmer_profile.certification_id
    loan_app.certification_photo_url = farmer_profile.certification_photo_url
    
    loan_app.loan_type = credit_infos[0].loan_type
    loan_app.loan_amount = flt(credit_infos[0].loan_amount)
    loan_app.loan_reason = credit_infos[0].purpose_message
    loan_app.status = "Draft"
    
    loan_app.insert(ignore_permissions=False)
    frappe.db.commit()

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
            }
        },
        message="Loan application created successfully"
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

    frappe.has_permission("A2C Loan Application", "write", doc=application_id, throw=True)
    doc = _get_app(application_id)

    # Apply the status change through the A2C Loan Application Workflow. The workflow enforces
    # legal transitions and per-role gating, and submits the doc (docstatus 1) on
    # Approve/Reject. Illegal/unauthorised targets raise ValidationError.
    apply_status_transition(doc, status)
    frappe.db.commit()

    return success_response(message=f"Loan application status updated to {status}")

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
    frappe.db.commit()

    return success_response(
        data={
            "application_id": doc.name,
            "current_step": doc.current_step
        },
        message=f"Loan application step updated to {doc.current_step}"
    )
