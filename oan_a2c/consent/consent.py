import frappe
from frappe import _
from frappe.utils import now_datetime, add_to_date
from .openg2p_client import OpenG2PConsentClient
from .utils import generate_consent_receipt, enqueue_websub_delivery


@frappe.whitelist(allow_guest=False)
def send_otp_and_create_consent(**kwargs):
    """
    Unified endpoint called when user clicks 'Send OTP Request'.
    1. Looks up farmer by Fayda ID (national ID)
    2. Creates consent request in OpenG2P
    3. Sends OTP via Fayda
    4. Saves Consent Request doc in Frappe
    5. Returns transaction_id for OTP verification

    Required params: fayda_id, partner
    Optional params: loan_application, consent_form_attachment, purpose, validity_from, validity_to
    """
    data = {}
    form = getattr(frappe, 'form_dict', {})
    try:
        if frappe.request:
            data = frappe.request.get_json(silent=True) or {}
    except Exception:
        pass

    def _get(key, default=None):
        return kwargs.get(key) or data.get(key) or form.get(key) or default

    fayda_id                = _get("fayda_id")
    partner                 = _get("partner")
    loan_application        = _get("loan_application")
    consent_form_attachment = _get("consent_form_attachment")
    purpose                 = _get("purpose", "Loan for seeds and fertilizer")
    validity_from           = _get("validity_from")
    validity_to             = _get("validity_to")

    if not fayda_id:
        frappe.throw(_("fayda_id is required"))
    if not partner:
        frappe.throw(_("partner is required"))

    if not validity_from or not validity_to:
        validity_from = str(now_datetime())
        validity_to   = str(add_to_date(now_datetime(), years=1))

    client = OpenG2PConsentClient()

    # --- Step 1: Find farmer by Fayda ID (admin session) ---
    farmer_db_id = client.get_farmer_by_fayda_id(fayda_id)
    if not farmer_db_id:
        frappe.throw(_("Farmer with Fayda ID '{0}' not found in OpenG2P.").format(fayda_id))

    print(f">>>>>> Farmer DB ID: {farmer_db_id}")

    # --- Step 2: Get partner ID (admin session) ---
    partner_id_openg2p = client.get_partner_id(partner)
    if not partner_id_openg2p:
        frappe.throw(_("Partner '{0}' not found in OpenG2P.").format(partner))

    print(f">>>>>> Partner ID: {partner_id_openg2p}")

    # --- Step 3: Get allowed data field IDs for this partner (admin session) ---
    allowed_data_field_ids = client.get_partner_allowed_data_field_ids(partner_id_openg2p)
    if not allowed_data_field_ids:
        frappe.throw(_(
            "Partner '{0}' has no allowed data fields configured in OpenG2P."
        ).format(partner))

    # --- Step 4: Upload consent form attachment (admin session) ---
    attachment_ids = None
    if consent_form_attachment and consent_form_attachment not in ('None', 'none', ''):
        try:
            odoo_att_id = client.upload_consent_attachment(consent_form_attachment)
            if odoo_att_id:
                attachment_ids = [odoo_att_id]
            print(f">>>>>> Attachment uploaded. Odoo ID: {odoo_att_id}")
        except Exception as e:
            frappe.throw(_("Attachment upload failed: {0}").format(str(e)))

    # --- Step 5: Create consent in OpenG2P (portal session) ---
    try:
        consent_response = client.create_consent_request(
            partner_id=partner_id_openg2p,
            farmer_db_id=farmer_db_id,
            consent_type="specific",
            purpose=purpose,
            validity_from=validity_from,
            validity_to=validity_to,
            allowed_data_field_ids=allowed_data_field_ids,
            attachment_ids=attachment_ids
        )
    except Exception as e:
        frappe.throw(_("Failed to create consent in OpenG2P: {0}").format(str(e)))

    # openg2p_consent_id = (
    #     consent_response.get("id")
    #     or consent_response.get("consent_id")
    #     or consent_response.get("name")
    #     or "G2P-CONS-XXXXX"
    # )

    consent_data = consent_response.get("data") or {}
    openg2p_consent_id = (
        consent_data.get("consent_creation_request_id")
        or consent_data.get("id")
        or consent_response.get("consent_creation_request_id")
        or consent_response.get("id")
        or "G2P-CONS-XXXXX"
    )
    print(f">>>>>> OpenG2P Consent ID: {openg2p_consent_id}")

    # --- Step 6: Send OTP (portal session) ---
    # Portal user (a2capp@test.com) has consent_parent_partner_id set in Odoo
    # so partner context is resolved automatically server-side — no partner_id needed
    try:
        otp_response = client.send_otp(farmer_id=farmer_db_id)
    except Exception as e:
        frappe.throw(_("Consent created but OTP failed: {0}").format(str(e)))

    if isinstance(otp_response, dict) and otp_response.get("success") is False:
        frappe.throw(_("OTP Error: {0}").format(otp_response.get("message", "Unknown error")))

    transaction_id = None
    if isinstance(otp_response, dict):
        transaction_id = otp_response.get("transaction_id") or otp_response.get("id")

    if not transaction_id:
        frappe.throw(_("OTP sent but no transaction_id returned: {0}").format(str(otp_response)))

    masked_phone = ""
    if isinstance(otp_response, dict):
        masked_phone = otp_response.get("masked_mobile") or otp_response.get("masked_phone", "")

    # --- Step 7: Save Consent Request doc in Frappe ---
    try:
        doc = frappe.new_doc("Consent Request")
        doc.farmer_fayda_id                = fayda_id
        doc.partner                 = partner
        doc.loan_application        = loan_application
        doc.consent_type            = "specific"
        doc.purpose                 = purpose
        doc.validity_from           = validity_from
        doc.validity_to             = validity_to
        doc.consent_form_attachment = consent_form_attachment
        doc.openg2p_consent_id      = openg2p_consent_id
        doc.otp_transaction_id      = transaction_id
        doc.status                  = "Pending OTP"
        doc.insert(ignore_permissions=True)
        frappe.db.commit()
        consent_request_name = doc.name
        print(f">>>>>> Frappe Consent Request created: {consent_request_name}")
    except Exception as e:
        print(f">>>>>> Warning: Frappe doc creation failed: {str(e)}")
        frappe.log_error(f"Consent Doc Creation Failed: {str(e)}", "Consent Request Error")
        consent_request_name = None

    return {
        "status": "success",
        "consent_request": consent_request_name,
        "openg2p_consent_id": openg2p_consent_id,
        "transaction_id": transaction_id,
        "masked_phone": masked_phone,
        "message": "Consent created and OTP sent successfully"
    }


@frappe.whitelist(allow_guest=False)
def verify_otp(consent_request=None, otp_code=None):
    """
    Verify the OTP entered by the farmer.
    On success, marks consent as Approved, generates a receipt,
    and updates the linked Loan Application.
    """
    data = {}
    form = getattr(frappe, 'form_dict', {})
    try:
        if frappe.request:
            data = frappe.request.get_json(silent=True) or {}
    except Exception:
        pass

    consent_request = consent_request or data.get("consent_request") or form.get("consent_request")
    otp_code        = otp_code        or data.get("otp_code")        or form.get("otp_code")

    if not consent_request or not otp_code:
        frappe.throw(_("consent_request and otp_code are required"))

    doc = frappe.get_doc("Consent Request", consent_request)
    transaction_id = doc.otp_transaction_id

    if not transaction_id:
        frappe.throw(_("OTP was not requested for this consent"))

    client = OpenG2PConsentClient()

    farmer_db_id = client.get_farmer_by_fayda_id(doc.farmer_fayda_id)
    if not farmer_db_id:
        frappe.throw(_("Farmer with Fayda ID '{0}' not found in OpenG2P.").format(doc.farmer_fayda_id))

    try:
        response = client.verify_otp(
            farmer_id=farmer_db_id,
            transaction_id=transaction_id,
            otp_code=otp_code
        )
        print(f">>>>>> verify_otp response: {response}")
    except Exception as e:
        frappe.throw(_("OTP verification failed: {0}").format(str(e)))

    try:
        # Approve consent in Odoo to trigger webhook
        if doc.openg2p_consent_id:
            client.approve_consent_request(doc.openg2p_consent_id)
            print(f">>>>>> Odoo consent request {doc.openg2p_consent_id} approved explicitly")
    except Exception as e:
        print(f">>>>>> Warning: Failed to approve consent in Odoo explicitly: {e}")

    frappe.db.set_value("Consent Request", consent_request, "status", "Approved")
    frappe.db.set_value("Consent Request", consent_request, "otp_verified_at", now_datetime())

    receipt = generate_consent_receipt(consent_request)
    enqueue_websub_delivery(receipt)
    
    # Link back to Loan Application
    if doc.loan_application:
        frappe.db.set_value("Loan Application", doc.loan_application, "consent_status", "Approved")
        frappe.db.set_value("Loan Application", doc.loan_application, "consent_request", consent_request)
        frappe.db.set_value("Loan Application", doc.loan_application, "consent_receipt", receipt.get("signature"))
        
        # Advance step if needed
        current_step = frappe.db.get_value("Loan Application", doc.loan_application, "current_step") or 0
        if int(current_step) < 4:
            frappe.db.set_value("Loan Application", doc.loan_application, "current_step", 4)
            
    frappe.db.commit()

    return {
        "status": "success",
        "consent_request": consent_request,
        "consent_receipt": receipt.get("signature"),
        "message": "Consent approved successfully"
    }