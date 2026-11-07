"""Client for the OpenG2P stack behind the consent flow.

Three services, two credentials:

- Registry Agent API (`/agent_portal/vc/lookup_beneficiary`) — finds the farmer
  by national ID. Called with the *caller's own* bearer token: Development Agents
  sign in through the OpenG2P IAM, so the token on this request is already one
  the registry accepts.
- Consent Manager partner portal (`/consent/v1/partner-portal/*`) — raises the
  OTP consent request and verifies the code the farmer received.
- Aggregation Layer (`/dci/registry/sync/search`, beneficiary360) — returns the
  farmer's registry data against the granted `consent_id`.

The CM and AL are called with the bank's *partner* token (Keycloak partner realm),
configured in the `A2C OpenG2P Settings` single doctype.
"""

import os
import time
import uuid
from typing import Any

import frappe
import requests
from frappe import _
from frappe.utils import now_datetime
from pydantic import BaseModel, ConfigDict, ValidationError, model_validator

SETTINGS_DOCTYPE = "A2C OpenG2P Settings"
TOKEN_CACHE_KEY = "openg2p_partner_token"
TIMEOUT = (5, 15)


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


# ─── Response schemas ─────────────────────────────────────────────────────────


class AgentLookupResult(DriftWarnModel):
	internal_record_id: str
	register_id: str
	record_name: str | None = None
	# False when the record exists but is not ACTIVE in the registry.
	eligible: bool = True
	reason: str | None = None


class SubjectId(DriftWarnModel):
	type: str
	value: str


class CMConsentRequest(DriftWarnModel):
	id: str
	status: str
	method: str | None = None
	use_case: str | None = None
	subject_id: SubjectId
	subject_identifiers: list[SubjectId] = []
	purpose: dict[str, Any] = {}
	grants: list[dict[str, Any]] = []
	valid_from: str | None = None
	valid_until: str | None = None
	created_by: str | None = None
	created_at: str | None = None
	submitted_at: str | None = None
	verified_by: str | None = None
	verified_at: str | None = None
	verification_note: str | None = None
	consent_id: str | None = None
	subject_authentication: dict[str, Any] | None = None
	# Where the code went and when it stops working; the code itself is never returned.
	otp_channel: str | None = None
	otp_expires_at: str | None = None


class CMConsent(DriftWarnModel):
	consent_id: str
	status: str
	subject_id: SubjectId
	subject_identifiers: list[SubjectId] = []
	purpose: dict[str, Any] = {}
	grants: list[dict[str, Any]] = []
	valid_from: str | None = None
	valid_until: str | None = None
	assurance: dict[str, Any] | None = None
	use_case: str | None = None
	consent_request_id: str | None = None
	created_at: str | None = None
	revoked_at: str | None = None
	receipt_id: str | None = None


class CMBinding(DriftWarnModel):
	data_controller: str
	allowed_data_scopes: list[str] = []
	allowed_purposes: list[str] = []
	allowed_subject_id_types: list[str] = []
	max_validity_duration: str | None = None
	consent_method: str = "partner_signed"


# User-facing text for the Consent Manager's `{"error": code}` replies
# (consent-management otp_consent_service.py). Anything not listed — e.g. the
# OTP provider's wrong/expired-code reasons on verify — is surfaced as sent.
CM_ERROR_MESSAGES = {
	"otp_unavailable": "The OTP service is unavailable. Please try again later.",
	"subject_otp_limit_reached": "Too many OTPs have been sent to this farmer. Please try again later.",
	"resend_too_soon": "Please wait a minute before requesting another OTP.",
	"resend_limit_reached": "The OTP resend limit has been reached. Please start a new consent request.",
	"not_found": "The consent request was not found.",
}

# The Aggregation Layer refuses a search with `{"error": reason, "detail": ...}`.
AL_ERROR_MESSAGES = {
	"no_subject_consent": "The farmer's consent was not found or is no longer valid.",
	"consent_withdrawn": "The farmer has withdrawn this consent.",
	"subject_mismatch": "The consent does not belong to this farmer.",
	"purpose_mismatch": "The consent was given for a different purpose.",
	"no_scope_permitted": "The consent does not cover any of the requested data.",
	"consent_check_failed": "The consent could not be checked. Please try again later.",
}


class OpenG2PClient:
	"""Stateless: every call reads the cached settings and partner token."""

	def __init__(self):
		self.settings = frappe.get_cached_doc(SETTINGS_DOCTYPE)
		required = (
			"keycloak_url",
			"consent_manager_url",
			"aggregation_url",
			"partner_client_id",
			"partner_id",
		)
		missing = [f for f in required if not self.settings.get(f)]
		if missing:
			frappe.log_error(
				f"A2C OpenG2P Settings is missing: {', '.join(missing)}",
				"OpenG2P Configuration",
			)
			frappe.throw(_("OpenG2P integration is not configured. Please contact the administrator."))

		self.session = requests.Session()
		# OpenG2P uses an internal CA ("OpenG2P Local CA"). When verifying, point
		# requests at the CA bundle that includes it (REQUESTS_CA_BUNDLE, else the
		# system bundle) rather than plain True, which would use certifi and fail.
		if self.settings.verify_ssl:
			self.session.verify = os.environ.get("REQUESTS_CA_BUNDLE", "/etc/ssl/certs/ca-certificates.crt")
		else:
			self.session.verify = False
			requests.packages.urllib3.disable_warnings(
				requests.packages.urllib3.exceptions.InsecureRequestWarning
			)

	# -------------------------------------------------------------------------
	# Internal helpers
	# -------------------------------------------------------------------------

	def _partner_token(self) -> str:
		cached = frappe.cache().get_value(TOKEN_CACHE_KEY)
		if cached:
			return cached

		s = self.settings
		data = {"grant_type": s.partner_grant_type or "client_credentials", "client_id": s.partner_client_id}
		client_secret = s.get_password("partner_client_secret", raise_exception=False)
		if client_secret:
			data["client_secret"] = client_secret
		if data["grant_type"] == "password":
			data["username"] = s.partner_username
			data["password"] = s.get_password("partner_password", raise_exception=False)

		url = f"{s.keycloak_url}/realms/{s.partner_realm or 'partner'}/protocol/openid-connect/token"
		try:
			response = self.session.post(url, data=data, timeout=TIMEOUT)
		except requests.exceptions.RequestException as e:
			frappe.log_error(f"Keycloak connection error: {e}", "OpenG2P Connection")
			frappe.throw(_("Unable to reach OpenG2P. Please try again later."))

		if not response.ok:
			# Keep the client id out of the client-facing error; log it server-side.
			frappe.log_error(
				f"Partner token request failed for client {s.partner_client_id}: "
				f"{response.status_code} {response.text[:500]}",
				"OpenG2P Authentication",
			)
			frappe.throw(_("Unable to authenticate with OpenG2P. Please try again later."))

		body = response.json()
		token = body["access_token"]
		# Expire our copy a little before Keycloak does, so a token is never sent
		# in its last seconds.
		ttl = max(int(body.get("expires_in") or 300) - 30, 30)
		frappe.cache().set_value(TOKEN_CACHE_KEY, token, expires_in_sec=ttl)
		return token

	def _request(self, method, url, *, token=None, json=None, headers=None, error_messages=None, attempt=0):
		"""Send one request and return the decoded JSON body.

		`token=None` means "use the partner token" (refreshed once on a 401).
		Non-2xx replies are turned into frappe exceptions with a user-facing message.
		"""
		if frappe.cache().get_value("cb:openg2p") == "open":
			frappe.throw(
				_(
					"OpenG2P service is temporarily unavailable due to high error rates. Please try again later."
				)
			)

		use_partner_token = token is None
		bearer = self._partner_token() if use_partner_token else token
		headers = {**(headers or {}), "Authorization": f"Bearer {bearer}"}

		try:
			response = self.session.request(method, url, json=json, headers=headers, timeout=TIMEOUT)
		except requests.exceptions.RequestException as e:
			if attempt < 2:
				time.sleep(0.5 * (2**attempt))
				return self._request(
					method,
					url,
					token=token,
					json=json,
					headers=headers,
					error_messages=error_messages,
					attempt=attempt + 1,
				)

			fails = int(frappe.cache().get_value("cb:openg2p_fails") or 0) + 1
			frappe.cache().set_value("cb:openg2p_fails", fails, expires_in_sec=60)
			if fails >= 3:
				frappe.cache().set_value("cb:openg2p", "open", expires_in_sec=60)

			frappe.log_error(f"OpenG2P connection error on {url}: {e}", "OpenG2P Connection")
			frappe.throw(_("Unable to reach OpenG2P. Please try again later."))

		if response.status_code == 401 and use_partner_token and attempt == 0:
			# The cached token was revoked or expired early: fetch a fresh one once.
			frappe.cache().delete_value(TOKEN_CACHE_KEY)
			return self._request(
				method, url, json=json, headers=headers, error_messages=error_messages, attempt=1
			)

		if response.ok:
			frappe.cache().delete_value("cb:openg2p_fails")
			return response.json() if response.content else {}

		frappe.log_error(
			f"OpenG2P returned {response.status_code} on {url}: {response.text[:1000]}", "OpenG2P Error"
		)
		try:
			body = response.json()
		except ValueError:
			body = {}
		code = (body.get("error") or body.get("detail") or "") if isinstance(body, dict) else ""
		if not isinstance(code, str):
			# FastAPI 422s carry a list of field errors in `detail`.
			code = ""

		status = response.status_code
		message = (error_messages or {}).get(code)
		if not message and code.startswith("request is '"):
			message = _("This consent request is no longer pending ({0}).").format(code[len("request is ") :])
		if status in (401, 403) and not message:
			frappe.throw(_("OpenG2P refused the request: not authorised."), frappe.PermissionError)
		if status == 404:
			frappe.throw(_(message or "Not found in OpenG2P."), frappe.DoesNotExistError)
		if status >= 500 and not message:
			frappe.throw(_("OpenG2P request could not be completed. Please try again later."))
		# 4xx with an application-level reason (wrong OTP, rate limits, consent refusals):
		# surface it so the caller sees actionable feedback.
		frappe.throw(_(message or code or "OpenG2P request could not be completed."))

	@staticmethod
	def _parse(model, body, what):
		try:
			return model.model_validate(body)
		except ValidationError as e:
			frappe.log_error(f"{what} response validation failed: {e!s}", "OpenG2P Error")
			frappe.throw(_("Invalid response from OpenG2P ({0}).").format(what))

	# -------------------------------------------------------------------------
	# Agent API — farmer lookup
	# -------------------------------------------------------------------------

	def lookup_beneficiary(self, national_id) -> dict:
		"""Find the farmer's registry record by national (Fayda) ID."""
		cache_key = f"farmer_by_fayda:{national_id}"
		cached = frappe.cache().get_value(cache_key)
		if cached:
			return cached

		if not self.settings.agent_api_url:
			frappe.throw(_("OpenG2P integration is not configured. Please contact the administrator."))

		auth_header = frappe.get_request_header("Authorization") or ""
		token = auth_header[7:] if auth_header.lower().startswith("bearer ") else ""
		if not token:
			frappe.throw(
				_("An agent sign-in is required to search the farmer registry."), frappe.PermissionError
			)

		envelope = {
			"request_header": {
				"request_id": str(uuid.uuid4()),
				"request_timestamp": now_datetime().isoformat(),
				"sender_app_mnemonic": "oan_a2c",
				"sender_app_url": frappe.utils.get_url(),
			},
			"request_body": {"request_payload": {"national_id": national_id}},
		}
		# iam_core's CsrfMiddleware wants the double-submit pair on every POST, even
		# with a bearer token: the same random value as X-CSRF-Token cookie and header.
		csrf = frappe.generate_hash(length=32)
		body = self._request(
			"POST",
			f"{self.settings.agent_api_url}/agent_portal/vc/lookup_beneficiary",
			token=token,
			json=envelope,
			headers={"X-CSRF-Token": csrf, "Cookie": f"X-CSRF-Token={csrf}"},
		)

		header = body.get("response_header") or {}
		if str(header.get("response_status", "")).upper() == "ERROR":
			error_message = header.get("response_error_message") or ""
			frappe.log_error(
				f"Agent lookup_beneficiary error {header.get('response_error_code')}: {error_message}",
				"OpenG2P Error",
			)
			if "not found" in error_message.lower() or "no record" in error_message.lower():
				frappe.throw(
					_("Farmer with Fayda ID '{0}' not found.").format(national_id), frappe.DoesNotExistError
				)
			frappe.throw(_(error_message or "Farmer lookup failed."))

		payload = (body.get("response_body") or {}).get("response_payload")
		if not payload:
			frappe.throw(
				_("Farmer with Fayda ID '{0}' not found.").format(national_id), frappe.DoesNotExistError
			)

		farmer = self._parse(AgentLookupResult, payload, "lookup_beneficiary").model_dump()
		frappe.cache().set_value(cache_key, farmer, expires_in_sec=3600)
		return farmer

	# -------------------------------------------------------------------------
	# Consent Manager — OTP consent
	# -------------------------------------------------------------------------

	def _cm(self, path):
		return f"{self.settings.consent_manager_url}/consent/v1/partner-portal{path}"

	def create_otp_request(self, national_id) -> dict:
		"""Raise a consent request with method "otp"; the CM sends the farmer a code.

		What is asked for comes from the partner's Consent Manager bindings: every binding
		that allows our purpose contributes its data controller and all its allowed scopes,
		so the binding is the one place that decides the farmer-facing scope.
		"""
		purpose = self.settings.purpose_code or "loan_origination"
		grants = [
			{"data_controller": b["data_controller"], "data_scopes": b["allowed_data_scopes"]}
			for b in self.get_bindings()
			if purpose in b["allowed_purposes"] and b["allowed_data_scopes"]
		]
		if not grants:
			frappe.throw(
				_(
					"The Consent Manager allows no data scopes for purpose '{0}'. Please contact the administrator."
				).format(purpose)
			)

		body = {
			"method": "otp",
			"subject_id": {"type": "national_id", "value": national_id},
			"purpose": {"code": purpose},
			"grants": grants,
		}
		result = self._request(
			"POST", self._cm("/consent-requests"), json=body, error_messages=CM_ERROR_MESSAGES
		)
		return self._parse(CMConsentRequest, result, "create consent request").model_dump()

	def verify_otp(self, request_id, otp) -> dict:
		"""Verify the farmer's code; a correct code grants the consent."""
		result = self._request(
			"POST",
			self._cm(f"/consent-requests/{request_id}/verify-otp"),
			json={"otp": str(otp)},
			error_messages=CM_ERROR_MESSAGES,
		)
		return self._parse(CMConsent, result, "verify OTP").model_dump()

	def resend_otp(self, request_id) -> dict:
		result = self._request(
			"POST", self._cm(f"/consent-requests/{request_id}/resend-otp"), error_messages=CM_ERROR_MESSAGES
		)
		return self._parse(CMConsentRequest, result, "resend OTP").model_dump()

	def get_bindings(self) -> list[dict]:
		"""What this partner may ask for: data controllers, scopes, purposes."""
		cache_key = "openg2p_partner_bindings"
		cached = frappe.cache().get_value(cache_key)
		if cached:
			return cached

		result = self._request("GET", self._cm("/bindings"), error_messages=CM_ERROR_MESSAGES)
		bindings = [self._parse(CMBinding, b, "bindings").model_dump() for b in (result or [])]
		frappe.cache().set_value(cache_key, bindings, expires_in_sec=3600)
		return bindings

	# -------------------------------------------------------------------------
	# Aggregation Layer — farmer data
	# -------------------------------------------------------------------------

	def fetch_beneficiary360(self, national_id, consent_id) -> dict:
		"""Return the beneficiary360 record (`reg_records[0]`) for a granted consent."""
		body = {
			"header": {"sender_id": self.settings.partner_id, "receiver_id": "aggregation-layer"},
			"message": {
				"transaction_id": frappe.generate_hash(length=20),
				"search_request": [
					{
						"reference_id": frappe.generate_hash(length=20),
						"search_criteria": {
							"query_type": "beneficiary360",
							"query": {
								"@context": "https://schemas.openg2p.org/beneficiary360/v1/context.jsonld",
								"foundationalId": national_id,
								"timeframe": self.settings.timeframe or "Timeframe-Medium",
								"sections": ["REGISTRIES"],
							},
							"purpose": {"code": self.settings.purpose_code or "loan_origination"},
							"authorize": {"consent_id": consent_id},
						},
					}
				],
			},
		}
		result = self._request(
			"POST",
			f"{self.settings.aggregation_url}/dci/registry/sync/search",
			json=body,
			error_messages=AL_ERROR_MESSAGES,
		)

		header = result.get("header") or {}
		search_response = ((result.get("message") or {}).get("search_response") or [{}])[0]
		if header.get("status") != "succ" or search_response.get("status") not in (None, "succ"):
			frappe.log_error(
				f"Aggregation Layer rejected the search: {header.get('status_reason_code')} "
				f"{header.get('status_reason_message')}",
				"OpenG2P Error",
			)
			frappe.throw(_("The farmer registries are unavailable right now. Please try again later."))

		records = (search_response.get("data") or {}).get("reg_records") or []
		if not records:
			frappe.throw(_("No registry data was returned for this farmer."), frappe.DoesNotExistError)
		return records[0]
