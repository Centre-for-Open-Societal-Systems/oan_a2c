import frappe
from frappe import _
from frappe.utils import now_datetime, add_to_date
from .openg2p_client import OpenG2PConsentClient
from .utils import generate_consent_receipt, enqueue_websub_delivery
from oan_a2c.api.utils import success_response, handle_api_errors


# ─── Shared helper ────────────────────────────────────────────────────────────

def _parse_request(kwargs):
    """Merge kwargs, JSON body, and form_dict into one dict — returns a getter callable."""
    data = {}
    form = getattr(frappe, "form_dict", {})
    try:
        if frappe.request:
            data = frappe.request.get_json(silent=True) or {}
    except Exception:
        pass

    def _getter(key, default=None):
        return kwargs.get(key) or data.get(key) or form.get(key) or default

    return _getter


def _fetch_and_save_farmer_data(client, fayda_id, target_doctype, target_name, openg2p_consent_id):
    """
    Directly fetch farmer profile from OpenG2P and save it as consent_data
    on the target document. Mirrors what the WebSub webhook would have sent.
    Non-fatal on failure.
    """
    import json as _json

    try:
        farmer_db_id = client.get_farmer_by_fayda_id(fayda_id)
        farmer_record = {}
        selected_data = {}

        if farmer_db_id:
            farmer_records = client._admin_search_read(
                "res.partner",
                [["id", "=", farmer_db_id]],
                ["id", "name", "email", "mobile", "phone"]
            )
            frappe.logger().debug(f"Direct fetch res.partner: {farmer_records}")

            if farmer_records:
                f = farmer_records[0]
                full_name = (f.get("name") or "").strip()
                parts = full_name.split()
                given_name  = parts[0].title() if parts else ""
                family_name = " ".join(p.title() for p in parts[1:]) if len(parts) > 1 else ""
                mobile = f.get("mobile") or f.get("phone") or ""

                farmer_record = {"id": f.get("id"), "name": full_name}
                selected_data = {
                    "farmer": {
                        "given_name":  given_name,
                        "family_name": family_name,
                        "email":       f.get("email") or "",
                        "phone_no":    [mobile] if mobile else [],
                    }
                }

        synthetic_payload = {
            "source": "frappe_direct_fetch",
            "event_type": "WEBSUB_INDIVIDUAL_UPDATED",
            "published_at": str(now_datetime()),
            "consent": {
                "consent_creation_request_id": openg2p_consent_id,
                "status": "approved",
                "approved_at": str(now_datetime()),
            },
            "farmer": farmer_record,
            "selected_data": selected_data,
        }

        doc = frappe.get_doc(target_doctype, target_name)
        doc.consent_data   = _json.dumps(synthetic_payload, indent=2, ensure_ascii=False)
        if target_doctype == "A2C Loan Application":
            doc.fayda_verified = 1
        doc.save(ignore_permissions=True)
        frappe.logger().info(f"Farmer data saved to {target_doctype} {target_name}")
        return selected_data.get("farmer", {}), farmer_record

    except Exception as e:
        frappe.logger().warning(f"Direct farmer data fetch failed: {e}")
        frappe.log_error(f"Direct consent data fetch failed: {e}", "Consent Data Fetch")
        return {}, {}


# ─── NEW FLOW ─────────────────────────────────────────────────────────────────
# Step 2: request_otp  (farmer must already be found via search_farmer)
# Step 3: verify_otp_and_create  (verify OTP → create consent → create Lead Application)
# ─────────────────────────────────────────────────────────────────────────────


@frappe.whitelist(allow_guest=False)
@handle_api_errors
def search_farmer(**kwargs):
    """
    NEW FLOW — Step 1: Search for Farmer in OpenG2P using Fayda ID.
    """
    _getter = _parse_request(kwargs)
    fayda_id = _getter("fayda_id")

    if not fayda_id:
        frappe.throw(frappe._("fayda_id is required"))

    client = OpenG2PConsentClient()
    farmer_db_id = client.get_farmer_by_fayda_id(fayda_id)
    
    if not farmer_db_id:
        frappe.throw(frappe._("Farmer with Fayda ID '{0}' not found in OpenG2P.").format(fayda_id), frappe.DoesNotExistError)

    # Optionally fetch basic profile to show who it is
    farmer_records = client._admin_search_read(
        "res.partner",
        [["id", "=", farmer_db_id]],
        ["id", "name", "mobile", "phone"]
    )
    
    farmer_data = farmer_records[0] if farmer_records else {"id": farmer_db_id}

    return success_response(
        data={
            "farmer_db_id": farmer_db_id,
            "farmer": farmer_data
        },
        message="Farmer found successfully."
    )

@frappe.whitelist(allow_guest=False)
@handle_api_errors
def request_otp(**kwargs):
	"""
	NEW FLOW — Step 2: Request OTP and Create Consent in OpenG2P.

	Attachment Inputs (Mutually Exclusive):
	  - Option A: Pre-uploaded file URL via 'consent_form_attachment'.
	  - Option B: Raw base64 upload via 'consent_form_base64' and 'consent_form_filename'.
	"""
    _getter = _parse_request(kwargs)

    fayda_id                = _getter("fayda_id")
    partner                 = _getter("partner")
    lead_id                 = _getter("lead_id")
    purpose                 = _getter("purpose", "Loan for seeds and fertilizer")
    validity_from           = _getter("validity_from")
    validity_to             = _getter("validity_to")
    consent_form_attachment = _getter("consent_form_attachment")

    if not lead_id:
        frappe.throw(frappe._("lead_id is required"))
    if not frappe.db.exists("A2C Lead", lead_id):
        frappe.throw(frappe._("A2C Lead {0} not found").format(lead_id), frappe.DoesNotExistError)

    frappe.has_permission("A2C Lead", "write", doc=lead_id, throw=True)

    if not fayda_id:
        frappe.throw(frappe._("fayda_id is required"))
    if not partner:
        frappe.throw(frappe._("partner is required"))

    client = OpenG2PConsentClient()

    farmer_db_id = client.get_farmer_by_fayda_id(fayda_id)
    if not farmer_db_id:
        frappe.throw(frappe._("Farmer with Fayda ID '{0}' not found in OpenG2P.").format(fayda_id))

    partner_id_openg2p = client.get_partner_id(partner)
    if not partner_id_openg2p:
        frappe.throw(frappe._("Partner '{0}' not found in OpenG2P.").format(partner))

    allowed_data_field_ids = client.get_partner_allowed_data_field_ids(partner_id_openg2p)
    if not allowed_data_field_ids:
        allowed_data_field_ids = []

    consent_form_filename   = _getter("consent_form_filename")
    consent_form_base64     = _getter("consent_form_base64")

    if not validity_from or not validity_to:
        validity_from = now_datetime().strftime("%Y-%m-%d %H:%M:%S")
        validity_to   = add_to_date(now_datetime(), years=1).strftime("%Y-%m-%d %H:%M:%S")

    # 1. Save attachment locally in Frappe if base64 provided
    if consent_form_filename and consent_form_base64:
        import base64
        from frappe.utils.file_manager import save_file
        
        b64_data = consent_form_base64
        if "," in b64_data:
            b64_data = b64_data.split(",", 1)[1]
            
        file_content = base64.b64decode(b64_data)
        saved_file = save_file(
            fname=consent_form_filename,
            content=file_content,
            dt="A2C Lead",
            dn=lead_id if lead_id else None,
            is_private=1
        )
		consent_form_attachment = saved_file.file_url
	elif not consent_form_attachment:
		frappe.throw(
			frappe._("An attachment is strictly required. Please provide 'consent_form_base64' and 'consent_form_filename'."),
			frappe.ValidationError
		)

    # 2. Trigger Odoo OTP (which hits Fayda)
    try:
        otp_response = client.request_otp(farmer_id=farmer_db_id)
        odoo_session_id = None
        for cookie in client.session.cookies:
            if cookie.name == "session_id":
                odoo_session_id = cookie.value
                break
    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "OTP Request Failure")
        frappe.throw(frappe._("Failed to request OTP from the identity provider."))

    otp_data = otp_response.get("data") or {}
    transaction_id = otp_data.get("transaction_id")
    masked_phone = otp_data.get("masked_mobile") or "XXXX"

    if not transaction_id:
        frappe.log_error(f"OTP sent but no transaction_id returned: {otp_response}", "OTP Request Error")
        frappe.throw(frappe._("Identity provider did not return a transaction ID."))

    import requests
    cookie_dict = requests.utils.dict_from_cookiejar(client.session.cookies)
    frappe.logger().debug(f"Storing Odoo session dict: {cookie_dict} for transaction_id: {transaction_id}")
    if cookie_dict:
        frappe.cache().set_value(f"odoo_session_dict_{transaction_id}", cookie_dict, expires_in_sec=1800)

    # 3. Create Frappe Consent Request (Pending Odoo Creation)
    try:
        doc = frappe.new_doc("A2C Consent Request")
        doc.lead                    = lead_id
        doc.farmer_fayda_id         = fayda_id
        doc.partner                 = partner
        doc.consent_type            = "specific"
        doc.purpose                 = purpose
        doc.validity_from           = validity_from
        doc.validity_to             = validity_to
        doc.consent_form_attachment = consent_form_attachment
        doc.otp_transaction_id      = transaction_id
        doc.status                  = "Pending OTP"
            
        doc.insert(ignore_permissions=False)
        frappe.db.commit()
        consent_request_name = doc.name
            
    except Exception as e:
        frappe.log_error(f"Consent Request Creation Failed: {str(e)}", "Consent Request Error")
        consent_request_name = None

    return success_response(
        data={
            "consent_request": consent_request_name,
            "transaction_id": transaction_id,
            "masked_phone": masked_phone
        },
        message="OTP sent successfully. Proceed to verify OTP."
    )


@frappe.whitelist(allow_guest=False)
@handle_api_errors
def verify_otp_for_lead(**kwargs):
    """
    NEW FLOW — Step 3: Verify OTP and save consent to the A2C Lead.
    """
    _getter = _parse_request(kwargs)

    lead_id  = _getter("lead_id")
    otp_code = _getter("otp_code")

    if not lead_id:
        frappe.throw(frappe._("lead_id is required"))
    if not otp_code:
        frappe.throw(frappe._("otp_code is required"))

    frappe.has_permission("A2C Lead", "write", doc=lead_id, throw=True)
    lead_doc = frappe.get_doc("A2C Lead", lead_id)
    
    # Find the latest pending A2C Consent Request for this lead
    consent_request = frappe.db.get_value(
        "A2C Consent Request",
        {"lead": lead_id, "status": "Pending OTP"},
        "name",
        order_by="creation desc"
    )
    
    if not consent_request:
        frappe.throw(frappe._("No pending A2C Consent Request found for Lead '{0}'. Did you call request_otp first?").format(lead_id))

    frappe.has_permission("A2C Consent Request", "write", doc=consent_request, throw=True)
    cr_doc = frappe.get_doc("A2C Consent Request", consent_request)
    fayda_id                = cr_doc.farmer_fayda_id
    transaction_id          = cr_doc.otp_transaction_id
    partner                 = cr_doc.partner
    purpose                 = cr_doc.purpose
    consent_form_attachment = cr_doc.consent_form_attachment
    
    validity_from = cr_doc.validity_from.strftime("%Y-%m-%d %H:%M:%S") if cr_doc.validity_from else None
    validity_to   = cr_doc.validity_to.strftime("%Y-%m-%d %H:%M:%S") if cr_doc.validity_to else None

    if not transaction_id:
        frappe.throw(frappe._("OTP was not requested for this consent"))

    # Restore Odoo session cookie to match request_otp context
    cookie_dict = frappe.cache().get_value(f"odoo_session_dict_{transaction_id}")
    if cookie_dict:
        frappe.logger().debug(f"Retrieved Odoo cookie_dict: {cookie_dict} for transaction_id: {transaction_id}")
        client = OpenG2PConsentClient(cookie_dict=cookie_dict)
    else:
        # Fallback for sessions created before the dict logic was deployed
        odoo_session_id = frappe.cache().get_value(f"odoo_session_{transaction_id}")
        frappe.logger().debug(f"Retrieved fallback Odoo session_id: {odoo_session_id} for transaction_id: {transaction_id}")
        client = OpenG2PConsentClient(portal_session_id=odoo_session_id)

    farmer_db_id = client.get_farmer_by_fayda_id(fayda_id)
    if not farmer_db_id:
        frappe.throw(frappe._("Farmer with Fayda ID '{0}' not found in OpenG2P.").format(fayda_id))

	# 1. Verify OTP with Odoo (Fayda)
	try:
		client.verify_otp(
			farmer_id=farmer_db_id,
			transaction_id=transaction_id,
			otp_code=otp_code
		)
	except Exception as e:
		frappe.log_error(frappe.get_traceback(), "OTP Verification Failure")
		frappe.throw(frappe._("OTP verification failed. Please check your code and try again."))

	# 2. Upload Attachment
	try:
		attachment_id = client.upload_consent_attachment(consent_form_attachment)
	except Exception as e:
		frappe.log_error(frappe.get_traceback(), "Attachment Upload Failure")
		frappe.throw(frappe._("Failed to upload consent attachment."))

	# 3. Create Consent Request in Odoo
	partner_id_openg2p = client.get_partner_id(partner)
	if not partner_id_openg2p:
		frappe.throw(frappe._("Partner '{0}' not found in OpenG2P.").format(partner))
		
	allowed_data_field_ids = client.get_partner_allowed_data_field_ids(partner_id_openg2p) or []
	
	try:
		consent_response = client.create_consent_request(
			partner_id=partner_id_openg2p,
			farmer_db_id=farmer_db_id,
			consent_type="specific",
			purpose=purpose,
			validity_from=validity_from,
			validity_to=validity_to,
			allowed_data_field_ids=allowed_data_field_ids,
			attachment_ids=attachment_id
		)
	except Exception as e:
		frappe.log_error(frappe.get_traceback(), "Consent Creation Failure")
		frappe.throw(frappe._("Failed to create consent request in the identity system."))

	consent_data_resp = consent_response.get("data") or {}
	openg2p_consent_id = consent_data_resp.get("consent_creation_request_id") or consent_data_resp.get("id")

	if not openg2p_consent_id:
		frappe.log_error(f"Consent created but no ID returned: {consent_response}", "Consent Creation Error")
		frappe.throw(frappe._("Consent request created but no identification ID was returned."))

    # 4. Approve Consent in OpenG2P
    try:
        client.approve_consent_request(openg2p_consent_id)
    except Exception as e:
        frappe.logger().warning(f"consent approval failed: {e}")

    # 5. Generate Receipt and Update Consent Request
    frappe.db.set_value("A2C Consent Request", consent_request, {
        "status":             "Approved",
        "otp_verified_at":    now_datetime(),
        "openg2p_consent_id": openg2p_consent_id,
    })
    frappe.db.commit()

    receipt = generate_consent_receipt(consent_request)
    enqueue_websub_delivery(receipt)

    frappe.db.set_value("A2C Consent Request", consent_request, {
        "consent_receipt": receipt.get("signature")
    })
    frappe.db.commit()

    # 4. Fetch Farmer Data from OpenG2P into the Lead
    farmer_preview, _unused = _fetch_and_save_farmer_data(
        client, fayda_id, "A2C Lead", lead_id, openg2p_consent_id
    )

    given_name  = farmer_preview.get("given_name", "")
    family_name = farmer_preview.get("family_name", "")
    mobile      = (farmer_preview.get("phone_no") or [""])[0] if isinstance(
        farmer_preview.get("phone_no"), list) else farmer_preview.get("phone_no", "")

    if given_name or family_name or mobile:
        try:
            la = frappe.get_doc("A2C Lead", lead_id)
            if given_name:
                la.first_name = given_name
            if family_name:
                la.last_name = family_name
            if mobile and not la.phone_number:
                la.phone_number = mobile
            la.save(ignore_permissions=False)
            frappe.db.commit()
        except Exception as e:
            frappe.logger().warning(f"could not save farmer name fields: {e}")

	return success_response(
		data={
			"lead_id": lead_id,
			"consent_request": consent_request,
			"openg2p_consent_id": openg2p_consent_id,
			"consent_receipt": receipt.get("signature"),
            "farmer_preview": farmer_preview
		},
		message="OTP verified. Consent approved and saved to Lead."
	)