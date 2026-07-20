from typing import Optional

import frappe
from frappe import _
from frappe.utils import now_datetime
from pydantic import BaseModel, Field

from oan_a2c.api.utils import SafeDate, handle_api_errors, success_response, validate_request
from oan_a2c.api.v1.webhook_consent_data import validate_and_enqueue_consent

from .openg2p_client import OpenG2PConsentClient
from .utils import generate_consent_receipt


class ConsentNotApproved(Exception):
	"""Raised when OpenG2P declines/auto-fails a consent submission, so the
	handler can persist a "Rejected" outcome instead of a false "Approved".
	Carries the upstream reason as its string value."""


# ─── Pydantic Validation Schemas ──────────────────────────────────────────────


class SearchFarmerSchema(BaseModel):
	fayda_id: str = Field(..., min_length=1)
	lead_id: str | None = None


class RequestOTPSchema(BaseModel):
	fayda_id: str = Field(..., min_length=1)
	lead_id: str = Field(..., min_length=1)
	idempotency_key: str | None = None


class VerifyOTPSchema(BaseModel):
	lead_id: str = Field(..., min_length=1)
	otp_code: str = Field(..., min_length=1)
	transaction_id: str | None = None
	consent_request: str = Field(..., min_length=1)


class SubmitConsentSchema(BaseModel):
	lead_id: str = Field(..., min_length=1)
	consent_request: str = Field(..., min_length=1)
	consent_type: str | None = "specific"
	consent_reason_id: int | None = 1
	validity_months: int | None = None
	consent_form_filename: str = Field(..., min_length=1)
	consent_form_base64: str = Field(..., min_length=1)
	allowed_data_field_ids: list[int] | None = None


class GetConsentAllowedFieldsSchema(BaseModel):
	pass


# ─── Rate Limiting & Helpers ──────────────────────────────────────────────────


def check_rate_limit(key: str, limit: int, window: int):
	"""
	Apply rate limits using Redis counter.
	key    — unique per user+endpoint
	limit  — max calls allowed in window
	window — seconds
	"""
	cache = frappe.cache()
	count = cache.get_value(key) or 0

	if int(count) >= limit:
		frappe.response.status_code = 429
		frappe.throw(_("Rate limit exceeded. Try again later."), frappe.ValidationError)

	pipeline = cache.pipeline()
	pipeline.incr(key)
	pipeline.expire(key, window)
	pipeline.execute()


def _get_farmer_preview_from_lead(lead_id):
	"""Reconstruct a farmer preview dict from the lead's linked Farmer Profile."""
	farmer_profile_name = frappe.db.get_value("A2C Lead", lead_id, "farmer_profile")
	if farmer_profile_name:
		profile = frappe.get_doc("A2C Farmer Profile", farmer_profile_name)
		return {
			"given_name": profile.first_name,
			"family_name": profile.last_name,
			"email": profile.email,
			"phone_no": [profile.phone_number] if profile.phone_number else [],
		}
	return {}


# ─── Private helpers ──────────────────────────────────────────────────────────


def _client_for_transaction(transaction_id):
	"""Rebuild the OpenG2P client bound to the Odoo session that issued the OTP."""
	cookie_dict = frappe.cache().get_value(f"odoo_session_dict_{transaction_id}")
	if cookie_dict:
		return OpenG2PConsentClient(cookie_dict=cookie_dict)
	odoo_session_id = frappe.cache().get_value(f"odoo_session_{transaction_id}")
	return OpenG2PConsentClient(portal_session_id=odoo_session_id)


def _get_consent_request_and_client(consent_request, expected_status=None, check_verified=False):
	"""Retrieve A2C Consent Request doc and rebuild its associated client."""
	# Pessimistic lock to prevent race conditions
	status = frappe.db.get_value("A2C Consent Request", consent_request, "status", for_update=True)
	if not status:
		frappe.throw(
			_("A2C Consent Request '{0}' not found.").format(consent_request), frappe.DoesNotExistError
		)

	cr_doc = frappe.get_doc("A2C Consent Request", consent_request)

	if expected_status and cr_doc.status != expected_status:
		frappe.throw(
			_("A2C Consent Request '{0}' must be in status '{1}'. Current status: '{2}'.").format(
				consent_request, expected_status, cr_doc.status
			),
			frappe.ValidationError,
		)

	if check_verified and not cr_doc.otp_verified_at:
		frappe.throw(
			_("OTP has not been verified for consent request '{0}'.").format(consent_request),
			frappe.ValidationError,
		)

	transaction_id = cr_doc.otp_transaction_id
	if not transaction_id:
		frappe.throw(
			_("Transaction ID is missing for consent request '{0}'.").format(consent_request),
			frappe.ValidationError,
		)

	client = _client_for_transaction(transaction_id)
	return cr_doc, client, transaction_id


def _save_farmer_data_to_lead(lead_id, farmer_dict, openg2p_consent_id):
	"""
	Persist the OpenG2P farmer profile onto the lead as consent_data — mirrors
	what the WebSub webhook would deliver. Non-fatal on failure.
	Returns the farmer preview dict.
	"""
	import json as _json

	try:
		farmer_record = {}
		selected_data = {}

		if farmer_dict:
			full_name = (farmer_dict.get("name") or "").strip()
			parts = full_name.split()
			given_name = parts[0].title() if parts else ""
			family_name = " ".join(p.title() for p in parts[1:]) if len(parts) > 1 else ""
			mobile = farmer_dict.get("mobile") or farmer_dict.get("phone") or ""

			farmer_record = {"id": farmer_dict.get("id"), "name": full_name}
			selected_data = {
				"synthetic_direct_fetch": {
					"Full Name": full_name,
					"Email": farmer_dict.get("email") or "",
					"Mobile Number": [mobile] if mobile else [],
				}
			}
			# Still need to return the old farmer_preview dict shape
			# for the `submit_consent` method caller
			farmer_preview_dict = {
				"given_name": given_name,
				"family_name": family_name,
				"email": farmer_dict.get("email") or "",
				"phone_no": [mobile] if mobile else [],
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

		lead_doc = frappe.get_doc("A2C Lead", lead_id)
		lead_doc.consent_data = _json.dumps(synthetic_payload, indent=2, ensure_ascii=False)
		# Using ignore_permissions=False is required here for secure row-level permission enforcement.
		lead_doc.save(ignore_permissions=False)
		frappe.logger().info(f"Farmer data saved to A2C Lead {lead_id}")
		return farmer_preview_dict

	except Exception as e:
		frappe.logger().warning(f"Direct farmer data save failed: {e}")
		frappe.log_error(f"Direct consent data save failed: {e}", "Consent Data Save")
		return {}


# 1 ───────────────────────────────────────────────────────────────────────────
@frappe.whitelist(allow_guest=False)
@validate_request(SearchFarmerSchema)
@handle_api_errors
def search_farmer(**kwargs):
	"""Find a farmer  by Fayda ID. → client.get_farmer_by_fayda_id"""
	check_rate_limit(f"rl:search_farmer:{frappe.session.user}", limit=20, window=60)

	fayda_id = kwargs.get("fayda_id")
	lead_id = kwargs.get("lead_id")

	if lead_id:
		frappe.has_permission("A2C Lead", "read", doc=lead_id, throw=True)
	else:
		if not (frappe.has_permission("A2C Lead", "read") or "System Manager" in frappe.get_roles()):
			frappe.throw(_("Not permitted to search farmer profile"), frappe.PermissionError)

	client = OpenG2PConsentClient()
	farmer_dict = client.get_farmer_by_fayda_id(fayda_id)

	return success_response(
		data={
			"farmer": {
				"name": farmer_dict.get("name"),
				"mobile": farmer_dict.get("mobile"),
				"phone": farmer_dict.get("phone"),
				"profile_image_url": farmer_dict.get("profile_image_url"),
				"id": farmer_dict.get("id"),
				"type": farmer_dict.get("otp_identifier_type"),
			}
		},
		message="Farmer found successfully.",
	)


# 2 ───────────────────────────────────────────────────────────────────────────
@frappe.whitelist(allow_guest=False)
@handle_api_errors
def get_partner_allowed_data_field_ids():
	"""Return the allowed data field IDs for the consent partner.
	→ client.get_partner_allowed_data_field_ids"""
	client = OpenG2PConsentClient()
	field_ids = client.get_partner_allowed_data_field_ids()

	return success_response(
		data={"allowed_data_field_ids": field_ids},
		message="Allowed data field IDs retrieved successfully.",
	)


# 3 ───────────────────────────────────────────────────────────────────────────
@frappe.whitelist(allow_guest=False)
@validate_request(RequestOTPSchema)
@handle_api_errors
def request_otp(**kwargs):
	"""Open a pending consent request and ask OpenG2P/Fayda for an OTP."""
	check_rate_limit(f"rl:request_otp:{frappe.session.user}", limit=5, window=60)

	fayda_id = kwargs.get("fayda_id")
	lead_id = kwargs.get("lead_id")
	idempotency_key = kwargs.get("idempotency_key")

	if not frappe.db.exists("A2C Lead", lead_id):
		frappe.throw(_("A2C Lead {0} not found").format(lead_id), frappe.DoesNotExistError)
	frappe.has_permission("A2C Lead", "write", doc=lead_id, throw=True)

	# Idempotency lock & check using Redis cache
	if idempotency_key:
		lock_key = f"lock:request_otp:{idempotency_key}"
		if frappe.cache().get_value(lock_key):
			frappe.throw(_("Request in progress, please retry in a moment."), frappe.ValidationError)
		frappe.cache().set_value(lock_key, "1", expires_in_sec=10)

		# Check cached response
		cached_res = frappe.cache().get_value(f"idempotency:request_otp:{idempotency_key}")
		if cached_res:
			frappe.cache().delete_value(lock_key)
			return cached_res

		# Check existing mapping in cache
		existing_req_name = frappe.cache().get_value(f"idempotency_consent_req:{idempotency_key}")
		if existing_req_name:
			txn_id = frappe.db.get_value("A2C Consent Request", existing_req_name, "otp_transaction_id")
			frappe.cache().delete_value(lock_key)
			return success_response(
				data={
					"consent_request": existing_req_name,
					"transaction_id": txn_id or "",
					"masked_phone": "XXXX",
				},
				message="OTP sent successfully. Proceed to verify OTP.",
			)

	try:
		client = OpenG2PConsentClient()

		# Resolve the farmer's OpenG2P id once
		farmer_dict = client.get_farmer_by_fayda_id(fayda_id)
		farmer_db_id = farmer_dict.get("id")

		# Open the pending consent request
		doc = frappe.new_doc("A2C Consent Request")
		doc.lead = lead_id
		doc.farmer = farmer_db_id
		doc.farmer_fayda_id = fayda_id
		doc.status = "Pending OTP"
		doc.insert(ignore_permissions=False)

		# client call — request the OTP from Fayda via Odoo.
		otp_data = client.request_otp(farmer_id=farmer_db_id)
		transaction_id = otp_data["transaction_id"]

		doc.otp_transaction_id = transaction_id
		doc.save(ignore_permissions=False)
		frappe.db.commit()

		# Preserve the Odoo session so verify_otp / submit_consent reuse it.
		import requests

		cookie_dict = requests.utils.dict_from_cookiejar(client.session.cookies)
		if cookie_dict:
			frappe.cache().set_value(f"odoo_session_dict_{transaction_id}", cookie_dict, expires_in_sec=1800)

		res_payload = success_response(
			data={
				"consent_request": doc.name,
				"transaction_id": transaction_id,
				"masked_phone": otp_data["masked_mobile"],
			},
			message="OTP sent successfully. Proceed to verify OTP.",
		)

		if idempotency_key:
			frappe.cache().set_value(
				f"idempotency:request_otp:{idempotency_key}", res_payload, expires_in_sec=86400
			)
			frappe.cache().set_value(
				f"idempotency_consent_req:{idempotency_key}", doc.name, expires_in_sec=86400
			)
			frappe.cache().delete_value(lock_key)

		return res_payload

	except Exception as e:
		if idempotency_key:
			frappe.cache().delete_value(f"lock:request_otp:{idempotency_key}")
		raise e


# 4 ───────────────────────────────────────────────────────────────────────────
@frappe.whitelist(allow_guest=False)
@validate_request(VerifyOTPSchema)
@handle_api_errors
def verify_otp(**kwargs):
	"""Verify the Fayda OTP for a pending consent request. → client.verify_otp"""
	lead_id = kwargs.get("lead_id")
	otp_code = kwargs.get("otp_code")
	consent_request = kwargs.get("consent_request")

	frappe.has_permission("A2C Lead", "write", doc=lead_id, throw=True)
	frappe.has_permission("A2C Consent Request", "write", doc=consent_request, throw=True)

	cr_doc, client, transaction_id = _get_consent_request_and_client(
		consent_request, expected_status="Pending OTP"
	)
	farmer_db_id = cr_doc.farmer
	client.verify_otp(
		farmer_id=farmer_db_id,
		transaction_id=transaction_id,
		otp_code=otp_code,
	)

	# In the reverted schema, the only valid options are Draft, Pending OTP, and Approved.
	# Keep status at "Pending OTP" and record the verification timestamp.
	frappe.db.set_value(
		"A2C Consent Request",
		consent_request,
		{
			"status": "Pending OTP",
			"otp_verified_at": now_datetime(),
		},
	)
	# nosemgrep: frappe-manual-commit -- reviewed: persist OTP-verified status before returning
	frappe.db.commit()

	return success_response(
		data={
			"lead_id": lead_id,
			"consent_request": consent_request,
			"transaction_id": transaction_id,
			"status": "OTP Verified",
		},
		message="OTP verified successfully. Proceed to submit consent.",
	)


def _save_direct_consent_response_to_lead(consent_request, response_data, openg2p_consent_id):
	"""
	OpenG2P now returns the farmer profile directly in the submit_consent
	response instead of delivering it later via the WebSub webhook. When that
	inline payload (`response_data`) is present, reshape it into the webhook
	envelope and route it through `validate_and_enqueue_consent` — the same
	internal, queued path the real webhook uses — so the farmer profile is
	persisted onto the lead by a background job exactly as a webhook delivery
	would.

	No-op (returns False) when there is no payload, leaving the async webhook
	path untouched. Non-fatal on failure.
	"""
	if not response_data:
		return False

	# validate_and_enqueue_consent looks up the A2C Consent Request by
	# consent.id == openg2p_consent_id, then enqueues process_consent_data,
	# which reads the farmer dict from selected_data.
	try:
		payload = {
			"source": "frappe_direct_response",
			"event_type": "WEBSUB_INDIVIDUAL_UPDATED",
			"published_at": str(now_datetime()),
			"consent": {
				"id": openg2p_consent_id,
				"consent_creation_request_id": str(openg2p_consent_id),
				"status": "approved",
				"approved_at": str(now_datetime()),
			},
			"selected_data": response_data,
		}
		# enforce_permission=False: called in-process, not via authenticated HTTP.
		validate_and_enqueue_consent(payload, enforce_permission=False)
		frappe.logger().info(f"Direct consent response enqueued for {consent_request}")
		return True
	except Exception as e:
		frappe.logger().warning(f"Direct consent response enqueue failed: {e}")
		frappe.log_error(frappe.get_traceback(), "Direct Consent Response Save")
		return False


# 5 ───────────────────────────────────────────────────────────────────────────
@frappe.whitelist(allow_guest=False)
@validate_request(SubmitConsentSchema)
@handle_api_errors
def submit_consent(**kwargs):
	"""Attach the consent details, submit to OpenG2P, and finalise the lead.
	consent_type, purpose, allowed data fields and the attachment are supplied
	here (not at request_otp). → client.submit_consent"""
	lead_id = kwargs.get("lead_id")
	consent_request = kwargs.get("consent_request")
	consent_type = kwargs.get("consent_type")
	consent_reason_id = kwargs.get("consent_reason_id", 1)
	validity_months = kwargs.get("validity_months")
	consent_form_filename = kwargs.get("consent_form_filename")
	consent_form_base64 = kwargs.get("consent_form_base64")
	allowed_data_field_ids = kwargs.get("allowed_data_field_ids") or []

	frappe.has_permission("A2C Lead", "write", doc=lead_id, throw=True)
	frappe.has_permission("A2C Consent Request", "write", doc=consent_request, throw=True)

	# 1. Idempotency Check (Pessimistic lock row first to check status safely)
	status = frappe.db.get_value("A2C Consent Request", consent_request, "status", for_update=True)
	if not status:
		frappe.throw(
			_("A2C Consent Request '{0}' not found.").format(consent_request), frappe.DoesNotExistError
		)

	if status == "Approved":
		cr_doc = frappe.get_doc("A2C Consent Request", consent_request)
		if cr_doc.lead != lead_id:
			frappe.throw(_("Consent Request does not belong to the specified lead."), frappe.ValidationError)
		return success_response(
			data={
				"lead_id": cr_doc.lead,
				"consent_request": cr_doc.name,
				"status": "Approved",
				"openg2p_consent_id": cr_doc.openg2p_consent_id,
				"consent_receipt": cr_doc.consent_receipt,
				"farmer_preview": _get_farmer_preview_from_lead(cr_doc.lead),
			},
			message="Consent already submitted and approved.",
		)

	# 2. Retrieve locked request doc and client
	cr_doc, client, transaction_id = _get_consent_request_and_client(
		consent_request, expected_status="Pending OTP", check_verified=True
	)

	receipt = None
	openg2p_consent_id = None
	farmer_preview = {}

	# Define savepoint for rollback of partial writes on failure
	frappe.db.savepoint("before_submit")

	try:
		# Single farmer lookup, reused for both submission and the lead preview.
		farmer_dict = client.get_farmer_by_fayda_id(cr_doc.farmer_fayda_id)
		farmer_db_id = farmer_dict.get("id")

		# Decode the consent form once: keep a copy on the doc and forward the
		# base64 straight to OpenG2P.
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
			dn=lead_id,
			is_private=1,
		)
		attachment_base64 = base64.b64encode(file_content).decode("utf-8")

		# Map the requested field ids to names for the child table.
		fields_res = client.get_consent_allowed_fields()
		fields_data = fields_res.get("data") if isinstance(fields_res, dict) else []
		field_map = {f["id"]: f["name"] for f in fields_data if isinstance(f, dict) and "id" in f}

		# Persist the consent details onto the request now that they're known.
		cr_doc.consent_type = consent_type
		cr_doc.purpose = consent_reason_id
		cr_doc.consent_form_attachment = saved_file.file_url
		if validity_months:
			from frappe.utils import add_days, today

			cr_doc.validity_from = today()
			cr_doc.validity_to = add_days(cr_doc.validity_from, days=int(validity_months) * 30)
		cr_doc.set("requested_data_fields", [])
		for f_id in allowed_data_field_ids:
			cr_doc.append(
				"requested_data_fields",
				{
					"field_value": str(f_id),
					"field_name": field_map.get(int(f_id), "OpenG2P Data Field ID"),
				},
			)
		cr_doc.save(ignore_permissions=False)

		# client call — submit the consent with the details provided in this request.
		consent_response = client.submit_consent(
			farmer_db_id=farmer_db_id,
			consent_type=consent_type,
			consent_reason_id=consent_reason_id,
			allowed_data_field_ids=allowed_data_field_ids,
			attachment_base64=attachment_base64,
			attachment_filename=consent_form_filename,
			fayda_otp_transaction_id=transaction_id,
			validity_months=validity_months,
		)
		data_block = consent_response.get("data", {})
		openg2p_consent_id = data_block.get("consent_id")

		# OpenG2P now performs (auto-)approval upstream and returns the outcome.
		# Do NOT assume success: only mark Approved when OpenG2P actually
		# approved. On an auto-approval failure we raise ConsentNotApproved,
		# which the except block below records as a persisted "Rejected" status
		# (with the upstream reason) rather than a false "Approved".
		upstream_status = (data_block.get("status") or "").strip().lower()
		auto_approval_failed = data_block.get("auto_approval_failed")
		is_approved = (
			upstream_status in ("approved", "granted", "active")
			or (data_block.get("auto_approved") is True and not auto_approval_failed)
			# Backwards-compat: pre-drift responses omit status entirely but
			# returning a consent_id has always signalled a successful create.
			or (not upstream_status and data_block.get("auto_approved") is None and openg2p_consent_id)
		)

		if not is_approved or auto_approval_failed:
			reason = data_block.get("error_details") or upstream_status or "unknown reason"
			raise ConsentNotApproved(reason)

		frappe.db.set_value(
			"A2C Consent Request",
			consent_request,
			{
				"status": "Approved",
				"openg2p_consent_id": openg2p_consent_id,
			},
		)

		# TEMPORARY: OpenG2P now returns the farmer profile inline in the
		# response. If present, persist it to the lead like the webhook would;
		# otherwise this is a no-op and the async WebSub path still applies.
		_save_direct_consent_response_to_lead(
			consent_request,
			data_block.get("response_data"),
			openg2p_consent_id,
		)

		# Generate and store the signed consent receipt.
		receipt = generate_consent_receipt(consent_request)
		frappe.db.set_value(
			"A2C Consent Request", consent_request, "consent_receipt", receipt.get("signature")
		)

		# Persist the farmer profile onto the lead and sync the headline fields.
		farmer_preview = _save_farmer_data_to_lead(lead_id, farmer_dict, openg2p_consent_id)

		given_name = farmer_preview.get("given_name", "")
		family_name = farmer_preview.get("family_name", "")
		phone_list = farmer_preview.get("phone_no") or []
		mobile = phone_list[0] if isinstance(phone_list, list) and phone_list else ""

		if given_name or family_name or mobile:
			try:
				lead = frappe.get_doc("A2C Lead", lead_id)
				if given_name:
					lead.first_name = given_name
				if family_name:
					lead.last_name = family_name
				if mobile and not lead.phone_number:
					lead.phone_number = mobile
				lead.save(ignore_permissions=False)
			except Exception as e:
				frappe.logger().warning(f"Could not save farmer name fields: {e}")

		frappe.db.commit()

	except ConsentNotApproved as e:
		# Upstream (OpenG2P) declined the consent. Roll back the partial writes,
		# then persist a durable "Rejected" outcome (the rollback is scoped to
		# the savepoint, so this post-rollback set_value + commit survives) so
		# the request is auditable/retryable instead of stuck at "Pending OTP".
		frappe.db.rollback(save_point="before_submit")
		frappe.db.set_value("A2C Consent Request", consent_request, "status", "Rejected")
		frappe.db.commit()
		frappe.throw(
			_("Consent was not approved by OpenG2P: {0}").format(str(e)),
			frappe.ValidationError,
		)

	except Exception as e:
		frappe.db.rollback(save_point="before_submit")
		frappe.log_error(frappe.get_traceback(), f"Consent submission failed: {e!s}")
		raise e

	return success_response(
		data={
			"lead_id": lead_id,
			"consent_request": consent_request,
			"status": "Approved",
			"openg2p_consent_id": openg2p_consent_id,
			"consent_receipt": receipt.get("signature") if receipt else None,
			"farmer_preview": farmer_preview,
		},
		message="Consent submitted and approved successfully.",
	)


# 6 ───────────────────────────────────────────────────────────────────────────
@frappe.whitelist(allow_guest=False)
@handle_api_errors
def get_consent_reasons():
	"""Fetch all active consent reasons from OpenG2P. → client.get_consent_reasons"""
	client = OpenG2PConsentClient()
	response = client.get_consent_reasons()
	return success_response(
		data=response.get("data") if response else [],
		message="Consent reasons retrieved successfully.",
	)


# 7 ───────────────────────────────────────────────────────────────────────────
@frappe.whitelist(allow_guest=False)
@validate_request(GetConsentAllowedFieldsSchema)
@handle_api_errors
def get_consent_allowed_fields(**kwargs):
	"""Fetch the allowed data fields for the consent partner.
	→ client.get_consent_allowed_fields"""
	client = OpenG2PConsentClient()
	response = client.get_consent_allowed_fields()
	return success_response(
		data=response.get("data") if response else [],
		message="Allowed data fields retrieved successfully.",
	)
