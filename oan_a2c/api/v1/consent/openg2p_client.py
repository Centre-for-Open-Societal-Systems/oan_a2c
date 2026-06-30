import frappe
import requests
from frappe import _
from pydantic import BaseModel, Field, ValidationError
from typing import Optional, Union


class OpenG2PResponse(BaseModel):
    success: bool
    message: Optional[str] = None


class OpenG2PFarmerSchema(BaseModel):
    id: Union[int, str]
    name: str
    mobile: Optional[str] = None
    phone: Optional[str] = None


class OpenG2PSearchFarmerResponse(OpenG2PResponse):
    data: dict[str, list[OpenG2PFarmerSchema]] = Field(default_factory=dict)


class OpenG2POTPData(BaseModel):
    transaction_id: str
    masked_mobile: Optional[str] = "XXXX"


class OpenG2POTPResponse(OpenG2PResponse):
    data: OpenG2POTPData


class OpenG2PSubmitConsentData(BaseModel):
    consent_request_id: Optional[Union[int, str]] = None
    consent_id: Optional[Union[int, str]] = None
    consent_creation_request_id: Optional[Union[int, str]] = None
    id: Optional[Union[int, str]] = None


class OpenG2PSubmitConsentResponse(OpenG2PResponse):
    data: OpenG2PSubmitConsentData


class OpenG2PConsentClient:
    def __init__(self, portal_session_id=None, cookie_dict=None):
        self.base_url = frappe.conf.get("openg2p_base_url")
        self.db = frappe.conf.get("openg2p_db", "")

        # Portal user — for consent creation
        self.username = frappe.conf.get("openg2p_username", "")
        self.password = frappe.conf.get("openg2p_password", "")

        if not self.base_url:
            frappe.throw(_("OpenG2P Base URL is missing in site_config.json"))

        if not self.username or not self.password:
            frappe.throw(_("OpenG2P portal credentials (openg2p_username, openg2p_password) are missing in site_config.json"))

        self.session = requests.Session()        # portal user session
        self.portal_session_id = portal_session_id
        self.cookie_dict = cookie_dict

        if cookie_dict:
            requests.utils.cookiejar_from_dict(cookie_dict, cookiejar=self.session.cookies)
        elif portal_session_id:
            from urllib.parse import urlparse
            domain = urlparse(self.base_url).hostname
            self.session.cookies.set("session_id", portal_session_id, domain=domain)
        else:
            # Cache the portal authentication session
            cached_cookies = frappe.cache().get_value("openg2p_portal_session_cookies")
            if cached_cookies:
                requests.utils.cookiejar_from_dict(cached_cookies, cookiejar=self.session.cookies)
            else:
                self._authenticate(self.session, self.username, self.password)
                new_cookies = requests.utils.dict_from_cookiejar(self.session.cookies)
                if new_cookies:
                    frappe.cache().set_value("openg2p_portal_session_cookies", new_cookies, expires_in_sec=1800)

    # -------------------------------------------------------------------------
    # Internal helpers
    # -------------------------------------------------------------------------

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
            response = session.post(url, json=payload, timeout=(5, 10))
            response.raise_for_status()
            result = response.json()
            if result.get("result", {}).get("uid"):
                return
            frappe.throw(_("OpenG2P authentication failed for user: {0}").format(username))
        except requests.exceptions.RequestException as e:
            frappe.throw(_("Failed to connect to OpenG2P: {0}").format(str(e)))

    def _refresh_portal_session(self):
        """Clears cached session cookies, re-authenticates, and updates cache."""
        frappe.cache().delete_value("openg2p_portal_session_cookies")
        self.session.cookies.clear()
        self._authenticate(self.session, self.username, self.password)
        new_cookies = requests.utils.dict_from_cookiejar(self.session.cookies)
        if new_cookies:
            frappe.cache().set_value("openg2p_portal_session_cookies", new_cookies, expires_in_sec=1800)

    def _call_rpc(self, endpoint, method, params):
        url = f"{self.base_url}{endpoint}"
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": method,
            "params": params
        }
        try:
            session = self.session
            session_id = None
            if hasattr(self, "portal_session_id") and self.portal_session_id:
                session_id = self.portal_session_id
            else:
                for cookie in session.cookies:
                    if cookie.name == "session_id":
                        session_id = cookie.value
                        break

            headers = {"Content-Type": "application/json"}
            if session_id:
                headers["X-Openerp-Session-Id"] = session_id

            frappe.logger().debug(f"[DEBUG RPC] Sending to {endpoint}")
            
            req = requests.Request('POST', url, json=payload, headers=headers)
            prepared = session.prepare_request(req)
            
            response = session.send(prepared, timeout=(5, 10))
            response.raise_for_status()
            data = response.json()

            if "error" in data:
                err = data["error"]
                msg = err.get("data", {}).get("message") or err.get("message") or "Unknown OpenG2P Error"
                
                # Check for session expiration to retry transparently
                if not getattr(self, "cookie_dict", None) and not getattr(self, "portal_session_id", None) and any(x in msg.lower() for x in ["session", "expired", "authentication", "logged out", "access denied"]):
                    self._refresh_portal_session()
                    return self._call_rpc(endpoint, method, params)

                frappe.throw(_("OpenG2P Error: {0}").format(msg))

            result = data.get("result")
            if isinstance(result, dict) and result.get("success") is False:
                frappe.throw(_("OpenG2P Error: {0}").format(result.get("message") or "Unknown error"))

            return result

        except requests.exceptions.RequestException as e:
            frappe.throw(_("Failed to connect to OpenG2P: {0}").format(str(e)))


    # -------------------------------------------------------------------------
    # Farmer lookup — uses normal portal session
    # -------------------------------------------------------------------------

    def get_farmer_by_fayda_id(self, fayda_id):
        """
        Find farmer's res.partner ID by Fayda/national ID using the Odoo backend endpoint.
        """
        cache_key = f"farmer_by_fayda:{fayda_id}"
        cached = frappe.cache().get_value(cache_key)
        if cached:
            return cached

        params = {"query": fayda_id}
        frappe.logger().debug("Calling Odoo /consent/search_farmer")
        
        result = self._call_rpc("/consent/search_farmer", "call", params)
        frappe.logger().debug("Odoo search_farmer response received")
        
        # Cleanly validate the entire response structure with Pydantic
        response = OpenG2PSearchFarmerResponse.model_validate(result)
        farmers = response.data.get("farmers")
        if farmers:
            farmer_data = farmers[0].model_dump()
            frappe.cache().set_value(cache_key, farmer_data, expires_in_sec=3600)
            return farmer_data
            
        frappe.throw(
            _("Farmer with Fayda ID '{0}' not found .").format(fayda_id),
            frappe.DoesNotExistError
        )

    # -------------------------------------------------------------------------
    # Partner lookup — uses normal portal session
    # -------------------------------------------------------------------------


    def get_partner_allowed_data_field_ids(self):
        """Fetch allowed_data_field_ids for a consent partner using Odoo API."""
        try:
            result = self.get_consent_allowed_fields()
            if result and isinstance(result, dict):
                fields_data = result.get("data", [])
                if isinstance(fields_data, list):
                    return [f["id"] for f in fields_data if isinstance(f, dict) and "id" in f]
            return []
        except Exception as e:
            frappe.logger().error(f"Failed to fetch allowed_data_field_ids: {str(e)}")
            return []

    # -------------------------------------------------------------------------
    # OTP — calls Fayda directly (bypasses Odoo session dependency)
    # -------------------------------------------------------------------------

    def request_otp(self, farmer_id):
        
        # WORKAROUND FOR ODOO 17 FRESH SESSION BUG:
        # We must initialize Odoo's internal session dictionary before requesting an OTP.
        # Calling verify_otp with a dummy transaction but a valid farmer_id reaches
        # _get_fayda_otp_session_store and forces Werkzeug to initialize and flush `{}`.
        frappe.logger().debug("[DEBUG] Priming Odoo session")
        prime_payload = {
            "jsonrpc": "2.0",
            "method": "call",
            "params": {
                "farmer_id": farmer_id,
                "transaction_id": "DUMMY_INIT",
                "otp_code": "000"
            }
        }
        try:
            self.session.post(f"{self.base_url}/consent/fayda/verify_otp", json=prime_payload, timeout=(5, 10))
        except requests.exceptions.RequestException as e:
            # Observability: log connectivity error specifically, but do not raise since it's best-effort priming
            frappe.logger().warning(f"OpenG2P session priming failed due to connectivity issue: {str(e)}")
        except Exception as e:
            # Log any other unexpected exception for debugging purposes
            frappe.logger().debug(f"OpenG2P session priming returned expected dummy error or other exception: {str(e)}")

        params = {"farmer_id": farmer_id}
        frappe.logger().debug("Calling Odoo /consent/fayda/request_otp")
        result = self._call_rpc("/consent/fayda/request_otp", "call", params)
        frappe.logger().debug("Odoo request_otp response received")

        # Cleanly validate response using Pydantic
        try:
            response = OpenG2POTPResponse.model_validate(result)
        except ValidationError as e:
            frappe.log_error(f"OTP Response Validation Failed: {str(e)}", "OTP Request Error")
            frappe.throw(_("Invalid response from OpenG2P OTP Request."))

        return {
            "transaction_id": response.data.transaction_id,
            "masked_mobile": response.data.masked_mobile
        }

    def verify_otp(self, farmer_id, transaction_id, otp_code):
        """
        Calls Odoo's /consent/fayda/verify_otp endpoint.
        """
        params = {
            "farmer_id": str(farmer_id),
            "transaction_id": transaction_id,
            "otp_code": str(otp_code)
        }
        frappe.logger().debug("Calling Odoo /consent/fayda/verify_otp")
        result = self._call_rpc("/consent/fayda/verify_otp", "call", params)
        frappe.logger().debug("Odoo verify_otp response received")

        # Cleanly validate response using Pydantic
        try:
            response = OpenG2PResponse.model_validate(result)
        except ValidationError as e:
            frappe.log_error(f"OTP Verification Response Validation Failed: {str(e)}", "OTP Verify Error")
            frappe.throw(_("Invalid response from OpenG2P OTP Verification."))

        return response.model_dump()

    def submit_consent(self, farmer_db_id, consent_type, consent_reason_id,
                       allowed_data_field_ids, attachment_base64, attachment_filename,
                       fayda_otp_transaction_id=None, validity_months=None):
        params = {
            "farmer_id": farmer_db_id,
            "consent_type": consent_type,
            "consent_reason_id": consent_reason_id,
            "allowed_data_field_ids": allowed_data_field_ids,
            "attachment_base64": attachment_base64,
            "attachment_filename": attachment_filename,
        }
        if fayda_otp_transaction_id:
            params["fayda_otp_transaction_id"] = fayda_otp_transaction_id
        if validity_months is not None:
            params["validity_months"] = validity_months

        frappe.logger().debug("Calling Odoo /api/consent/submit_consent")
        result = self._call_rpc("/api/consent/submit_consent", "call", params)
        frappe.logger().debug("Odoo submit_consent response received")

        # Cleanly validate response using Pydantic
        try:
            response = OpenG2PSubmitConsentResponse.model_validate(result)
        except ValidationError as e:
            frappe.log_error(f"Submit Consent Response Validation Failed: {str(e)}", "Submit Consent Error")
            frappe.throw(_("Invalid response from OpenG2P Consent Submission."))

        return response.model_dump()

    def get_consent_reasons(self):
        """Fetch all active consent reasons from OpenG2P."""
        cache_key = "openg2p_consent_reasons"
        cached = frappe.cache().get_value(cache_key)
        if cached:
            return cached

        frappe.logger().debug("Calling Odoo /api/consent/reasons")
        result = self._call_rpc("/api/consent/reasons", "call", {})
        frappe.logger().debug("Odoo get_consent_reasons response received")
        if result:
            frappe.cache().set_value(cache_key, result, expires_in_sec=3600)
        return result

    def get_consent_allowed_fields(self):
        """Fetch allowed data fields for a partner from OpenG2P."""
        cache_key = "openg2p_consent_allowed_fields"
        cached = frappe.cache().get_value(cache_key)
        if cached:
            return cached

        frappe.logger().debug("Calling Odoo /api/consent/allowed_data_fields")
        result = self._call_rpc("/api/consent/allowed_data_fields", "call", {})
        frappe.logger().debug("Odoo get_consent_allowed_fields response received")
        if result:
            frappe.cache().set_value(cache_key, result, expires_in_sec=3600)
        return result