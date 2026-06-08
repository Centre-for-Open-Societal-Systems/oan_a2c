import os
import frappe
import requests
from frappe import _
from uuid import uuid4
from datetime import datetime


class OpenG2PConsentClient:
    def __init__(self):
        self.base_url = frappe.conf.get("openg2p_base_url")
        self.db = frappe.conf.get("openg2p_db", "openg2p")

        # Portal user — for consent creation
        self.username = frappe.conf.get("openg2p_username", "admin")
        self.password = frappe.conf.get("openg2p_password", "admin")

        # Admin user — for res.partner / g2p.reg.id lookups
        self.admin_username = frappe.conf.get("openg2p_admin_username", "admin")
        self.admin_password = frappe.conf.get("openg2p_admin_password", "admin")

        if not self.base_url:
            frappe.throw(_("OpenG2P Base URL is missing in site_config.json"))

        self.session = requests.Session()        # portal user session
        self.admin_session = requests.Session()  # admin session

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
            response = self.session.post(url, json=payload)
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

            return data.get("result")

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

    def get_partner_id(self, partner_name):
        """Fetch or auto-create the Odoo res.partner ID for the given partner name."""
        url = f"{self.base_url}/web/dataset/call_kw"
        try:
            result = self._admin_search_read(
                "res.partner",
                [["name", "=", partner_name]],
                ["id"]
            )
            if result:
                return result[0]["id"]

            create_payload = {
                "jsonrpc": "2.0",
                "method": "call",
                "params": {
                    "model": "res.partner",
                    "method": "create",
                    "args": [{"name": partner_name, "is_company": True}],
                    "kwargs": {}
                }
            }
            create_response = self.admin_session.post(url, json=create_payload)
            create_data = create_response.json()
            if "error" in create_data:
                frappe.throw(_("Failed to create partner in Odoo: {0}").format(str(create_data["error"])))
            return create_data.get("result")

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

    def create_consent_request(self, partner_id, farmer_db_id, consent_type, purpose,
                                validity_from, validity_to, allowed_data_field_ids,
                                attachment_ids=None):
        params = {
            "partner_id": partner_id,
            "farmer_db_id": farmer_db_id,
            "consent_type": consent_type,
            "purpose": purpose,
            "validity_from": validity_from,
            "validity_to": validity_to,
            "allowed_data_field_ids": allowed_data_field_ids
        }
        if attachment_ids:
            params["attachment_ids"] = attachment_ids if isinstance(attachment_ids, list) else [attachment_ids]

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

    def send_otp(self, farmer_id):
        """
        Call Fayda OTP API directly from Frappe.
        Stores transaction context in Frappe cache — no Odoo session dependency.
        """
        print(f">>>>>> send_otp (direct Fayda) | farmer_id: {farmer_id}")

        identifier = self._get_farmer_fayda_identifier(farmer_id)
        if not identifier:
            frappe.throw(_("Farmer has no Fayda identifier configured."))

        transaction_id = self._make_transaction_id()
        url = f"{self.fayda_base_url}/requestData"

        payload = {
            "id": self.fayda_client_id,
            "clientSecret": self.fayda_client_secret,
            "version": self.fayda_version,
            "requestTime": self._now_iso_millis(),
            "env": self.fayda_env,
            "domainUri": self.fayda_domain_uri,
            "transactionID": transaction_id,
            "individualId": identifier,
            "individualIdType": self.fayda_identifier_type,
            "otpChannel": [self.fayda_channel],
        }

        print(f">>>>>> Calling Fayda /requestData: {url}")
        print(f">>>>>> Fayda payload: {payload}")

        try:
            resp = requests.post(
                url,
                json=payload,
                headers={"Content-Type": "application/json", "Accept": "application/json"},
                timeout=20
            )
            resp.raise_for_status()
            response_payload = resp.json()
        except requests.exceptions.RequestException as e:
            frappe.throw(_("Fayda OTP request failed: {0}").format(str(e)))

        print(f">>>>>> Fayda /requestData response: {response_payload}")

        errors = response_payload.get("errors")
        if errors:
            frappe.throw(_("Fayda OTP Error: {0}").format(str(errors)))

        response_data = response_payload.get("response") or {}
        masked_mobile = (response_data.get("maskedMobile") or "").strip()

        # Store OTP context in Frappe cache (10 min TTL)
        cache_key = f"fayda_otp_{transaction_id}"
        frappe.cache().set_value(cache_key, {
            "farmer_id": farmer_id,
            "identifier": identifier,
            "identifier_type": self.fayda_identifier_type,
            "masked_mobile": masked_mobile,
            "transaction_id": transaction_id,
        }, expires_in_sec=600)

        print(f">>>>>> OTP requested. transaction_id: {transaction_id}, masked_mobile: {masked_mobile}")

        return {
            "success": True,
            "transaction_id": transaction_id,
            "masked_mobile": masked_mobile,
            "data": {
                "transaction_id": transaction_id,
                "masked_mobile": masked_mobile,
            }
        }

    def verify_otp(self, farmer_id, transaction_id, otp_code):
        """
        Verify OTP directly with Fayda from Frappe.
        Reads context from Frappe cache — no Odoo session needed.
        """
        print(f">>>>>> verify_otp (direct Fayda) | farmer_id: {farmer_id}, transaction_id: {transaction_id}")

        # Load OTP context from Frappe cache
        cache_key = f"fayda_otp_{transaction_id}"
        cached = frappe.cache().get_value(cache_key)
        print(f">>>>>> Cached OTP context: {cached}")

        if not cached:
            frappe.throw(_("OTP session expired or not found. Please request a new OTP."))

        if cached.get("farmer_id") != farmer_id:
            frappe.throw(_("OTP session does not match the selected farmer."))

        identifier = cached.get("identifier", "")
        identifier_type = cached.get("identifier_type", self.fayda_identifier_type)

        url = f"{self.fayda_base_url}/getDataAuth"
        verify_time = self._now_iso_millis()

        payload = {
            "id": self.fayda_client_id,
            "clientSecret": self.fayda_client_secret,
            "version": self.fayda_version,
            "requestTime": verify_time,
            "env": self.fayda_env,
            "domainUri": self.fayda_domain_uri,
            "transactionID": transaction_id,
            "requestedAuth": {
                "otp": True,
                "demo": False,
                "bio": False,
            },
            "consentObtained": True,
            "individualId": identifier,
            "individualIdType": identifier_type,
            "thumbprint": self.fayda_thumbprint,
            "requestSessionKey": self.fayda_request_session_key,
            "requestHMAC": self.fayda_request_hmac,
            "request": {
                "timestamp": verify_time,
                "otp": otp_code,
            },
        }

        print(f">>>>>> Calling Fayda /getDataAuth: {url}")
        print(f">>>>>> Fayda verify payload: {payload}")

        try:
            resp = requests.post(
                url,
                json=payload,
                headers={"Content-Type": "application/json", "Accept": "application/json"},
                timeout=20
            )
            resp.raise_for_status()
            response_payload = resp.json()
        except requests.exceptions.RequestException as e:
            frappe.throw(_("Fayda OTP verification failed: {0}").format(str(e)))

        print(f">>>>>> Fayda /getDataAuth response: {response_payload}")

        errors = response_payload.get("errors")
        response_data = response_payload.get("response") or {}
        auth_status = bool(response_data.get("authStatus"))

        if errors or not auth_status:
            msg = str(errors) if errors else "OTP verification failed."
            frappe.throw(_("OTP verification failed: {0}").format(msg))

        # Clear cache after successful verification
        frappe.cache().delete_value(cache_key)

        verified_at = frappe.utils.now_datetime().strftime("%Y-%m-%d %H:%M:%S")
        print(f">>>>>> OTP verified successfully at {verified_at}")

        return {
            "success": True,
            "transaction_id": transaction_id,
            "masked_mobile": cached.get("masked_mobile", ""),
            "verified_at": verified_at,
        }

    # -------------------------------------------------------------------------
    # Attachment upload — uses admin session
    # -------------------------------------------------------------------------

    def upload_attachment(self, filename, file_content_base64, mimetype="application/pdf"):
        url = f"{self.base_url}/web/dataset/call_kw"
        payload = {
            "jsonrpc": "2.0",
            "method": "call",
            "params": {
                "model": "ir.attachment",
                "method": "create",
                "args": [{
                    "name": filename,
                    "datas": file_content_base64,
                    "mimetype": mimetype,
                    "res_model": "g2p.consent.request"
                }],
                "kwargs": {}
            }
        }
        try:
            response = self.admin_session.post(url, json=payload)
            data = response.json()
            if "error" in data:
                frappe.throw(_("Failed to upload attachment: {0}").format(
                    data["error"].get("data", {}).get("message", str(data["error"]))
                ))
            return data.get("result")
        except requests.exceptions.RequestException as e:
            frappe.throw(_("Failed to upload attachment to OpenG2P: {0}").format(str(e)))

    def upload_consent_attachment(self, file_url):
        """Read file from Frappe disk and upload to OpenG2P."""
        import base64
        import os

        site_path = frappe.get_site_path()
        file_path = f"{site_path}/public{file_url}"
        if not os.path.exists(file_path):
            file_path = f"{site_path}/private{file_url}"

        print(f">>>>>> Reading attachment from: {file_path}")

        with open(file_path, "rb") as f:
            file_content_b64 = base64.b64encode(f.read()).decode("utf-8")

        filename = file_url.split("/")[-1]
        return self.upload_attachment(filename=filename, file_content_base64=file_content_b64)