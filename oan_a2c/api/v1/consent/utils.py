import frappe
import hmac
import hashlib
import json
import requests as http_requests
from frappe.utils import now_datetime


def generate_consent_receipt(consent_request_name):
    secret_key = frappe.conf.get("secret_key")
    if not secret_key:
        frappe.throw("secret_key not found in site_config.json")

    consent = frappe.get_doc("A2C Consent Request", consent_request_name)

    receipt_data = {
        "consent_request": consent.name,
        "fayda_id": consent.farmer_fayda_id,
        "partner": consent.partner,
        "lead": getattr(consent, "lead", None),
        "openg2p_consent_id": consent.openg2p_consent_id or None,
        "status": consent.status,
        "otp_verified_at": str(consent.otp_verified_at) if consent.otp_verified_at else None,
        "timestamp": str(now_datetime())
    }

    payload_str = json.dumps(receipt_data, separators=(',', ':'), sort_keys=True)

    signature = hmac.new(
        secret_key.encode('utf-8'),
        payload_str.encode('utf-8'),
        hashlib.sha256
    ).hexdigest()

    return {
        "receipt_data": receipt_data,
        "signature": signature
    }


def enqueue_websub_delivery(receipt):
    consent_request_name = receipt["receipt_data"]["consent_request"]
    frappe.enqueue(
        "oan_a2c.api.v1.consent.utils.deliver_websub_payload",
        receipt=receipt,
        consent_request_name=consent_request_name,
        queue="default"
    )


def deliver_websub_payload(receipt, consent_request_name):
    # Set user context
    owner = frappe.db.get_value("A2C Consent Request", consent_request_name, "owner")
    # TODO: This fallback to "Administrator" will be changed to fail/raise an exception if owner is not present
    user_to_set = owner if owner and frappe.db.exists("User", owner) else "Administrator"
    frappe.set_user(user_to_set)

    frappe.logger().info(f"Delivering WebSub payload for {consent_request_name}")

    try:
        consent = frappe.get_doc("A2C Consent Request", consent_request_name)
        openg2p_base_url = frappe.conf.get("openg2p_base_url")
        openg2p_db = frappe.conf.get("openg2p_db")
        openg2p_username = frappe.conf.get("openg2p_username")
        openg2p_password = frappe.conf.get("openg2p_password")

        frappe.logger().info(f"openg2p_consent_id: {consent.openg2p_consent_id}")

        if openg2p_base_url and consent.openg2p_consent_id:
            session = http_requests.Session()
            auth_resp = session.post(
                f"{openg2p_base_url}/web/session/authenticate",
                json={
                    "jsonrpc": "2.0",
                    "method": "call",
                    "params": {
                        "db": openg2p_db,
                        "login": openg2p_username,
                        "password": openg2p_password,
                    }
                },
                timeout=10
            )
            auth_resp.raise_for_status()
            frappe.logger().info(f"Odoo auth response: {auth_resp.status_code}")

            resp = session.post(
                f"{openg2p_base_url}/consent/frappe/otp_verified",
                json={
                    "consent_creation_request_id": consent.openg2p_consent_id,
                    "fayda_otp_transaction_id": consent.otp_transaction_id or "",
                    "fayda_otp_verified_at": str(consent.otp_verified_at) if consent.otp_verified_at else "",
                },
                timeout=10
            )
            resp.raise_for_status()
            frappe.logger().info(f"Odoo callback response: {resp.status_code} {resp.text[:500]}")
        else:
            frappe.logger().warning(f"Skipping Odoo callback: base_url={openg2p_base_url} consent_id={consent.openg2p_consent_id}")

        frappe.db.set_value("A2C Consent Request", consent_request_name, "websub_delivered", 1)
        frappe.db.set_value("A2C Consent Request", consent_request_name, "websub_delivered_at", now_datetime())
        frappe.db.commit()
    except Exception as e:
        frappe.db.rollback()
        frappe.log_error(frappe.get_traceback(), f"WebSub Delivery Failed for {consent_request_name}")
        raise e

