import frappe
from frappe import _
from frappe.utils import cint, flt
from functools import wraps
import json

def success_response(data=None, message="Success", meta=None, pagination=None):
    res = {
        "status": "success",
        "message": message,
        "data": data,
        "meta": meta or {},
    }
    if pagination:
        res["pagination"] = pagination
    return res

def error_response(message, code="GENERIC_ERROR", details=None):
    return {
        "status": "error",
        "message": message,
        "code": code,
        "details": details or {},
    }

def handle_api_errors(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except frappe.PermissionError:
            frappe.local.message_log = []
            frappe.response.status_code = 403
            return error_response("Permission denied", "PERMISSION_DENIED")
        except frappe.DoesNotExistError:
            frappe.local.message_log = []
            frappe.response.status_code = 404
            return error_response("Resource not found", "NOT_FOUND")
        except frappe.ValidationError as e:
            frappe.local.message_log = []
            frappe.response.status_code = 400
            return error_response(str(e), "VALIDATION_ERROR")
        except Exception as e:
            frappe.local.message_log = []
            frappe.log_error(frappe.get_traceback(), f"API Error in {func.__name__}")
            frappe.response.status_code = 500
            return error_response("An unexpected error occurred", "INTERNAL_ERROR")
    return wrapper

def _get_app(application_id):
    if not frappe.db.exists("A2C Loan Application", application_id):
        frappe.throw(_("Loan Application {0} not found").format(application_id), frappe.DoesNotExistError)
    return frappe.get_doc("A2C Loan Application", application_id)

def validate_lead(lead_id):
    if not lead_id:
        frappe.local.response["http_status_code"] = 400
        return {
            "error": {
                "code": "LEAD_ID_REQUIRED",
                "message": "lead_id is required"
            }
        }
    if not frappe.db.exists("A2C Lead", lead_id):
        frappe.local.response["http_status_code"] = 404
        return {
            "error": {
                "code": "LEAD_NOT_FOUND",
                "message": f"A2C Lead {lead_id} not found"
            }
        }
    return None

@frappe.whitelist(allow_guest=False)
@handle_api_errors
def get_basic_profile(lead_id=None, include_consent_data=None):
    """
    Retrieves the basic profile information of a farmer associated with a lead.
    """
    if not lead_id:
        frappe.local.response["http_status_code"] = 400
        return {
            "error": {
                "code": "LEAD_ID_REQUIRED",
                "message": "lead_id is required"
            }
        }
    
    err = validate_lead(lead_id)
    if err:
        return err

    frappe.has_permission("A2C Lead", "read", doc=lead_id, throw=True)

    lead_doc = frappe.get_doc("A2C Lead", lead_id)
    if not lead_doc.farmer_profile:
        frappe.throw(_("Farmer Profile not found for this lead"), frappe.ValidationError)
    
    frappe.has_permission("A2C Farmer Profile", "read", doc=lead_doc.farmer_profile, throw=True)
    doc = frappe.get_doc("A2C Farmer Profile", lead_doc.farmer_profile)
    
    data = {
        "first_name": doc.first_name,
        "last_name": doc.last_name,
        "phone_number": doc.phone_number,
        "email": doc.email,
        "location": doc.location
    }

    if cint(include_consent_data):
        res_fields = {}
        requested_data_fields = []
        if doc.consent_id:
            frappe.has_permission("A2C Consent Request", "read", doc=doc.consent_id, throw=True)
            res_fields = frappe.db.get_value(
                "A2C Consent Request", 
                doc.consent_id, 
                ["websub_delivered_at", "consent_type", "purpose", "validity_from", "validity_to"], 
                as_dict=True
            ) or {}
            for key in ["websub_delivered_at", "validity_from", "validity_to"]:
                if res_fields.get(key):
                    res_fields[key] = str(res_fields[key])

            requested_data_fields = frappe.get_all(
                "A2C Consent Data",
                filters={"parent": doc.consent_id},
                fields=["field_name", "field_value"]
            )

        data.update(res_fields)
        data["requested_data_fields"] = requested_data_fields

    return success_response(data, message="Basic profile retrieved successfully")

@frappe.whitelist(allow_guest=False, methods=["POST"])
@handle_api_errors
def update_basic_profile(lead_id=None, email=None, location=None):
    """
    Updates the email and location details for a lead's farmer profile.
    """
    if not lead_id:
        frappe.local.response["http_status_code"] = 400
        return {
            "error": {
                "code": "LEAD_ID_REQUIRED",
                "message": "lead_id is required"
            }
        }
    
    err = validate_lead(lead_id)
    if err:
        return err
    
    frappe.has_permission("A2C Lead", "write", doc=lead_id, throw=True)
    
    lead_doc = frappe.get_doc("A2C Lead", lead_id)
    if not lead_doc.farmer_profile:
        frappe.throw(_("Farmer Profile not found for this lead"), frappe.ValidationError)
        
    frappe.has_permission("A2C Farmer Profile", "write", doc=lead_doc.farmer_profile, throw=True)
    farmer_doc = frappe.get_doc("A2C Farmer Profile", lead_doc.farmer_profile)
    
    changed = False
    updates = {
        "email": email,
        "location": location
    }
    
    if email:
        from frappe.utils import validate_email_address
        if not validate_email_address(email):
            frappe.throw(_("Invalid email address format"), frappe.ValidationError)

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
@handle_api_errors
def get_full_profile(application_id=None):
    """
    Retrieves the full profile details of a loan application.
    """
    if not application_id:
        frappe.throw(_("application_id is required"), frappe.MandatoryError)

    frappe.has_permission("A2C Loan Application", "read", doc=application_id, throw=True)
    doc = _get_app(application_id)
    farmer_profile = frappe.db.get_value("A2C Lead", doc.lead_id, "farmer_profile")

    data = {
        "application_id": doc.name,
        "lead_id": doc.lead_id,
        "farmer_profile": farmer_profile,
        "first_name": doc.first_name,
        "last_name": doc.last_name,
        "location": doc.location,
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
        "farmland_size_hectares": float(doc.farmland_size_hectares) if doc.farmland_size_hectares else 0.0,
        "land_ownership_status": doc.land_ownership_status,
        "soil_fertility_minerals": doc.soil_fertility_minerals,
        "moisture_levels": doc.moisture_levels,
        "certification_id": doc.certification_id,
        "certification_photo_url": doc.certification_photo_url
    }
    
    return success_response(data, message="Full profile retrieved successfully")

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

    return success_response(summary, message="Loan summary retrieved successfully")

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
    
    return success_response({"statuses": statuses}, message="Loan metadata retrieved successfully")

@frappe.whitelist(allow_guest=False)
@handle_api_errors
def get_all_loans(status=None, loan_amount=None, min_loan_amount=None, max_loan_amount=None, loan_type=None, location=None, phone_number=None, from_date=None, to_date=None, page=1, page_size=20, lead_id=None, search_query=None):
    """
    Retrieves a paginated list of all loan applications matching given filter parameters.
    """
    frappe.has_permission("A2C Loan Application", "read", throw=True)

    page = cint(page) or 1
    page_size = cint(page_size) or 20
    page_size = max(1, min(page_size, 100))
    offset = (page - 1) * page_size

    filters = {}

    if status:
        allowed_statuses = {"Draft", "Processing", "Approved", "Rejected"}
        valid_statuses = [s.strip() for s in status.split(",") if s.strip() in allowed_statuses]
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
        filters['loan_type'] = loan_type

    if location:
        filters['location'] = ("like", f"%{location}%")

    if phone_number:
        filters['phone_number'] = ("like", f"%{phone_number}%")

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
@handle_api_errors
def upload_supporting_documents(application_id=None):
    """
    Uploads private supporting document files for a specific loan application.
    """
    if not application_id:
        frappe.throw(_("application_id is required"), frappe.MandatoryError)

    frappe.has_permission("A2C Loan Application", "write", doc=application_id, throw=True)
    doc = _get_app(application_id)

    if not frappe.request.files:
        frappe.throw(_("No files found in request"), frappe.ValidationError)
        
    uploaded_files = []
    ALLOWED_EXTENSIONS = ('.pdf', '.png', '.jpg', '.jpeg')
    MAX_FILE_SIZE = 5 * 1024 * 1024

    for key, file_storage in frappe.request.files.items():
        filename = file_storage.filename.lower()
        if not filename.endswith(ALLOWED_EXTENSIONS):
            frappe.throw(_("Invalid file type for {0}. Only PDF, PNG, and JPG are allowed.").format(filename), frappe.ValidationError)
            
        content = file_storage.read()
        if len(content) > MAX_FILE_SIZE:
            frappe.throw(_("File {0} exceeds the 5MB size limit.").format(filename), frappe.ValidationError)
            
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
    return success_response(uploaded_files, message="Supporting documents uploaded successfully")

@frappe.whitelist(allow_guest=False)
@handle_api_errors
def get_supporting_documents(application_id=None):
    """
    Retrieves list information for all files uploaded under a loan application.
    """
    if not application_id:
        frappe.throw(_("application_id is required"), frappe.MandatoryError)

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

    return success_response(files, message="Supporting documents retrieved successfully")

@frappe.whitelist(allow_guest=False)
@handle_api_errors
def download_supporting_document(file_id=None, view=None):
    """
    Downloads or streams the content of an uploaded private supporting document.
    """
    if not file_id:
        frappe.throw(_("file_id is required"), frappe.MandatoryError)

    if not frappe.db.exists("File", file_id):
        frappe.throw(_("File not found"), frappe.DoesNotExistError)

    file_doc = frappe.get_doc("File", file_id)

    if file_doc.attached_to_doctype and file_doc.attached_to_name:
        frappe.has_permission(file_doc.attached_to_doctype, "read", doc=file_doc.attached_to_name, throw=True)
    else:
        frappe.has_permission("File", "read", doc=file_doc, throw=True)

    frappe.local.response.filename = file_doc.file_name
    frappe.local.response.filecontent = file_doc.get_content()
    frappe.local.response.type = "download"
    if cint(view):
        frappe.local.response.display_content_as = "inline"

@frappe.whitelist(allow_guest=False, methods=["POST"])
@handle_api_errors
def delete_supporting_document(application_id=None, file_id=None):
    """
    Deletes an attached supporting document from a loan application.
    """
    if not application_id or not file_id:
        frappe.throw(_("application_id and file_id are required"), frappe.MandatoryError)
        
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
@handle_api_errors
def create_loan_application(lead_id=None):
    """
    Creates an A2C Loan Application by copying data from the Lead's linked Farmer Profile and Credit Information.
    """
    if not lead_id:
        frappe.local.response["http_status_code"] = 400
        return {
            "error": {
                "code": "LEAD_ID_REQUIRED",
                "message": "lead_id is required"
            }
        }

    err = validate_lead(lead_id)
    if err:
        return err

    frappe.has_permission("A2C Loan Application", "create", throw=True)
    frappe.has_permission("A2C Lead", "read", doc=lead_id, throw=True)

    existing = frappe.get_list("A2C Loan Application", filters={"lead_id": lead_id}, fields=["name"], limit=1, ignore_permissions=False)
    if existing:
        frappe.throw(_("Loan application already exists for this lead"), frappe.ValidationError)

    lead_doc = frappe.get_doc("A2C Lead", lead_id)
    
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
    
    return success_response(
        data={"application_id": loan_app.name},
        message="Loan application created successfully"
    )

@frappe.whitelist(allow_guest=False, methods=["POST"])
@handle_api_errors
def update_loan_status(application_id=None, status=None):
    """
    Updates the status of a loan application. Cannot update if current status is Rejected or Approved.
    """
    if not application_id or not status:
        frappe.throw(_("application_id and status are required"), frappe.MandatoryError)

    frappe.has_permission("A2C Loan Application", "write", doc=application_id, throw=True)
    doc = _get_app(application_id)
    
    if doc.status in ["Rejected", "Approved"]:
        frappe.throw(_("Cannot change status. Loan application is already {0}").format(doc.status), frappe.ValidationError)

    doc.status = status
    doc.save(ignore_permissions=False)
    frappe.db.commit()

    return success_response(message=f"Loan application status updated to {status}")

@frappe.whitelist(allow_guest=False, methods=["POST"])
@handle_api_errors
def update_loan_step(application_id=None, step=None):
    """
    Updates the current step of a loan application.
    """
    if not application_id or step is None:
        frappe.throw(_("application_id and step are required"), frappe.MandatoryError)

    frappe.has_permission("A2C Loan Application", "write", doc=application_id, throw=True)
    doc = _get_app(application_id)
    
    step_val = cint(step)
    if step_val < 1:
        frappe.throw(_("Step must be a positive integer"), frappe.ValidationError)

    doc.current_step = step_val
    doc.save(ignore_permissions=False)
    frappe.db.commit()

    return success_response(message=f"Loan application step updated to {step_val}")
