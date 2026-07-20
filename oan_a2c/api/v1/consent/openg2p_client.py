import os
from typing import Optional, Union

import frappe
import requests
from frappe import _
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator


class DriftWarnModel(BaseModel):
	"""Base for OpenG2P response schemas.

	Keeps unexpected fields instead of silently dropping them (extra="allow")
	and logs a warning when OpenG2P returns fields we don't model — so API
	payload drift is surfaced without raising/breaking the flow.
	"""

	model_config = ConfigDict(extra="allow")

	# How long (seconds) to suppress duplicate drift Error Logs for the same
	# model + field signature, so a drifting OpenG2P endpoint doesn't flood the
	# Error Log table on every request.
	_DRIFT_LOG_TTL = 3600

	@model_validator(mode="after")
	def _warn_on_unexpected_fields(self):
		extra = self.model_extra or {}
		if extra:
			model_name = type(self).__name__
			field_keys = list(extra.keys())
			message = (
				f"OpenG2P response {model_name} returned unexpected fields (possible API drift): {field_keys}"
			)
			# Full, untruncated signal to the log file / dev console.
			frappe.logger().warning(message)
			# Persistent, UI-visible signal in the Error Log — this survives in
			# production (WARNING logs are dropped there) but is rate-limited so
			# sustained drift doesn't bloat the table.
			self._log_drift_error(model_name, field_keys, message)
		return self

	def _log_drift_error(self, model_name, field_keys, message):
		signature = f"{model_name}:{','.join(sorted(field_keys))}"
		try:
			# Resolve the underlying RedisWrapper regardless of whether
			# frappe.cache() returns it directly or the ClientCache wrapper,
			# so make_key / SET behave consistently across Frappe versions.
			cache = frappe.cache()
			redis = getattr(cache, "redis", cache)
			cache_key = redis.make_key(f"openg2p_drift_logged:{signature}")
			# SET NX EX: log only if the key isn't already present, and let it
			# expire after the TTL — one Error Log per drift signature per hour.
			was_set = redis.set(cache_key, 1, nx=True, ex=self._DRIFT_LOG_TTL)
			if was_set:
				frappe.log_error(title="OpenG2P API Drift", message=message)
		except Exception:
			# Never let observability break the response flow.
			pass


class OpenG2PResponse(DriftWarnModel):
	success: bool
	message: str | None = None


class OpenG2PFarmerSchema(DriftWarnModel):
	id: int | str
	name: str
	mobile: str | None = None
	phone: str | None = None
	profile_image_url: str | None = None
	otp_identifier_type: str | None = None


class OpenG2PSearchFarmerResponse(OpenG2PResponse):
	data: dict[str, list[OpenG2PFarmerSchema]] = Field(default_factory=dict)


class OpenG2POTPData(DriftWarnModel):
	transaction_id: str
	masked_mobile: str | None = "XXXX"
	# Added after OpenG2P drift: request_otp now also returns email masking and
	# identifier provenance. Modelled as optional so older responses still parse.
	masked_email: str | None = None
	identifier_type: str | None = None
	identifier_source: str | None = None


class OpenG2POTPResponse(OpenG2PResponse):
	data: OpenG2POTPData


class OpenG2PSubmitConsentData(DriftWarnModel):
	consent_request_id: int | str | None = None
	consent_id: int | str | None = None
	consent_creation_request_id: int | str | None = None
	id: int | str | None = None
	# Farmer profile returned inline by OpenG2P (keyed by farmer id). Declared
	# so Pydantic preserves it through model_dump() instead of dropping it.
	response_data: dict | None = None
	# Added after OpenG2P drift: submit_consent now returns the upstream
	# auto-approval decision. `status` is the authoritative consent outcome —
	# consuming code must NOT assume every submission is approved.
	status: str | None = None
	auto_approved: bool | None = None
	auto_approval_failed: bool | None = None
	auto_approve_method: str | None = None
	error_details: str | None = None


class OpenG2PSubmitConsentResponse(OpenG2PResponse):
	data: OpenG2PSubmitConsentData


class OpenG2PConsentClient:
	# TEMPORARY (dev only): the OpenG2P dev server uses a self-signed TLS
	# certificate. Set to False to skip certificate verification so requests
	# succeed against it. MUST be True in production — disabling this exposes
	# the connection to man-in-the-middle attacks.
	VERIFY_SSL = True

	def __init__(self, portal_session_id=None, cookie_dict=None):
		self.base_url = frappe.conf.get("openg2p_base_url")
		self.db = frappe.conf.get("openg2p_db", "")

		# Portal user — for consent creation
		self.username = frappe.conf.get("openg2p_username", "")
		self.password = frappe.conf.get("openg2p_password", "")

		if not self.base_url or not self.username or not self.password:
			frappe.log_error(
				"OpenG2P is not fully configured (base_url/username/password missing in site_config.json).",
				"OpenG2P Configuration",
			)
			frappe.throw(_("OpenG2P integration is not configured. Please contact the administrator."))

		self.session = requests.Session()  # portal user session
		# OpenG2P uses an internal CA ("OpenG2P Local CA"). When verifying, point
		# requests at the CA bundle that includes it (REQUESTS_CA_BUNDLE, else the
		# system bundle) rather than plain True, which would use certifi and fail.
		# This is set on the session AND passed explicitly to session.send(), which
		# otherwise bypasses this setting.
		if self.VERIFY_SSL:
			self.session.verify = os.environ.get("REQUESTS_CA_BUNDLE", "/etc/ssl/certs/ca-certificates.crt")
		else:
			self.session.verify = False
			# Silence the per-request InsecureRequestWarning while verification is off.
			requests.packages.urllib3.disable_warnings(
				requests.packages.urllib3.exceptions.InsecureRequestWarning
			)
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
					frappe.cache().set_value(
						"openg2p_portal_session_cookies", new_cookies, expires_in_sec=1800
					)

	# -------------------------------------------------------------------------
	# Internal helpers
	# -------------------------------------------------------------------------

	def _authenticate(self, session, username, password):
		url = f"{self.base_url}/web/session/authenticate"
		payload = {
			"jsonrpc": "2.0",
			"method": "call",
			"params": {"db": self.db, "login": username, "password": password},
		}
		try:
			response = session.post(url, json=payload, timeout=(5, 10))
			response.raise_for_status()
			result = response.json()
			if result.get("result", {}).get("uid"):
				return
			# Keep the internal username out of the client-facing error; log the
			# detail server-side only.
			frappe.log_error(
				f"OpenG2P authentication failed for user: {username}",
				"OpenG2P Authentication",
			)
			frappe.throw(_("Unable to authenticate with OpenG2P. Please try again later."))
		except requests.exceptions.RequestException as e:
			frappe.log_error(
				f"OpenG2P connection error during authentication "
				f"(transaction_id={self.portal_session_id}): {e}",
				"OpenG2P Connection",
			)
			frappe.throw(_("Unable to reach OpenG2P. Please try again later."))

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
		payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
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

			req = requests.Request("POST", url, json=payload, headers=headers)
			prepared = session.prepare_request(req)

			# session.send() bypasses requests' environment/session merging, so
			# neither session.verify nor REQUESTS_CA_BUNDLE is applied unless we
			# pass verify explicitly. Forward the session's verify setting.
			response = session.send(prepared, timeout=(5, 10), verify=session.verify)
			response.raise_for_status()
			data = response.json()

			if "error" in data:
				err = data["error"]
				msg = err.get("data", {}).get("message") or err.get("message") or "Unknown OpenG2P Error"

				# Check for session expiration to retry transparently
				if (
					not getattr(self, "cookie_dict", None)
					and not getattr(self, "portal_session_id", None)
					and any(
						x in msg.lower()
						for x in ["session", "expired", "authentication", "logged out", "access denied"]
					)
				):
					self._refresh_portal_session()
					return self._call_rpc(endpoint, method, params)

				frappe.log_error(f"OpenG2P RPC error on {endpoint}: {msg}", "OpenG2P Error")
				frappe.throw(_("OpenG2P request could not be completed. Please try again later."))

			result = data.get("result")
			if isinstance(result, dict) and result.get("success") is False:
				frappe.log_error(
					f"OpenG2P returned failure on {endpoint}: {result.get('message') or 'Unknown error'}",
					"OpenG2P Error",
				)
				frappe.throw(_("OpenG2P request could not be completed. Please try again later."))

			return result

		except requests.exceptions.RequestException as e:
			txn_id = (params or {}).get("fayda_otp_transaction_id") or self.portal_session_id
			frappe.log_error(
				f"OpenG2P connection error on {endpoint} (transaction_id={txn_id}): {e}",
				"OpenG2P Connection",
			)
			frappe.throw(_("Unable to reach OpenG2P. Please try again later."))

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
			farmer_model = farmers[0]
			if farmer_model.profile_image_url:
				try:
					if farmer_model.profile_image_url.startswith("/"):
						full_url = f"{self.base_url}{farmer_model.profile_image_url}"
					else:
						full_url = farmer_model.profile_image_url

					img_resp = self.session.get(full_url, timeout=(5, 10))
					if img_resp.status_code == 200:
						import base64

						content_type = img_resp.headers.get("Content-Type") or "image/png"
						b64_data = base64.b64encode(img_resp.content).decode("utf-8")
						farmer_model.profile_image_url = f"data:{content_type};base64,{b64_data}"
				except Exception as e:
					frappe.logger().warning(f"Failed to fetch profile image for farmer: {e}")

			farmer_data = farmer_model.model_dump()
			frappe.cache().set_value(cache_key, farmer_data, expires_in_sec=3600)
			return farmer_data

		frappe.throw(_("Farmer with Fayda ID '{0}' not found .").format(fayda_id), frappe.DoesNotExistError)

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
			frappe.logger().error(f"Failed to fetch allowed_data_field_ids: {e!s}")
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
			"params": {"farmer_id": farmer_id, "transaction_id": "DUMMY_INIT", "otp_code": "000"},
		}
		try:
			self.session.post(
				f"{self.base_url}/consent/fayda/verify_otp", json=prime_payload, timeout=(5, 10)
			)
		except requests.exceptions.RequestException as e:
			# Observability: log connectivity error specifically, but do not raise since it's best-effort priming
			frappe.logger().warning(f"OpenG2P session priming failed due to connectivity issue: {e!s}")
		except Exception as e:
			# Log any other unexpected exception for debugging purposes
			frappe.logger().debug(
				f"OpenG2P session priming returned expected dummy error or other exception: {e!s}"
			)

		params = {"farmer_id": farmer_id}
		frappe.logger().debug("Calling Odoo /consent/fayda/request_otp")
		result = self._call_rpc("/consent/fayda/request_otp", "call", params)
		frappe.logger().debug("Odoo request_otp response received")

		# Cleanly validate response using Pydantic
		try:
			response = OpenG2POTPResponse.model_validate(result)
		except ValidationError as e:
			frappe.log_error(f"OTP Response Validation Failed: {e!s}", "OTP Request Error")
			frappe.throw(_("Invalid response from OpenG2P OTP Request."))

		return {"transaction_id": response.data.transaction_id, "masked_mobile": response.data.masked_mobile}

	def verify_otp(self, farmer_id, transaction_id, otp_code):
		"""
		Calls Odoo's /consent/fayda/verify_otp endpoint.
		"""
		params = {"farmer_id": str(farmer_id), "transaction_id": transaction_id, "otp_code": str(otp_code)}
		frappe.logger().debug("Calling Odoo /consent/fayda/verify_otp")
		result = self._call_rpc("/consent/fayda/verify_otp", "call", params)
		frappe.logger().debug("Odoo verify_otp response received")

		# Cleanly validate response using Pydantic
		try:
			response = OpenG2PResponse.model_validate(result)
		except ValidationError as e:
			frappe.log_error(f"OTP Verification Response Validation Failed: {e!s}", "OTP Verify Error")
			frappe.throw(_("Invalid response from OpenG2P OTP Verification."))

		return response.model_dump()

	def submit_consent(
		self,
		farmer_db_id,
		consent_type,
		consent_reason_id,
		allowed_data_field_ids,
		attachment_base64,
		attachment_filename,
		fayda_otp_transaction_id=None,
		validity_months=None,
	):
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
			frappe.log_error(f"Submit Consent Response Validation Failed: {e!s}", "Submit Consent Error")
			frappe.throw(_("Invalid response from OpenG2P Consent Submission."))

		# Expected-but-missing check: the farmer profile is delivered inline via
		# response_data. Warn (don't fail) if it's absent so a silent no-op in
		# the downstream save doesn't go unnoticed.
		if not response.data.response_data:
			frappe.logger().warning(
				"OpenG2P submit_consent response has no response_data — "
				"farmer profile will not be saved from the inline payload."
			)

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
