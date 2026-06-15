import frappe
import json
from oan_a2c.api.utils import success_response, handle_api_errors

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
        consent_info = data.get("consent", {})
        
        # Update Consent Request status
        new_status = consent_info.get("status")
        if new_status:
            mapped_status = new_status.capitalize() if new_status.islower() else new_status
            frappe.db.set_value("A2C Consent Request", consent_doc_name, "status", mapped_status)

        # Parse Farmer Data
        farmer_data = data.get("farmer", {})
        selected_data = data.get("selected_data", {}).get("farmer", {})

        name_parts = farmer_data.get("name", "").split(" ")
        first_name = name_parts[0] if len(name_parts) > 0 else ""
        last_name = " ".join(name_parts[1:]) if len(name_parts) > 1 else ""

        region = selected_data.get("Region", {}).get("name", "")
        zone = selected_data.get("Zone", {}).get("name", "")
        woreda = selected_data.get("Woreda", {}).get("name", "")
        
        location_parts = [p for p in [region, zone, woreda] if p]
        location = ", ".join(location_parts)

        # Fetch Consent Request to check links
        consent_doc = frappe.get_doc("A2C Consent Request", consent_doc_name)
        lead_id = consent_doc.get("lead")
        
        phone_number = selected_data.get("Phone Number", "")
        if not phone_number and lead_id:
            phone_number = frappe.db.get_value("A2C Lead", lead_id, "phone_number")

        updates = {
            "first_name": first_name,
            "last_name": last_name,
            "location": location,
            "farmer_id": farmer_data.get("id"),
            "consent_id": consent_doc_name,
            "phone_number": phone_number,
            "lead_id": lead_id
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

            if existing_profile_name:
                farmer_profile.save(ignore_permissions=True)
            else:
                farmer_profile.insert(ignore_permissions=True)
            
            # Link back to lead
            lead_doc.db_set("farmer_profile", farmer_profile.name)

        frappe.db.commit()
        frappe.logger().info(f"✅ SUCCESS: Background webhook data saved for consent {consent_doc_name}")

    except Exception as e:
        frappe.db.rollback()
        # Log to Frappe Desk visible Error Log (Option 1)
        frappe.log_error(frappe.get_traceback(), f"Background Webhook Error for Consent {consent_doc_name}")
        # Transition Consent Request status to Failed
        try:
            frappe.db.set_value("A2C Consent Request", consent_doc_name, "status", "Failed")
            frappe.db.commit()
        except Exception as status_err:
            frappe.logger().error(f"Failed to update Consent Request status to Failed: {str(status_err)}")
        raise e


def validate_and_enqueue_consent(data, enforce_permission=True):
    """
    Internal: validate an OpenG2P consent payload and enqueue background
    processing. Returns the resolved A2C Consent Request name.

    Callable in-process (e.g. from the WebSub hub endpoint) without going
    through HTTP auth. When called from the authenticated receiver, pass
    enforce_permission=True so the caller's write permission is checked.
    """
    consent_info = data.get("consent", {})
    consent_request_id = consent_info.get("consent_creation_request_id")

    if not consent_request_id:
        frappe.throw(frappe._("Missing consent_creation_request_id"), frappe.ValidationError)

    # Find Consent Request
    consent_docs = frappe.get_all(
        "A2C Consent Request",
        filters={"openg2p_consent_id": consent_request_id},
        fields=["name"],
        limit=1
    )

    if not consent_docs:
        frappe.throw(frappe._("Consent Request not found: {0}").format(consent_request_id), frappe.DoesNotExistError)

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
        consent_request_id=consent_request_id,
        job_name=f"process_consent_{consent_request_id}"
    )

    return consent_doc_name


@frappe.whitelist(allow_guest=False)
@handle_api_errors
def receive_consent_data(**kwargs):
    """
    Authenticated webhook receiver for OpenG2P consent data.
    Requires `Authorization: token <api_key>:<api_secret>` and write permission
    on A2C Consent Request. Used by direct callers (Postman, Odoo server action).
    """
    data = {}
    if frappe.request:
        data = frappe.request.get_json(silent=True) or {}
    if not data and kwargs:
        data = kwargs

    frappe.logger().info(f"🔗 Webhook received. Keys: {list(data.keys())}")

    consent_doc_name = validate_and_enqueue_consent(data, enforce_permission=True)

    frappe.response["http_status_code"] = 202
    return success_response(
        data={
            "consent_request": consent_doc_name
        },
        message="Data accepted for background processing"
    )



