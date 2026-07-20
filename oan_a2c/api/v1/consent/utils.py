import hashlib
import hmac
import json

import frappe
from frappe import _
from frappe.utils import now_datetime


def generate_consent_receipt(consent_request_name):
	secret_key = frappe.conf.get("secret_key")
	if not secret_key:
		frappe.throw(_("secret_key not found in site_config.json"))

	consent = frappe.get_doc("A2C Consent Request", consent_request_name)

	receipt_data = {
		"consent_request": consent.name,
		"fayda_id": consent.farmer_fayda_id,
		"partner": consent.partner,
		"lead": getattr(consent, "lead", None),
		"openg2p_consent_id": consent.openg2p_consent_id or None,
		"status": consent.status,
		"otp_verified_at": str(consent.otp_verified_at) if consent.otp_verified_at else None,
		"timestamp": str(now_datetime()),
	}

	payload_str = json.dumps(receipt_data, separators=(",", ":"), sort_keys=True)

	signature = hmac.new(secret_key.encode("utf-8"), payload_str.encode("utf-8"), hashlib.sha256).hexdigest()

	return {"receipt_data": receipt_data, "signature": signature}
