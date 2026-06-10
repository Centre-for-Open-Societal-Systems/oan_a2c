import os
import frappe
import requests
from frappe import _
from uuid import uuid4
from datetime import datetime


class OpenG2PConsentClient:
    def __init__(self, portal_session_id=None):
        self.base_url = frappe.conf.get("openg2p_base_url")
        self.db = frappe.conf.get("openg2p_db", "openg2p")

        # Portal user — for consent creation
        self.username = frappe.conf.get("openg2p_username", "megha")
        self.password = frappe.conf.get("openg2p_password", "megha")

        # Admin user — for res.partner / g2p.reg.id lookups
        self.admin_username = frappe.conf.get("openg2p_admin_username", "admin")
        self.admin_password = frappe.conf.get("openg2p_admin_password", "admin")

        if not self.base_url:
            frappe.throw(_("OpenG2P Base URL is missing in site_config.json"))

        self.session = requests.Session()        # portal user session
        self.admin_session = requests.Session()  # admin session
        self.portal_session_id = portal_session_id

        if portal_session_id:
            self.session.cookies.set("session_id", portal_session_id)
        else:
            self._authenticate(self.session, self.username, self.password)
            
        self._authenticate(self.admin_session, self.admin_username, self.admin_password)

        # Fayda direct config (same env vars Odoo uses)
        self.fayda_base_url = (
            frappe.conf.get("fayda_otp_base_url")
            or os.getenv("G2P_FAYDA_OTP_BASE_URL")
            or f"{self.base_url}"  # fallback to openg2p host
        ).rstrip("/")
        self.fayda_client_id = (
            frappe.conf.get("fayda_client_id")
            or os.getenv("G2P_FAYDA_OTP_CLIENT_ID")
            or os.getenv("MOCK_FAYDA_CLIENT_ID")
            or "demo-client"
        )
        self.fayda_client_secret = (
            frappe.conf.get("fayda_client_secret")
            or os.getenv("G2P_FAYDA_OTP_CLIENT_SECRET")
            or os.getenv("MOCK_FAYDA_CLIENT_SECRET")
            or "demo-secret"
        )
        self.fayda_env = (
            frappe.conf.get("fayda_env")
            or os.getenv("G2P_FAYDA_OTP_ENV")
            or os.getenv("MOCK_FAYDA_ENV")
            or "prod"
        )
        self.fayda_domain_uri = (
            frappe.conf.get("fayda_domain_uri")
            or os.getenv("G2P_FAYDA_OTP_DOMAIN_URI")
            or os.getenv("MOCK_FAYDA_DOMAIN_URI")
            or "fayda.et"
        )
        self.fayda_channel = (
            frappe.conf.get("fayda_channel")
            or os.getenv("G2P_FAYDA_OTP_CHANNEL")
            or "phone"
        )
        self.fayda_identifier_type = (
            frappe.conf.get("fayda_identifier_type")
            or os.getenv("G2P_FAYDA_OTP_ID_TYPE")
            or "FIN"
        ).upper()
        self.fayda_version = (
            frappe.conf.get("fayda_version")
            or os.getenv("G2P_FAYDA_OTP_VERSION")
            or "1.0"
        )
        self.fayda_thumbprint = (
            frappe.conf.get("fayda_thumbprint")
            or os.getenv("G2P_FAYDA_OTP_THUMBPRINT")
            or ""
        )
        self.fayda_request_session_key = (
            frappe.conf.get("fayda_request_session_key")
            or os.getenv("G2P_FAYDA_OTP_REQUEST_SESSION_KEY")
            or ""
        )
        self.fayda_request_hmac = (
            frappe.conf.get("fayda_request_hmac")
            or os.getenv("G2P_FAYDA_OTP_REQUEST_HMAC")
            or ""
        )

    # -------------------------------------------------------------------------
    # Internal helpers
    # -------------------------------------------------------------------------

    def _now_iso_millis(self):
        return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3]

    def _make_transaction_id(self):
        return uuid4().hex.upper()

    def _authenticate(self, session, username, password):
        url = f"{self.base_url}/web/session/authenticate"
        payload = {
            "jsonrpc": "2.0",
            "method": "call",
            "params": {
                "db": self.db,
                "login": username,
                "password": password
            }
        }
        try:
            response = session.post(url, json=payload)
            response.raise_for_status()
            result = response.json()
            if result.get("result", {}).get("uid"):
                return
            frappe.throw(_("OpenG2P authentication failed for user: {0}").format(username))
        except requests.exceptions.RequestException as e:
            frappe.throw(_("Failed to connect to OpenG2P: {0}").format(str(e)))

    def _call_rpc(self, endpoint, method, params):
        url = f"{self.base_url}{endpoint}"
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": method,
            "params": params
        }
        try:
            session_id = None
            if hasattr(self, "portal_session_id") and self.portal_session_id:
                session_id = self.portal_session_id
            else:
                session_id = self.session.cookies.get("session_id")

            headers = {"Content-Type": "application/json"}
            if session_id:
                # Explicitly pass session_id to guarantee Odoo receives it
                headers["X-Openerp-Session-Id"] = session_id
                headers["Cookie"] = f"session_id={session_id}"

            print(f">>>>>> [DEBUG RPC] Sending to {endpoint} with session_id: {session_id}")
            response = self.session.post(url, json=payload, headers=headers)
            response.raise_for_status()
            data = response.json()

            if "error" in data:
                error_data = data["error"]
                if isinstance(error_data, dict):
                    error_msg = (
                        error_data.get("data", {}).get("message")
                        or error_data.get("message")
                        or str(error_data)
                    )
                else:
                    error_msg = str(error_data)
                frappe.throw(_("OpenG2P Error: {0}").format(error_msg))

            result = data.get("result")
            if isinstance(result, dict) and result.get("success") is False:
                frappe.throw(_("OpenG2P Error: {0}").format(result.get("message") or "Unknown error"))

            return result

        except requests.exceptions.RequestException as e:
            frappe.throw(_("Failed to connect to OpenG2P: {0}").format(str(e)))
        except Exception as e:
            frappe.throw(_("OpenG2P Error: {0}").format(str(e)))

    def _admin_search_read(self, model, domain, fields, limit=1):
        """Run a search_read using the admin session."""
        url = f"{self.base_url}/web/dataset/call_kw"
        payload = {
            "jsonrpc": "2.0",
            "method": "call",
            "params": {
                "model": model,
                "method": "search_read",
                "args": [domain],
                "kwargs": {"fields": fields, "limit": limit}
            }
        }
        response = self.admin_session.post(url, json=payload)
        data = response.json()
        if "error" in data:
            print(f">>>>>> admin_search_read error on {model}: {data['error']}")
            return []
        return data.get("result", [])

    # -------------------------------------------------------------------------
    # Farmer lookup — uses admin session
    # -------------------------------------------------------------------------

    def get_farmer_by_fayda_id(self, fayda_id):
        """
        Find farmer's res.partner ID by Fayda/national ID.
        Tries: unique_id field, then g2p.reg.id value.
        """
        base_domain = [("is_registrant", "=", True), ("is_group", "=", False)]

        # Try 1: unique_id field
        try:
            result = self._admin_search_read(
                "res.partner",
                base_domain + [("unique_id", "=", fayda_id)],
                ["id", "name"]
            )
            if result:
                print(f">>>>>> Found farmer by unique_id: {result[0]}")
                return result[0]["id"]
        except Exception as e:
            print(f">>>>>> unique_id search failed: {str(e)}")

        # Try 2: g2p.reg.id value
        try:
            reg_result = self._admin_search_read(
                "g2p.reg.id",
                [("value", "=", fayda_id)],
                ["id", "partner_id"]
            )
            if reg_result:
                partner = reg_result[0].get("partner_id")
                if isinstance(partner, (list, tuple)):
                    print(f">>>>>> Found farmer by reg_id: {partner}")
                    return partner[0]
        except Exception as e:
            print(f">>>>>> reg_id search failed: {str(e)}")

        print(f">>>>>> Farmer with Fayda ID '{fayda_id}' not found")
        return None

    def _get_farmer_fayda_identifier(self, farmer_db_id):
        """
        Get the Fayda identifier (FIN/UID) value for a farmer from Odoo reg_ids.
        Uses admin session.
        """
        id_type_map = {"FIN": "uid", "RID": "rid"}
        preferred_type = id_type_map.get(self.fayda_identifier_type, self.fayda_identifier_type).lower()

        reg_result = self._admin_search_read(
            "g2p.reg.id",
            [("partner_id", "=", farmer_db_id)],
            ["id", "value", "id_type"],
            limit=50
        )
        print(f">>>>>> farmer {farmer_db_id} reg_ids: {reg_result}")

        for reg in reg_result:
            id_type = reg.get("id_type", [])
            type_name = (
                id_type[1] if isinstance(id_type, (list, tuple)) else str(id_type)
            ).lower()
            if preferred_type in type_name or type_name in preferred_type:
                val = reg.get("value", "").strip()
                if val:
                    print(f">>>>>> Found Fayda identifier: {val} (type: {type_name})")
                    return val

        # Fallback: return first available value
        if reg_result:
            val = reg_result[0].get("value", "").strip()
            print(f">>>>>> Fallback Fayda identifier: {val}")
            return val

        return ""

    # -------------------------------------------------------------------------
    # Partner lookup — uses admin session
    # -------------------------------------------------------------------------

    def get_partner_id(self, partner_name=None):
        """Fetch the consent_parent_partner_id of the API user. Odoo strictly requires this ID."""
        try:
            result = self._admin_search_read(
                "res.users",
                [["login", "=", self.username]],
                ["consent_parent_partner_id"]
            )
            if result and result[0].get("consent_parent_partner_id"):
                return result[0]["consent_parent_partner_id"][0]
            
            # Fallback if not found
            return None
        except requests.exceptions.RequestException as e:
            frappe.throw(_("Failed to connect to OpenG2P: {0}").format(str(e)))

    def get_partner_allowed_data_field_ids(self, partner_record_id):
        """Fetch allowed_data_field_ids for a consent partner."""
        try:
            result = self._admin_search_read(
                "res.partner",
                [["id", "=", partner_record_id]],
                ["id", "allowed_data_field_ids"]
            )
            if result:
                ids = result[0].get("allowed_data_field_ids", [])
                print(f">>>>>> Partner {partner_record_id} allowed_data_field_ids: {ids}")
                return ids
            return []
        except Exception as e:
            print(f">>>>>> Failed to fetch allowed_data_field_ids: {str(e)}")
            return []

    # -------------------------------------------------------------------------
    # Consent Partners
    # -------------------------------------------------------------------------

    def get_consent_partners(self):
        """Fetch all Consent Partners from OpenG2P using admin session."""
        url = f"{self.base_url}/web/dataset/call_kw"
        payload = {
            "jsonrpc": "2.0",
            "method": "call",
            "params": {
                "model": "g2p.consent.partner",
                "method": "search_read",
                "args": [[]],
                "kwargs": {"fields": ["id", "name", "partner_id"], "limit": 20}
            }
        }
        try:
            response = self.admin_session.post(url, json=payload)
            data = response.json()
            if "error" in data:
                print(f">>>>>> Error fetching consent partners: {data['error']}")
                return []
            result = data.get("result", [])
            print(f">>>>>> Found {len(result)} Consent Partners")
            return result
        except Exception as e:
            print(f">>>>>> Failed to fetch consent partners: {str(e)}")
            return []

    # -------------------------------------------------------------------------
    # Consent creation — uses portal session
    # -------------------------------------------------------------------------

    def upload_attachment(self, attachment_base64, attachment_filename):
        """Uploads an attachment to Odoo and returns the attachment_id"""
        params = {
            "attachment_base64": attachment_base64,
            "attachment_filename": attachment_filename
        }
        print(f">>>>>> Uploading attachment to OpenG2P: {attachment_filename}")
        result = self._call_rpc("/api/consent/attachment/upload", "call", params)
        print(f">>>>>> OpenG2P attachment response: {result}")
        
        # OpenG2P returns {"success": True, "data": {"attachment_id": 123}}
        if not result or not result.get("success"):
            frappe.throw(frappe._("OpenG2P Attachment Upload Failed: {0}").format(result.get("message")))
        
        return result.get("data", {}).get("attachment_id")

    def create_consent_request(self, partner_id, farmer_db_id, consent_type, purpose,
                                validity_from, validity_to, allowed_data_field_ids,
                                attachment_ids=None, attachment_base64=None, attachment_filename=None):
        
        # If base64 is provided, upload it first to get the attachment_id
        if attachment_base64:
            uploaded_id = self.upload_attachment(attachment_base64, attachment_filename or "consent.pdf")
            if uploaded_id:
                attachment_ids = [uploaded_id]

        params = {
            "partner_id": partner_id,
            "farmer_db_id": farmer_db_id,
            "consent_type": consent_type,
            "purpose": purpose,
            "validity_from": validity_from,
            "validity_to": validity_to,
            "allowed_data_field_ids": allowed_data_field_ids,
            "originated_from": "partner"
        }
        if attachment_ids:
            params["attachment_ids"] = attachment_ids if not isinstance(attachment_ids, list) else attachment_ids[0]

        print(f">>>>>> Sending consent to OpenG2P: {params}")
        result = self._call_rpc("/api/consent/request/create", "call", params)
        print(f">>>>>> OpenG2P consent response: {result}")
        return result

    def approve_consent_request(self, consent_creation_request_id):
        params = {
            "consent_creation_request_id": consent_creation_request_id
        }
        print(f">>>>>> Approving consent in OpenG2P: {params}")
        result = self._call_rpc("/api/consent/request/approve", "call", params)
        print(f">>>>>> OpenG2P approve response: {result}")
        return result

    # -------------------------------------------------------------------------
    # OTP — calls Fayda directly (bypasses Odoo session dependency)
    # -------------------------------------------------------------------------

    def request_otp(self, farmer_id):
        """
        Calls Odoo's new /consent/fayda/request_otp endpoint.
        """
        params = {"farmer_id": farmer_id}
        print(f">>>>>> Calling Odoo /consent/fayda/request_otp: {params}")
        result = self._call_rpc("/consent/fayda/request_otp", "call", params)
        print(f">>>>>> Odoo request_otp response: {result}")
        return result

    def verify_otp(self, farmer_id, transaction_id, otp_code):
        """
        Calls Odoo's new /consent/fayda/verify_otp endpoint.
        """
        params = {
            "farmer_id": int(farmer_id),
            "transaction_id": transaction_id,
            "otp_code": str(otp_code)
        }
        print(f">>>>>> Calling Odoo /consent/fayda/verify_otp: {params}")
        result = self._call_rpc("/consent/fayda/verify_otp", "call", params)
        print(f">>>>>> Odoo verify_otp response: {result}")
        return result

    # -------------------------------------------------------------------------
    # Attachment upload — uses admin session
    # -------------------------------------------------------------------------



    def upload_consent_attachment(self, file_url):
        """Read file from Frappe disk and upload to OpenG2P."""
        import base64
        import os

        site_path = frappe.get_site_path()
        # If the file_url already starts with /private or /public, just strip the leading slash
        # otherwise prepend /public
        if file_url.startswith("/private/"):
            file_path = os.path.join(site_path, file_url.lstrip("/"))
        elif file_url.startswith("/public/"):
            file_path = os.path.join(site_path, file_url.lstrip("/"))
        else:
            file_path = f"{site_path}/public{file_url}"
            if not os.path.exists(file_path):
                file_path = f"{site_path}/private{file_url}"

        print(f">>>>>> Reading attachment from: {file_path}")

        with open(file_path, "rb") as f:
            file_content_b64 = base64.b64encode(f.read()).decode("utf-8")

        filename = os.path.basename(file_path)
        return self.upload_attachment(attachment_base64=file_content_b64, attachment_filename=filename)