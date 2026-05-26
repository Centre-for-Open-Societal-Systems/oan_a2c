# pyright: reportMissingImports=false
import frappe
from frappe.utils import now_datetime
import json


# ─── Helpers ────────────────────────────────────────────────────────────────

def success(data):
    return {"status": "success", "data": data}

def error(code, message):
    return {"status": "error", "error_code": code, "message": message}

def _get_app(application_id, editable=True):
    if not application_id:
        return None, error("MISSING_APP_ID", "application_id is required.")
    if not frappe.db.exists("Loan Application", application_id):
        return None, error("NOT_FOUND", f"Application {application_id} not found.")
    doc = frappe.get_doc("Loan Application", application_id)
    if editable and doc.status not in ["Draft", "In Progress"]:
        return None, error("NOT_EDITABLE", "This application is already submitted.")
    return doc, None

def _parse_body(kwargs):
    if not kwargs or list(kwargs.keys()) == ["cmd"]:
        # 1. Fallback for form-data (e.g. file uploads)
        try:
            if hasattr(frappe.local, "request") and hasattr(frappe.local.request, "form") and frappe.local.request.form:
                for k, v in frappe.local.request.form.items():
                    kwargs[k] = v
        except Exception:
            pass

        # 2. Fallback for JSON body (when Content-Type header is missing/altered)
        if len(kwargs) <= 1:
            try:
                raw_data = frappe.local.request.get_data()
                if raw_data:
                    body_params = json.loads(raw_data.decode("utf-8"))
                    if isinstance(body_params, dict):
                        kwargs.update(body_params)
            except Exception:
                pass
    return kwargs


# ─── API 1: Loan Details ─────────────────────────────────────────────────────

@frappe.whitelist(allow_guest=False)
def loan_details(**kwargs):
    kwargs = _parse_body(kwargs)
    action = kwargs.get("action", "save")

    if action == "save":
        application_id = kwargs.get("application_id")
        fields = {
            "loan_type":                         kwargs.get("loan_type"),
            "purpose_of_loan":                   kwargs.get("purpose_of_loan"),
            "requested_amount":                   kwargs.get("requested_loan_amount"),
            "loan_duration_months":              kwargs.get("loan_duration_months"),
            "nearest_branch":                    kwargs.get("nearest_branch"),
            "primary_crop":                      kwargs.get("primary_crop"),
            "crop_variety":                      kwargs.get("crop_variety"),
            "address":                           kwargs.get("address"),
            "quantity_requested_kg":             kwargs.get("quantity_requested_kg"),
            "unit_price":                        kwargs.get("unit_price"),
            "total_seed_cost":                   kwargs.get("total_seed_cost"),
            "land_size_ha":                      kwargs.get("land_size_hectares"),
            "expected_yield":                    kwargs.get("expected_yield"),
            "expected_harvest_date":             kwargs.get("expected_harvest_date"),
            "fertilizer_used":                   kwargs.get("fertilizer_used"),
            "other_farming_activities":          kwargs.get("other_farming_activities"),
            "farmer_group":                      kwargs.get("farmer_group"),
            "animal_reared":                     kwargs.get("animal_reared"),
            "farm_equipment":                    kwargs.get("farm_equipment"),
            "farm_size_hectares":                kwargs.get("farm_size_hectares"),
            "region":                            kwargs.get("region"),
            "zone":                              kwargs.get("zone"),
            "woreda":                            kwargs.get("woreda"),
            "kebele":                            kwargs.get("kebele"),
            "harvest_aggregator_type":           kwargs.get("harvest_aggregator_type"),
            "name_of_cooperative":               kwargs.get("name_of_cooperative"),
            "dap_quantity_kg":                   kwargs.get("dap_quantity_kg"),
            "urea_quantity_kg":                  kwargs.get("urea_quantity_kg"),
            "unit_price_per_fertilizer_type":    kwargs.get("unit_price_per_fertilizer_type"),
            "total_fertilizer_cost":             kwargs.get("total_fertilizer_cost"),
            "type_of_agrochemical":              kwargs.get("type_of_agrochemical"),
            "agrochemical_quantity_liters":       kwargs.get("agrochemical_quantity_liters"),
            "unit_price_agrochemical":           kwargs.get("unit_price_agrochemical"),
            "total_agrochemical_cost":           kwargs.get("total_agrochemical_cost"),
            "crop_protection_type":              kwargs.get("crop_protection_type"),
            "crop_protection_quantity":          kwargs.get("crop_protection_quantity"),
            "crop_protection_unit_price":        kwargs.get("crop_protection_unit_price"),
            "total_crop_protection_cost":        kwargs.get("total_crop_protection_cost"),
            "upfront_contribution_male_percent": kwargs.get("upfront_contribution_male_percent"),
            "upfront_contribution_female_percent": kwargs.get("upfront_contribution_female_percent"),
            "crop_insurance_premium_percent":    kwargs.get("crop_insurance_premium_percent"),
        }

        try:
            if not application_id:
                doc = frappe.get_doc({
                    "doctype": "Loan Application",
                    "status": "Draft",
                    "current_step": 1,
                    "loan_officer": frappe.session.user,
                    **{k: v for k, v in fields.items() if v is not None},
                })
                doc.insert(ignore_permissions=True)
                application_id = doc.name
            else:
                doc, err = _get_app(application_id)
                if err:
                    return err
                for k, v in fields.items():
                    if v is not None and hasattr(doc, k):
                        setattr(doc, k, v)
                doc.current_step = max(int(getattr(doc, "current_step", 0)), 1)
                doc.save(ignore_permissions=True)

            return success({"application_id": doc.name, "current_step": 1})
        except Exception as e:
            return error("SAVE_FAILED", str(e))

    return error("INVALID_ACTION", f"Unknown action: {action}")


# ─── API 2: Bank Details ─────────────────────────────────────────────────────

@frappe.whitelist(allow_guest=False)
def bank_details(**kwargs):
    kwargs = _parse_body(kwargs)
    action = kwargs.get("action", "save")

    if action == "save":
        application_id = kwargs.get("application_id")
        doc, err = _get_app(application_id)
        if err:
            return err

        total_amount = kwargs.get("total_amount_borrowing")
        tax_id = kwargs.get("tax_id")
        try:
            total_amount_float = float(total_amount) if total_amount else 0
        except Exception:
            total_amount_float = 0

        if total_amount_float > 100000 and not tax_id:
            return error("TAX_ID_REQUIRED", "Tax ID is required for loan amounts above ETB 1,00,000.")

        fields = {
            "bank_account_name":   kwargs.get("bank_account_name"),
            "bank_account_no":     kwargs.get("bank_account_number"),
            "bank_name":           kwargs.get("bank_name"),
            "bank_swift_ifsc_code": kwargs.get("bank_swift_ifsc_code"),
            "mobile_account_name": kwargs.get("mobile_account_name"),
            "mobile_payments_number": kwargs.get("mobile_payments_number"),
            "total_amount_borrowing": total_amount,
            "tax_id":              tax_id,
        }
        try:
            for k, v in fields.items():
                if v is not None and hasattr(doc, k):
                    setattr(doc, k, v)
            doc.current_step = max(getattr(doc, "current_step", 1), 2)
            doc.save(ignore_permissions=True)
            return success({"application_id": doc.name, "current_step": 2})
        except Exception as e:
            return error("SAVE_FAILED", str(e))

    return error("INVALID_ACTION", f"Unknown action: {action}")


# ─── API 3: Supporting Documents ─────────────────────────────────────────────

@frappe.whitelist(allow_guest=False)
def supporting_documents(**kwargs):
    kwargs = _parse_body(kwargs)
    action = kwargs.get("action", "list")

    if action == "upload":
        application_id = kwargs.get("application_id")
        doc, err = _get_app(application_id)
        if err:
            return err

        document_type = kwargs.get("document_type")
        marriage_status = kwargs.get("marriage_status")
        acknowledge_discrepancy = kwargs.get("acknowledge_discrepancy", False)

        allowed_types = ["Marriage Certificate", "Identity Document", "Land Ownership Proof"]
        if document_type not in allowed_types:
            return error("INVALID_DOC_TYPE", f"document_type must be one of: {allowed_types}")

        uploaded_file = frappe.request.files.get("file")
        if not uploaded_file:
            return error("NO_FILE", "No file was uploaded.")

        try:
            file_doc = frappe.get_doc({
                "doctype": "File",
                "file_name": uploaded_file.filename,
                "attached_to_doctype": "Loan Application",
                "attached_to_name": application_id,
                "attached_to_field": document_type.lower().replace(" ", "_"),
                "is_private": 1,
                "content": uploaded_file.read(),
            })
            file_doc.save(ignore_permissions=True)

            if marriage_status and hasattr(doc, "marriage_status"):
                doc.marriage_status = marriage_status
            if acknowledge_discrepancy:
                doc.acknowledge_discrepancy = 1
            doc.current_step = max(getattr(doc, "current_step", 1), 3)
            doc.save(ignore_permissions=True)

            return success({
                "document_id": file_doc.name,
                "file_url": file_doc.file_url,
                "upload_status": "uploaded",
                "current_step": 3,
            })
        except Exception as e:
            return error("UPLOAD_FAILED", str(e))

    if action == "list":
        application_id = kwargs.get("application_id")
        if not application_id:
            return error("MISSING_APP_ID", "application_id is required.")
        files = frappe.get_all(
            "File",
            filters={"attached_to_doctype": "Loan Application", "attached_to_name": application_id},
            fields=["name", "file_name", "file_url", "attached_to_field", "creation"],
        )
        return success({"documents": files})

    if action == "delete":
        application_id = kwargs.get("application_id")
        document_id = kwargs.get("document_id")
        if not document_id:
            return error("MISSING_DOC_ID", "document_id is required.")
        if not application_id:
            return error("MISSING_APP_ID", "application_id is required.")
        # Validate file belongs to this application
        file_owner = frappe.db.get_value("File", document_id, ["attached_to_doctype", "attached_to_name"], as_dict=True)
        if not file_owner or file_owner.attached_to_doctype != "Loan Application" or file_owner.attached_to_name != application_id:
            return error("NOT_AUTHORIZED", "This file does not belong to the specified application.")
        try:
            frappe.delete_doc("File", document_id, ignore_permissions=True)
            return success({"deleted": document_id})
        except Exception as e:
            return error("DELETE_FAILED", str(e))

    return error("INVALID_ACTION", f"Unknown action: {action}")


# ─── API 5: Farmer Details ────────────────────────────────────────────────────

@frappe.whitelist(allow_guest=False)
def farmer_details(**kwargs):
    kwargs = _parse_body(kwargs)
    action = kwargs.get("action", "save")

    if action == "save":
        application_id = kwargs.get("application_id")
        doc, err = _get_app(application_id)
        if err:
            return err

        field_map = {
            # Basic Information
            "full_name":                    kwargs.get("full_name"),
            "last_name":                    kwargs.get("last_name"),
            "mobile_phone":                 kwargs.get("mobile_phone"),
            "date_of_birth":                kwargs.get("date_of_birth"),
            "gender":                       kwargs.get("gender"),
            "woreda":                       kwargs.get("woreda"),
            "kebele":                       kwargs.get("kebele"),
            "id_type":                      kwargs.get("id_type"),
            "id_number":                    kwargs.get("id_number"),
            "language":                     kwargs.get("language"),
            # Land and Crop
            "land_size_acres":              kwargs.get("land_size_acres"),
            "farm_id":                      kwargs.get("farm_id"),
            "farm_polygon":                 kwargs.get("farm_polygon"),
            "land_acreage":                 kwargs.get("land_acreage"),
            "farm_land_number":             kwargs.get("farm_land_number"),
            # Socio-Economic
            "marital_status":               kwargs.get("marital_status"),
            "size_of_family":               kwargs.get("size_of_family"),
            "number_of_children":           kwargs.get("number_of_children"),
            "no_of_females_family":         kwargs.get("no_of_females_family"),
            "no_of_males_family":           kwargs.get("no_of_males_family"),
            "family_member_owns_land":      kwargs.get("family_member_owns_land"),
            "source_of_income":             kwargs.get("source_of_income"),
            "education_level":              kwargs.get("education_level"),
            # Land Crop and Livestock
            "total_farmland_as_landowner":  kwargs.get("total_farmland_as_landowner"),
            "total_farmland_as_crop_sharing": kwargs.get("total_farmland_as_crop_sharing"),
            "total_farmland_as_rented":     kwargs.get("total_farmland_as_rented"),
            "certification_id":             kwargs.get("certification_id"),
            # Agronomic Data
            "farmland_size_hectares":       kwargs.get("farmland_size_hectares"),
            "land_ownership_status":        kwargs.get("land_ownership_status"),
            "soil_fertility_minerals":      kwargs.get("soil_fertility_minerals"),
            "moisture_levels":              kwargs.get("moisture_levels"),
        }

        try:
            for k, v in field_map.items():
                if v is not None and hasattr(doc, k):
                    setattr(doc, k, v)
            doc.current_step = max(getattr(doc, "current_step", 1), 5)
            doc.save(ignore_permissions=True)
            return success({"application_id": doc.name, "current_step": 5})
        except Exception as e:
            return error("SAVE_FAILED", str(e))

    return error("INVALID_ACTION", f"Unknown action: {action}")


# ─── API: Get Farmer Details from Consent Data ─────────────────────────────

@frappe.whitelist(allow_guest=False)
def get_consent_data(**kwargs):
    """
    Returns nicely formatted farmer details from the webhook consent data.
    """
    kwargs = _parse_body(kwargs)
    application_id = kwargs.get("application_id")

    if not application_id:
        return error("MISSING_APP_ID", "application_id is required.")

    doc, err = _get_app(application_id, editable=False)
    if err:
        return err

    if not doc.consent_data:
        return error("NO_CONSENT_DATA", "Consent data not available yet. Please complete OTP verification.")

    try:
        data = json.loads(doc.consent_data) if isinstance(doc.consent_data, str) else doc.consent_data

        # Extract farmer information from the nested structure
        selected_farmer = data.get("selected_data", {}).get("farmer", {})
        farmer_basic = data.get("farmer", {})

        farmer_details = {
            "full_name": farmer_basic.get("name") or f"{selected_farmer.get('given_name', '')} {selected_farmer.get('family_name', '')}".strip(),
            "given_name": selected_farmer.get("given_name"),
            "family_name": selected_farmer.get("family_name"),
            "mobile_phone": selected_farmer.get("phone_no")[0] if selected_farmer.get("phone_no") else None,
            "email": selected_farmer.get("email"),
            "fayda_id": farmer_basic.get("id"),  # or unique_id if available
            "consent_status": doc.consent_status,
            "consent_creation_request_id": data.get("consent", {}).get("consent_creation_request_id")
        }

        return success({
            "application_id": application_id,
            "farmer_details": farmer_details,
            "raw_consent_data": data  # optional: for debugging
        })

    except Exception as e:
        return error("PARSE_ERROR", f"Failed to parse consent data: {str(e)}")

# ─── API 6: Application Manager ──────────────────────────────────────────────

@frappe.whitelist(allow_guest=False)
def application_manager(**kwargs):
    kwargs = _parse_body(kwargs)
    action = kwargs.get("action", "review")

    if action == "review":
        application_id = kwargs.get("application_id")
        doc, err = _get_app(application_id, editable=False)
        if err:
            return err

        has_docs = frappe.db.count("File", {
            "attached_to_doctype": "Loan Application",
            "attached_to_name": application_id,
        }) > 0

        consent_approved = getattr(doc, "consent_status", "") == "Approved"
        acknowledge_discrepancy = bool(getattr(doc, "acknowledge_discrepancy", False))
        current_step = getattr(doc, "current_step", 0)
        can_submit = (
            current_step >= 5
            and consent_approved
            and has_docs
        )

        return success({
            "sections": {
                "Loan Requirements":         {"complete": current_step >= 1, "step": 1},
                "Bank Details":              {"complete": current_step >= 2, "step": 2},
                "Supporting Documents":      {"complete": has_docs,         "step": 3},
                "Consent & OTP Verification": {"complete": consent_approved, "step": 4},
                "Farmer Details":            {"complete": current_step >= 5, "step": 5},
            },
            "acknowledge_discrepancy": acknowledge_discrepancy,
            "can_submit": can_submit,
        })

    if action == "submit":
        application_id = kwargs.get("application_id")
        doc, err = _get_app(application_id)
        if err:
            return err

        if getattr(doc, "consent_status", "") != "Approved":
            return error("CONSENT_NOT_APPROVED", "OTP consent must be verified before submitting.")

        has_docs = frappe.db.count("File", {
            "attached_to_doctype": "Loan Application",
            "attached_to_name": application_id,
        }) > 0
        if not has_docs:
            return error("MISSING_DOCS", "At least one supporting document must be uploaded.")

        try:
            submitted_at = now_datetime()
            doc.status        = "Submitted"
            doc.current_step  = 6
            doc.submitted_at  = submitted_at
            doc.transfer_method = "SFTP Sync"
            doc.save(ignore_permissions=True)

            farmer_name = f"{getattr(doc, 'full_name', '')} {getattr(doc, 'last_name', '')}".strip()

            return success({
                "application_id": doc.name,
                "submitted_at":   submitted_at,
                "transfer_method": "SFTP Sync",
                "farmer_name":    farmer_name,
                "status":         "Submitted",
            })
        except Exception as e:
            return error("SUBMIT_FAILED", str(e))

    if action == "tracking":
        application_id = kwargs.get("application_id")
        doc, err = _get_app(application_id, editable=False)
        if err:
            return err

        submitted_at = getattr(doc, "submitted_at", None)
        status = getattr(doc, "status", "Draft")

        return success({
            "timeline": [
                {"step": "Application Submitted", "status": "Done" if status != "Draft" else "Pending", "timestamp": submitted_at},
                {"step": "Under Review",           "status": "Pending", "timestamp": None},
                {"step": "Credit Scoring",         "status": "Pending", "timestamp": None},
                {"step": "Decision",               "status": "Pending", "timestamp": None},
                {"step": "Loan Disbursed",         "status": "Pending", "timestamp": None},
            ]
        })

    if action == "draft":
        application_id = kwargs.get("application_id")
        step = kwargs.get("step")
        data = kwargs.get("data", {})

        doc, err = _get_app(application_id)
        if err:
            return err

        # Fields that must not be set via the generic draft endpoint
        PROTECTED_FIELDS = {"status", "loan_officer", "consent_status", "submitted_at",
                            "sftp_transmitted", "sftp_transmitted_at", "current_step"}
        try:
            if isinstance(data, str):
                data = json.loads(data)
            for k, v in data.items():
                if k in PROTECTED_FIELDS:
                    continue
                if hasattr(doc, k):
                    setattr(doc, k, v)
            doc.save(ignore_permissions=True)
            return success({"saved_at": now_datetime(), "step": step})
        except Exception as e:
            return error("DRAFT_SAVE_FAILED", str(e))

    if action == "cancel":
        application_id = kwargs.get("application_id")
        reason = kwargs.get("reason", "")
        doc, err = _get_app(application_id)
        if err:
            return err

        try:
            doc.status = "Cancelled"
            doc.cancellation_reason = reason
            doc.save(ignore_permissions=True)
            return success({"application_id": doc.name, "status": "Cancelled"})
        except Exception as e:
            return error("CANCEL_FAILED", str(e))

    return error("INVALID_ACTION", f"Unknown action: {action}")
