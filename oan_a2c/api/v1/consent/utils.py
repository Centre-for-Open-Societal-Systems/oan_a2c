import hashlib
import hmac
import json

import frappe
from frappe.utils import now_datetime

from oan_a2c.api.utils import to_tz_aware_iso


def generate_consent_receipt(consent_request_name):
	"""Signed receipt for a consent, or None when the site has no `secret_key`.

	Nothing consumes the receipt, so a site without the key skips it rather than failing the consent."""
	secret_key = frappe.conf.get("secret_key")
	if not secret_key:
		return None

	consent = frappe.get_doc("A2C Consent Request", consent_request_name)

	receipt_data = {
		"consent_request": consent.name,
		"fayda_id": consent.farmer_fayda_id,
		"partner": consent.partner,
		# The lead this consent was raised for, or None for a self-service consent.
		# Read from the reference pair rather than the lead's own consent_id cache:
		# a receipt is signed evidence, so it must reflect what THIS request was for,
		# not whichever attempt happens to be the lead's latest.
		"lead": (consent.reference_name if consent.reference_doctype == "A2C Lead" else None),
		"openg2p_consent_id": consent.openg2p_consent_id or None,
		"status": consent.status,
		"otp_verified_at": to_tz_aware_iso(consent.otp_verified_at) if consent.otp_verified_at else None,
		"timestamp": to_tz_aware_iso(now_datetime()),
	}

	payload_str = json.dumps(receipt_data, separators=(",", ":"), sort_keys=True)

	signature = hmac.new(secret_key.encode("utf-8"), payload_str.encode("utf-8"), hashlib.sha256).hexdigest()

	return {"receipt_data": receipt_data, "signature": signature}
