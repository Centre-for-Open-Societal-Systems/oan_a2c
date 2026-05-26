import frappe
import json

@frappe.whitelist(allow_guest=True)
def receive_consent_data(**kwargs):
    """
    Webhook receiver for Odoo OpenG2P consent data.
    Bypasses custom JWT middleware.
    """
    try:
        # === Get Payload ===
        data = {}
        if frappe.request:
            data = frappe.request.get_json(silent=True) or {}
        if not data and kwargs:
            data = kwargs

        frappe.logger().info(f"🔗 Webhook received. Keys: {list(data.keys())}")

        # === Extract consent_creation_request_id ===
        consent_request_id = None
        if isinstance(data.get("consent"), dict):
            consent_request_id = data["consent"].get("consent_creation_request_id")
        if not consent_request_id:
            consent_request_id = data.get("consent_creation_request_id") or data.get("request_id")

        if not consent_request_id:
            return {"status": "error", "message": "Missing consent_creation_request_id"}

        # === Find Consent Request ===
        consent_docs = frappe.get_all(
            "Consent Request",
            filters={"openg2p_consent_id": consent_request_id},
            fields=["name", "loan_application"],
            limit=1
        )

        if not consent_docs:
            return {"status": "error", "message": f"Consent Request not found: {consent_request_id}"}

        loan_application_name = consent_docs[0].get("loan_application")
        if not loan_application_name:
            return {"status": "success", "message": "No linked Loan Application"}

        # === Save Data ===
        loan_app = frappe.get_doc("Loan Application", loan_application_name)
        loan_app.consent_data = json.dumps(data, indent=2, ensure_ascii=False)
        loan_app.consent_status = "Approved"
        loan_app.save(ignore_permissions=True)
        frappe.db.commit()

        frappe.logger().info(f"✅ SUCCESS: Data saved in {loan_app.name}")
        return {"status": "success", "message": "Data stored successfully", "loan_application": loan_app.name}

    except Exception as e:
        frappe.logger().error(f"Webhook Error: {str(e)}", exc_info=True)
        return {"status": "error", "message": str(e)}