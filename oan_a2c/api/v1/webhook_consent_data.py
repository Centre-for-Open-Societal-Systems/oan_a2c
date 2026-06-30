import frappe
import json
from oan_a2c.api.utils import success_response, handle_api_errors, validate_request
from pydantic import BaseModel, Field, ValidationError
from typing import Optional, Dict, Any, Any as DummyAny

class SelectedDataSchema(BaseModel):
    pass


class FarmerInfoSchema(BaseModel):
    id: Optional[int] = None
    farmer_id: Optional[Any] = None
    name: Optional[str] = None

class ConsentInfoSchema(BaseModel):
    id: Optional[int] = None
    consent_creation_request_id: str = Field(..., min_length=1)
    consent_type: Optional[str] = None
    status: Optional[str] = None
    approved_at: Optional[str] = None
    validity_from: Optional[str] = None
    validity_to: Optional[str] = None

class ReceiveConsentDataSchema(BaseModel):
    source: Optional[str] = None
    event_type: Optional[str] = None
    published_at: Optional[str] = None
    consent: ConsentInfoSchema
    farmer: Optional[FarmerInfoSchema] = None
    selected_data: Optional[Dict[str, Any]] = None

def process_consent_data(data, consent_doc_name, consent_request_id):
    """
    Background worker function that safely processes the OpenG2P payload.
    """
    # Set user context based on A2C Consent Request owner (Option 1 & 2)
    owner = frappe.db.get_value("A2C Consent Request", consent_doc_name, "owner")
    # TODO: This fallback to "Administrator" will be changed to fail/raise an exception if owner is not present
    user_to_set = owner if owner and frappe.db.exists("User", owner) else "Administrator"
    frappe.set_user(user_to_set)

    try:
        validated = ReceiveConsentDataSchema.model_validate(data)
        consent_info = validated.consent
        
        # Update Consent Request status
        new_status = consent_info.status
        if new_status:
            mapped_status = new_status.capitalize() if new_status.islower() else new_status
            frappe.db.set_value("A2C Consent Request", consent_doc_name, "status", mapped_status)

        if validated.published_at:
            frappe.db.set_value("A2C Consent Request", consent_doc_name, "websub_delivered_at", validated.published_at)

        # Parse Farmer Data
        farmer_data = validated.farmer or FarmerInfoSchema()
        
        raw_selected_data = validated.selected_data or {}
        farmer_info_dict = {}
        # Find the first dictionary inside selected_data that contains farmer info
        if isinstance(raw_selected_data, dict):
            for key, val in raw_selected_data.items():
                if isinstance(val, dict):
                    farmer_info_dict = val
                    break

        full_name = farmer_info_dict.get("Full Name", "")
        if full_name:
            name_parts = full_name.split(" ")
        else:
            name_parts = (farmer_data.name or "").split(" ")
            
        first_name = name_parts[0] if len(name_parts) > 0 else ""
        last_name = " ".join(name_parts[1:]) if len(name_parts) > 1 else ""

        # Mobile could be list or string
        mobile_data = farmer_info_dict.get("Mobile Number", farmer_info_dict.get("Phone Number", []))
        if isinstance(mobile_data, list) and mobile_data:
            phone_number = str(mobile_data[0])
        elif isinstance(mobile_data, str):
            phone_number = mobile_data
        else:
            phone_number = ""
            
        email = farmer_info_dict.get("Email", "")

        # Fetch Consent Request to check links
        consent_doc = frappe.get_doc("A2C Consent Request", consent_doc_name)
        lead_id = consent_doc.get("lead")
        
        # Parse Source of income
        source_of_income_list = farmer_info_dict.get("Source of Income", [])
        source_of_income = ", ".join([s.get("name") for s in source_of_income_list if isinstance(s, dict)]) if isinstance(source_of_income_list, list) else source_of_income_list
        
        # Parse farmland size
        farmland_size_data = farmer_info_dict.get("Farmland size (Hectares)", [])
        farmland_size_hectares = farmland_size_data[0] if isinstance(farmland_size_data, list) and farmland_size_data else farmland_size_data
        
        # Parse Certification ID
        land_ids = farmer_info_dict.get("Land ID", [])
        if isinstance(land_ids, list):
            certification_id = ", ".join([str(x) for x in land_ids])
        else:
            certification_id = land_ids

        # Parse Certification Photo
        cert_photos = farmer_info_dict.get("Certificate Provided", [])
        certification_photo_url = cert_photos[0] if isinstance(cert_photos, list) and len(cert_photos) > 0 else (cert_photos if isinstance(cert_photos, str) else None)

        raw_edu = farmer_info_dict.get("Education Level", "").lower()
        if "basic" in raw_edu or "primary" in raw_edu:
            education_level = "Primary (Grade 1-8)"
        elif "secondary" in raw_edu or "high" in raw_edu:
            education_level = "Secondary (Grade 9-12)"
        elif "none" in raw_edu:
            education_level = "None / No formal education"
        elif "tvet" in raw_edu or "certificate" in raw_edu:
            education_level = "TVET / Certificate"
        elif "diploma" in raw_edu:
            education_level = "Diploma"
        elif "degree" in raw_edu or "university" in raw_edu:
            education_level = "Degree and above"
        else:
            education_level = ""

        region_data = farmer_info_dict.get("Region")
        region = region_data.get("name") if isinstance(region_data, dict) else (region_data or "")

        woreda_data = farmer_info_dict.get("Woreda")
        woreda = woreda_data.get("name") if isinstance(woreda_data, dict) else (woreda_data or "")

        kebele_data = farmer_info_dict.get("Kebele")
        kebele = kebele_data.get("name") if isinstance(kebele_data, dict) else (kebele_data or "")

        updates = {
            "first_name": first_name,
            "last_name": last_name,
            "region": region,
            "woreda": woreda,
            "kebele": kebele,
            "language": farmer_info_dict.get("Language"),
            "farmer_id": farmer_data.id,
            "consent_id": consent_doc_name,
            "phone_number": phone_number,
            "email": email,
            "lead_id": lead_id,
            "date_of_birth": farmer_info_dict.get("Date of Birth"),
            "gender": (farmer_info_dict.get("Gender") or "").capitalize(),
            "marital_status": (farmer_info_dict.get("Marital Status") or "").capitalize(),
            "size_of_family": frappe.utils.cint(farmer_info_dict.get("Size of Family")),
            "number_of_children": frappe.utils.cint(farmer_info_dict.get("Number of Children")),
            "no_of_females_family": frappe.utils.cint(farmer_info_dict.get("Number of Females ( Family )")),
            "source_of_income": source_of_income,
            "education_level": education_level,
            "family_member_owns_land_independently": frappe.utils.cint(farmer_info_dict.get("Other family Member Own Land")),
            "total_farmland_size_as_landowner": frappe.utils.flt(farmer_info_dict.get("Total Owned Land")),
            "total_farmland_size_as_crop_sharing": frappe.utils.flt(farmer_info_dict.get("Total Crop sharing")),
            "total_farmland_size_as_rented": frappe.utils.flt(farmer_info_dict.get("Total Rented Land")),
            "farmland_size_hectares": frappe.utils.flt(farmland_size_hectares),
            "land_ownership_status": farmer_info_dict.get("Land Ownership Status"),
            "certification_id": certification_id,
            "certification_photo_url": certification_photo_url
        }

        if lead_id:
            lead_doc = frappe.get_doc("A2C Lead", lead_id)
            
            existing_profile_name = None
            if phone_number:
                existing_profile_name = frappe.db.get_value("A2C Farmer Profile", {"phone_number": phone_number}, "name")
            
            if existing_profile_name:
                farmer_profile = frappe.get_doc("A2C Farmer Profile", existing_profile_name)
            else:
                farmer_profile = frappe.new_doc("A2C Farmer Profile")

            for k, v in updates.items():
                if v is not None and v != "":
                    farmer_profile.set(k, v)

            # ignore_permissions=True is required because this background job processes webhooks
            # from OpenG2P asynchronously. The user context set (or Administrator fallback) may
            # not have direct write permissions on A2C Farmer Profile, but the system must persist
            # the verified profile details. Approved by: Lead Architect.
            if existing_profile_name:
                farmer_profile.save(ignore_permissions=True)
            else:
                farmer_profile.insert(ignore_permissions=True)
            
            # db_set is used here to link the farmer profile back to the lead, bypassing
            # validation, because this is an automated background webhook update. Approved by: Lead Architect.
            lead_doc.db_set("farmer_profile", farmer_profile.name)

        frappe.db.commit()
        frappe.logger().info(f"✅ SUCCESS: Background webhook data saved for consent {consent_doc_name}")

    except Exception as e:
        frappe.db.rollback()
        # Log to Frappe Desk visible Error Log (Option 1)
        frappe.log_error(frappe.get_traceback(), f"Background Webhook Error for Consent {consent_doc_name}")
        
        try:
            frappe.db.set_value("A2C Consent Request", consent_doc_name, "status", "Failed")
            frappe.db.commit()
        except Exception:
            pass

        raise e


def validate_and_enqueue_consent(data, enforce_permission=True):
    """
    Internal: validate an OpenG2P consent payload and enqueue background
    processing. Returns the resolved A2C Consent Request name.

    Callable in-process (e.g. from the WebSub hub endpoint) without going
    through HTTP auth. When called from the authenticated receiver, pass
    enforce_permission=True so the caller's write permission is checked.
    """
    try:
        validated_data = ReceiveConsentDataSchema.model_validate(data)
    except ValidationError as e:
        frappe.throw(frappe._("Invalid webhook payload format: {0}").format(str(e)), frappe.ValidationError)

    consent_info = validated_data.consent
    consent_id = consent_info.id

    # Find Consent Request
    consent_docs = frappe.get_all(
        "A2C Consent Request",
        filters={"openg2p_consent_id": str(consent_id)},
        fields=["name"],
        limit=1
    )

    if not consent_docs:
        frappe.throw(frappe._("Consent Request not found with OpenG2P ID: {0}").format(consent_id), frappe.DoesNotExistError)

    consent_doc_name = consent_docs[0].name

    # Pre-validate linked lead existence (Option 3)
    lead_id = frappe.db.get_value("A2C Consent Request", consent_doc_name, "lead")
    if lead_id and not frappe.db.exists("A2C Lead", lead_id):
        frappe.throw(frappe._("Linked Lead not found: {0}").format(lead_id), frappe.DoesNotExistError)

    # Enforce write permissions on the Consent Request (authenticated path only)
    if enforce_permission:
        frappe.has_permission("A2C Consent Request", "write", doc=consent_doc_name, throw=True)

    # Enqueue the processing job to prevent blocking the OpenG2P system
    frappe.enqueue(
        method=process_consent_data,
        queue="default",
        data=data,
        consent_doc_name=consent_doc_name,
        consent_request_id=str(consent_id),
        job_name=f"process_consent_{consent_id}"
    )

    return consent_doc_name


@frappe.whitelist(allow_guest=False)
@validate_request(ReceiveConsentDataSchema)
@handle_api_errors
def receive_consent_data(**kwargs):
    """
    Authenticated webhook receiver for OpenG2P consent data.
    Requires `Authorization: token <api_key>:<api_secret>` and write permission
    on A2C Consent Request. Used by direct callers (Postman, Odoo server action).
    """
    frappe.logger().info(f"🔗 Webhook received. Keys: {list(kwargs.keys())}")

    consent_doc_name = validate_and_enqueue_consent(kwargs, enforce_permission=True)

    frappe.response["http_status_code"] = 202
    return success_response(
        data={
            "consent_request": consent_doc_name
        },
        message="Data accepted for background processing"
    )



