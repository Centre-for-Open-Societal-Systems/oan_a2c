import frappe
import json

def process_consent_data(data, consent_doc_name, consent_request_id):
    """
    Background worker function that safely processes the OpenG2P payload.
    """
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

        updates = {
            "first_name": first_name,
            "last_name": last_name,
            "location": location,
            "farmer_id": farmer_data.get("id"),
            "consent_id": consent_doc_name
        }

        # Fetch Consent Request to check links
        consent_doc = frappe.get_doc("A2C Consent Request", consent_doc_name)

        # Update Loan Application
        loan_app_name = consent_doc.get("loan_application")
        if loan_app_name:
            loan_app = frappe.get_doc("A2C Loan Application", loan_app_name)
            for k, v in updates.items():
                if v is not None and v != "":
                    loan_app.set(k, v)
            loan_app.save(ignore_permissions=True)
            
            # Update Farmer Profile if linked
            farmer_profile_name = loan_app.get("farmer_profile")
            if farmer_profile_name:
                farmer_profile = frappe.get_doc("A2C Farmer Profile", farmer_profile_name)
                for k, v in updates.items():
                    if v is not None and v != "":
                        farmer_profile.set(k, v)
                farmer_profile.save(ignore_permissions=True)
        else:
            # Fallback: try finding by consent_id directly
            profiles = frappe.get_all("A2C Farmer Profile", filters={"consent_id": consent_doc_name}, pluck="name")
            if profiles:
                profile = frappe.get_doc("A2C Farmer Profile", profiles[0])
                for k, v in updates.items():
                    if v is not None and v != "":
                        profile.set(k, v)
                profile.save(ignore_permissions=True)

        frappe.db.commit()
        frappe.logger().info(f"✅ SUCCESS: Background webhook data saved for consent {consent_doc_name}")

    except Exception as e:
        frappe.logger().error(f"Background Webhook Error: {str(e)}", exc_info=True)


@frappe.whitelist(allow_guest=True)
def receive_consent_data(**kwargs):
    """
    Webhook receiver for Odoo OpenG2P consent data.
    Validates identity and pushes processing to a background worker queue.
    """
    try:
        data = {}
        if frappe.request:
            data = frappe.request.get_json(silent=True) or {}
        if not data and kwargs:
            data = kwargs

        frappe.logger().info(f"🔗 Webhook received. Keys: {list(data.keys())}")

        consent_info = data.get("consent", {})
        consent_request_id = consent_info.get("consent_creation_request_id")
        
        if not consent_request_id:
            frappe.response["http_status_code"] = 400
            return {"status": "error", "message": "Missing consent_creation_request_id"}

        # Find Consent Request
        consent_docs = frappe.get_all(
            "A2C Consent Request",
            filters={"openg2p_consent_id": consent_request_id},
            fields=["name"],
            limit=1
        )

        if not consent_docs:
            frappe.response["http_status_code"] = 404
            return {"status": "error", "message": f"Consent Request not found: {consent_request_id}"}

        consent_doc_name = consent_docs[0].name
        
        # Enqueue the processing job to prevent blocking the OpenG2P system
        frappe.enqueue(
            method=process_consent_data,
            queue="default",
            data=data,
            consent_doc_name=consent_doc_name,
            consent_request_id=consent_request_id,
            job_name=f"process_consent_{consent_request_id}"
        )

        frappe.response["http_status_code"] = 202
        return {"status": "success", "message": "Data accepted for background processing", "consent_request": consent_doc_name}

    except Exception as e:
        frappe.logger().error(f"Webhook Gateway Error: {str(e)}", exc_info=True)
        frappe.response["http_status_code"] = 500
        return {"status": "error", "message": str(e)}
