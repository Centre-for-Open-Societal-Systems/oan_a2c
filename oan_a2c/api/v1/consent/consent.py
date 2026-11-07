import base64
import json

import frappe
from frappe import _
from frappe.utils import now_datetime
from oan_auth_service.api.utils import rate_limit_per_user
from pydantic import BaseModel, Field

from oan_a2c.a2c_marketplace.roles import FARMER_ROLE
from oan_a2c.api.utils import handle_api_errors, success_response, to_tz_aware_iso, validate_request
from oan_a2c.api.v1.webhook_consent_data import validate_and_enqueue_consent

from .bene360 import to_selected_data
from .openg2p_client import OpenG2PClient
from .utils import generate_consent_receipt

# ─── Pydantic Validation Schemas ──────────────────────────────────────────────


class SearchFarmerSchema(BaseModel):
	fayda_id: str = Field(..., min_length=1)
	lead_id: str | None = None


class RequestOTPSchema(BaseModel):
	fayda_id: str = Field(..., min_length=1)
	lead_id: str | None = None
	idempotency_key: str | None = None


class VerifyOTPSchema(BaseModel):
	lead_id: str | None = None
	otp_code: str = Field(..., min_length=1)
	transaction_id: str | None = None
	consent_request: str = Field(..., min_length=1)


class ResendOTPSchema(BaseModel):
	lead_id: str | None = None
	consent_request: str = Field(..., min_length=1)


class SubmitConsentSchema(BaseModel):
	lead_id: str | None = None
	consent_request: str = Field(..., min_length=1)
	consent_type: str | None = "specific"
	# Optional: the OTP itself is the farmer's consent now. When sent, the signed
	# form is kept as a local record on the lead / consent request.
	consent_form_filename: str | None = None
	consent_form_base64: str | None = None
	# Deprecated: purpose, data scopes and validity are fixed when the OTP request
	# is raised (A2C OpenG2P Settings). Still accepted so older clients don't 400.
	consent_reason_id: int | None = None
	validity_months: int | None = None
	allowed_data_field_ids: list[int] | None = None


class GetConsentAllowedFieldsSchema(BaseModel):
	pass


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


def _lock_consent_request(consent_request, expected_status):
	"""Row-lock the A2C Consent Request and check it is in `expected_status`."""
	# Pessimistic lock to prevent race conditions
	status = frappe.db.get_value("A2C Consent Request", consent_request, "status", for_update=True)
	if not status:
		frappe.throw(
			_("A2C Consent Request '{0}' not found.").format(consent_request), frappe.DoesNotExistError
		)

	cr_doc = frappe.get_doc("A2C Consent Request", consent_request)
	if cr_doc.status != expected_status:
		frappe.throw(
			_("A2C Consent Request '{0}' must be in status '{1}'. Current status: '{2}'.").format(
				consent_request, expected_status, cr_doc.status
			),
			frappe.ValidationError,
		)
	if not cr_doc.otp_transaction_id:
		frappe.throw(
			_("Transaction ID is missing for consent request '{0}'.").format(consent_request),
			frappe.ValidationError,
		)
	return cr_doc


def _lead_for_consent_request(cr_doc, claimed_lead_id=None) -> str | None:
	"""The lead a consent request belongs to, taken from the request itself.

	`lead_id` is an optional request parameter, so it can never be the thing that
	decides which lead is written to -- a caller that simply omits it must not
	thereby skip the ownership check. The server-side link is authoritative; a
	client-supplied `lead_id` is only ever accepted as an assertion to verify.

	Returns None for a self-service consent, which is legitimately lead-less.
	"""
	actual = cr_doc.reference_name if cr_doc.reference_doctype == "A2C Lead" else None
	if claimed_lead_id and claimed_lead_id != actual:
		frappe.throw(_("Consent Request does not belong to the specified lead."), frappe.ValidationError)
	return actual


# 1 ───────────────────────────────────────────────────────────────────────────
@validate_request(SearchFarmerSchema)
@handle_api_errors
@rate_limit_per_user(limit=20, window=60)
def search_farmer(**kwargs):
	"""Find a farmer by Fayda ID. → Agent API lookup_beneficiary"""
	fayda_id = kwargs.get("fayda_id")
	lead_id = kwargs.get("lead_id")

	if lead_id:
		frappe.has_permission("A2C Lead", "read", doc=lead_id, throw=True)
	else:
		# No lead means the self-service (B2C) entry point: the farmer is doing the
		# Fayda lookup that precedes request_otp, before any record exists. Farmers hold
		# no DocPerm on A2C Lead, so they have to be admitted by role here.
		roles = frappe.get_roles()
		if not (
			frappe.has_permission("A2C Lead", "read") or FARMER_ROLE in roles or "System Manager" in roles
		):
			frappe.throw(_("Not permitted to search farmer profile"), frappe.PermissionError)

	farmer = OpenG2PClient().lookup_beneficiary(fayda_id)

	# mobile / phone / profile_image_url are kept for client compatibility; the
	# registry lookup does not return them.
	return success_response(
		data={
			"farmer": {
				"id": farmer["internal_record_id"],
				"name": farmer.get("record_name"),
				"mobile": None,
				"phone": None,
				"profile_image_url": None,
				"type": "national_id",
				"register_id": farmer.get("register_id"),
				"eligible": farmer.get("eligible"),
				"reason": farmer.get("reason"),
			}
		},
		message="Farmer found successfully.",
	)


# 2 ───────────────────────────────────────────────────────────────────────────
@handle_api_errors
def get_partner_allowed_data_field_ids():
	"""IDs of the data scopes the partner may request (positions in
	get_consent_allowed_fields). → Consent Manager bindings"""
	scopes = [s for b in OpenG2PClient().get_bindings() for s in b["allowed_data_scopes"]]
	return success_response(
		data={"allowed_data_field_ids": list(range(1, len(scopes) + 1))},
		message="Allowed data field IDs retrieved successfully.",
	)


# 3 ───────────────────────────────────────────────────────────────────────────
@validate_request(RequestOTPSchema)
@handle_api_errors
@rate_limit_per_user(limit=5, window=60)
def request_otp(**kwargs):
	"""Open a pending consent request; the Consent Manager sends the farmer an OTP."""
	fayda_id = kwargs.get("fayda_id")
	lead_id = kwargs.get("lead_id")
	idempotency_key = kwargs.get("idempotency_key")

	if lead_id:
		lead_status = frappe.db.get_value("A2C Lead", lead_id, "status")
		if not lead_status:
			frappe.throw(_("A2C Lead {0} not found").format(lead_id), frappe.DoesNotExistError)
		if lead_status in ["Converted", "Rejected"]:
			frappe.throw(
				_("Cannot request new consent because the lead is already {0}.").format(lead_status),
				frappe.ValidationError,
			)
		frappe.has_permission("A2C Lead", "write", doc=lead_id, throw=True)
	elif FARMER_ROLE not in frappe.get_roles():
		# Every consent request must be anchored to an identity: the farmer's own
		# account for self-service, or a lead when raised on their behalf. Without
		# either, nothing links the request to a lead or a profile afterwards -- the
		# webhook cannot resolve which profile to write, and the record is
		# unreachable by every consent lookup on A2C Loan Application.
		#
		# This is also the role gate for the lead-less path (mirroring search_farmer):
		# a farmer may raise consent for themselves, and anyone acting for a farmer
		# must name a lead, which the write check above then holds them to. Callers
		# who are neither -- a Bank Agent, say -- fail both branches.
		frappe.throw(
			_("lead_id is required when requesting consent on a farmer's behalf."),
			frappe.ValidationError,
		)

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
		client = OpenG2PClient()

		# Resolve the farmer's registry record first, so an unknown Fayda ID fails
		# before a consent request is opened or an OTP is sent.
		farmer = client.lookup_beneficiary(fayda_id)

		# Open the pending consent request.
		#
		# Two links, answering two different questions:
		#
		#   reference_doctype/reference_name  "which lead is THIS consent for?"
		#   A2C Lead.consent_id               "what is this lead's CURRENT consent?"
		#
		# The pointer on the consent request is the relationship and cannot be
		# dropped in favour of the one on the lead: request_otp opens a fresh consent
		# request on every call (OTP expiry, Rejected, Failed), so the lead's pointer
		# is overwritten each time. A consent webhook arrives holding a consent
		# request name and has to resolve its lead -- only this direction can still
		# answer that for an earlier, superseded attempt whose delivery is in flight.
		doc = frappe.new_doc("A2C Consent Request")
		doc.farmer = farmer["internal_record_id"]
		doc.farmer_fayda_id = fayda_id
		doc.partner = client.settings.partner_id
		if lead_id:
			doc.reference_doctype = "A2C Lead"
			doc.reference_name = lead_id
		doc.status = "Pending OTP"
		doc.insert(ignore_permissions=False)

		if lead_id:
			# Denormalised "latest attempt" cache, mirroring A2C Farmer Profile.consent_id.
			# db.set_value deliberately: a consent write must not run (or trip) unrelated
			# Lead validation such as the phone-uniqueness and verification gates.
			frappe.db.set_value("A2C Lead", lead_id, "consent_id", doc.name, update_modified=False)
		else:
			from oan_a2c.a2c_marketplace.permissions import get_user_farmer_profile, is_farmer

			user = frappe.session.user
			if is_farmer(user):
				profile_name = get_user_farmer_profile(user)
				if profile_name:
					frappe.db.set_value(
						"A2C Farmer Profile", profile_name, "consent_id", doc.name, update_modified=False
					)

		# Consent Manager call — raises the OTP consent request; the code goes to the farmer.
		cm_request = client.create_otp_request(fayda_id)

		# Record what was actually asked for: purpose and scopes are fixed here, not at submit.
		doc.otp_transaction_id = cm_request["id"]
		doc.purpose_id = doc.purpose = (cm_request.get("purpose") or {}).get("code")
		doc.set("requested_data_fields", [])
		for grant in cm_request.get("grants") or []:
			for scope in grant.get("data_scopes") or []:
				doc.append("requested_data_fields", {"field_value": scope, "field_name": scope})
		doc.save(ignore_permissions=False)

		res_payload = success_response(
			data={
				"consent_request": doc.name,
				"transaction_id": cm_request["id"],
				"masked_phone": cm_request.get("otp_channel") or "XXXX",
				"otp_expires_at": cm_request.get("otp_expires_at"),
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
@validate_request(VerifyOTPSchema)
@handle_api_errors
def verify_otp(**kwargs):
	"""Verify the farmer's OTP; a correct code grants the consent in the Consent
	Manager. → client.verify_otp"""
	lead_id = kwargs.get("lead_id")
	otp_code = kwargs.get("otp_code")
	consent_request = kwargs.get("consent_request")

	frappe.has_permission("A2C Consent Request", "write", doc=consent_request, throw=True)

	cr_doc = _lock_consent_request(consent_request, "Pending OTP")
	lead_id = _lead_for_consent_request(cr_doc, lead_id)
	if lead_id:
		frappe.has_permission("A2C Lead", "write", doc=lead_id, throw=True)

	consent = OpenG2PClient().verify_otp(cr_doc.otp_transaction_id, otp_code)

	frappe.get_doc(
		{
			"doctype": "Comment",
			"comment_type": "Info",
			"reference_doctype": "A2C Consent Request",
			"reference_name": consent_request,
			"content": f"OpenG2P Verify OTP Data: {frappe.as_json(consent)}",
		}
	).insert(ignore_permissions=True)

	# The consent now exists upstream; the farmer data is fetched against it in
	# submit_consent, which moves the request to Approved.
	frappe.db.set_value(
		"A2C Consent Request",
		consent_request,
		{
			"status": "OTP Verified",
			"otp_verified_at": now_datetime(),
			"openg2p_consent_id": consent["consent_id"],
			"validity_from": (consent.get("valid_from") or "")[:10] or None,
			"validity_to": (consent.get("valid_until") or "")[:10] or None,
		},
	)

	return success_response(
		data={
			"lead_id": lead_id,
			"consent_request": consent_request,
			"transaction_id": cr_doc.otp_transaction_id,
			"openg2p_consent_id": consent["consent_id"],
			"status": "OTP Verified",
		},
		message="OTP verified successfully. Proceed to submit consent.",
	)


# 4b ──────────────────────────────────────────────────────────────────────────
@validate_request(ResendOTPSchema)
@handle_api_errors
@rate_limit_per_user(limit=5, window=60)
def resend_otp(**kwargs):
	"""Send the farmer a new OTP; the previous code stops working. → client.resend_otp"""
	consent_request = kwargs.get("consent_request")

	frappe.has_permission("A2C Consent Request", "write", doc=consent_request, throw=True)
	cr_doc = _lock_consent_request(consent_request, "Pending OTP")
	lead_id = _lead_for_consent_request(cr_doc, kwargs.get("lead_id"))
	if lead_id:
		frappe.has_permission("A2C Lead", "write", doc=lead_id, throw=True)

	cm_request = OpenG2PClient().resend_otp(cr_doc.otp_transaction_id)
	return success_response(
		data={
			"consent_request": consent_request,
			"transaction_id": cr_doc.otp_transaction_id,
			"masked_phone": cm_request.get("otp_channel") or "XXXX",
			"otp_expires_at": cm_request.get("otp_expires_at"),
		},
		message="OTP resent successfully.",
	)


# 5 ───────────────────────────────────────────────────────────────────────────
@validate_request(SubmitConsentSchema)
@handle_api_errors
def submit_consent(**kwargs):
	"""Fetch the farmer's registry data against the granted consent, save it to the
	lead / farmer profile, and mark the consent Approved. → client.fetch_beneficiary360"""
	lead_id = kwargs.get("lead_id")
	consent_request = kwargs.get("consent_request")
	consent_form_filename = kwargs.get("consent_form_filename")
	consent_form_base64 = kwargs.get("consent_form_base64")

	frappe.has_permission("A2C Consent Request", "write", doc=consent_request, throw=True)

	# 1. Idempotency Check (Pessimistic lock row first to check status safely)
	status = frappe.db.get_value("A2C Consent Request", consent_request, "status", for_update=True)
	if not status:
		frappe.throw(
			_("A2C Consent Request '{0}' not found.").format(consent_request), frappe.DoesNotExistError
		)

	# The lead comes from the consent request, not from the caller -- see
	# _lead_for_consent_request. Resolved before any branch so both the replay path
	# and the live path are checked identically.
	lead_id = _lead_for_consent_request(frappe.get_doc("A2C Consent Request", consent_request), lead_id)
	if lead_id:
		frappe.has_permission("A2C Lead", "write", doc=lead_id, throw=True)

	if status == "Approved":
		cr_doc = frappe.get_doc("A2C Consent Request", consent_request)
		return success_response(
			data={
				"lead_id": lead_id,
				"consent_request": cr_doc.name,
				"status": "Approved",
				"openg2p_consent_id": cr_doc.openg2p_consent_id,
				"consent_receipt": cr_doc.consent_receipt,
				"farmer_preview": _get_farmer_preview_from_lead(lead_id) if lead_id else {},
			},
			message="Consent already submitted and approved.",
		)

	# 2. Retrieve the locked, OTP-verified request
	cr_doc = _lock_consent_request(consent_request, "OTP Verified")
	if not cr_doc.openg2p_consent_id:
		frappe.throw(_("Consent request '{0}' has no granted consent.").format(consent_request))

	# Define savepoint for rollback of partial writes on failure
	frappe.db.savepoint("before_submit")

	try:
		if consent_form_base64:
			if not consent_form_filename:
				frappe.throw(_("consent_form_filename is required with consent_form_base64."))
			b64_data = (
				consent_form_base64.split(",", 1)[1] if "," in consent_form_base64 else consent_form_base64
			)
			# Add padding and use urlsafe variant to accept both standard and URL-safe base64
			b64_data += "=" * (-len(b64_data) % 4)
			file_content = base64.urlsafe_b64decode(b64_data)

			# Consent form must be a PDF, max 10 MB. Check the decoded bytes: the size
			# on the real content (not the base64 length), and the %PDF- magic header so
			# a mislabeled/renamed non-PDF is rejected regardless of filename extension.
			if len(file_content) > 10 * 1024 * 1024:
				frappe.throw(_("Consent form exceeds the 10 MB maximum size."), frappe.ValidationError)
			if not (str(consent_form_filename).lower().endswith(".pdf") and file_content[:5] == b"%PDF-"):
				frappe.throw(_("Consent form must be a PDF file."), frappe.ValidationError)

			# Build the File doc directly with ignore_permissions rather than save_file():
			# save_file dedupes by content hash, so an identical consent PDF saved by another
			# user leaves a private File that validate_private_file_access blocks this caller
			# from re-attaching. This is a trusted server-side write, so bypass that check.
			#
			# Attach to the lead when there is one, otherwise to the consent request
			# itself. NOT to A2C Farmer Profile keyed on `cr_doc.farmer`: that field
			# holds the registry record id, not a Farmer Profile name.
			file_doc = frappe.get_doc(
				{
					"doctype": "File",
					"file_name": consent_form_filename,
					"content": file_content,
					"attached_to_doctype": "A2C Lead" if lead_id else "A2C Consent Request",
					"attached_to_name": lead_id or cr_doc.name,
					"is_private": 1,
				}
			)
			file_doc.insert(ignore_permissions=True)
			cr_doc.consent_form_attachment = file_doc.file_url

		cr_doc.consent_type = kwargs.get("consent_type")
		cr_doc.save(ignore_permissions=False)

		# Aggregation Layer call — the farmer's registry data under the granted consent.
		record = OpenG2PClient().fetch_beneficiary360(cr_doc.farmer_fayda_id, cr_doc.openg2p_consent_id)
		selected_data = to_selected_data(record)
		farmer = selected_data["farmer"]
		if not farmer.get("Full Name"):
			frappe.throw(
				_("No farmer registry data was returned for Fayda ID '{0}'.").format(cr_doc.farmer_fayda_id),
				frappe.DoesNotExistError,
			)

		# A registry that failed or timed out is reported, not fatal: keep what came back.
		warnings = (record.get("meta") or {}).get("warnings") or []
		if warnings:
			frappe.get_doc(
				{
					"doctype": "Comment",
					"comment_type": "Info",
					"reference_doctype": "A2C Consent Request",
					"reference_name": consent_request,
					"content": f"Aggregation Layer warnings: {frappe.as_json(warnings)}",
				}
			).insert(ignore_permissions=True)

		frappe.db.set_value("A2C Consent Request", consent_request, "status", "Approved")

		# Route the data through the same queued path the consent webhook uses, so
		# process_consent_data stays the single writer of A2C Farmer Profile.
		# validate_and_enqueue_consent looks the request up by consent.id ==
		# openg2p_consent_id. enforce_permission=False: called in-process, not via
		# authenticated HTTP. enqueue_after_commit: process_consent_data must run in
		# its own transaction only after this one commits, so it sees the committed
		# status and its rollback/commit can never corrupt this request.
		consent_payload = {
			"source": "aggregation_layer",
			"event_type": "WEBSUB_INDIVIDUAL_UPDATED",
			"published_at": to_tz_aware_iso(now_datetime()),
			"consent": {
				"id": cr_doc.openg2p_consent_id,
				"consent_creation_request_id": cr_doc.otp_transaction_id,
				"status": "approved",
				"approved_at": to_tz_aware_iso(now_datetime()),
			},
			# The registry record id the request was opened against in request_otp.
			"farmer": {"id": cr_doc.farmer, "name": farmer.get("Full Name")},
			"selected_data": selected_data,
		}
		validate_and_enqueue_consent(consent_payload, enforce_permission=False, enqueue_after_commit=True)

		# Generate and store the signed consent receipt.
		receipt = generate_consent_receipt(consent_request)
		frappe.db.set_value(
			"A2C Consent Request", consent_request, "consent_receipt", receipt.get("signature")
		)

		# Sync the lead's headline fields from the registry record.
		farmer_preview = {}
		if lead_id:
			parts = farmer["Full Name"].split()
			mobile = (farmer.get("Mobile Number") or [""])[0]
			farmer_preview = {
				"given_name": parts[0].title(),
				"family_name": " ".join(p.title() for p in parts[1:]),
				"email": "",
				"phone_no": [mobile] if mobile else [],
			}
			try:
				lead = frappe.get_doc("A2C Lead", lead_id)
				lead.consent_data = json.dumps(consent_payload, indent=2, ensure_ascii=False)
				lead.first_name = farmer_preview["given_name"]
				if farmer_preview["family_name"]:
					lead.last_name = farmer_preview["family_name"]
				if mobile and not lead.phone_number:
					lead.phone_number = mobile
				# ignore_permissions=False is required here for row-level permission enforcement.
				lead.save(ignore_permissions=False)
			except Exception as e:
				frappe.logger().warning(f"Could not save farmer fields to lead {lead_id}: {e}")

	except Exception as e:
		# The consent stays granted upstream and the request stays "OTP Verified",
		# so calling submit_consent again simply re-fetches the data.
		frappe.db.rollback(save_point="before_submit")
		frappe.log_error(frappe.get_traceback(), f"Consent submission failed: {e!s}")
		raise e

	return success_response(
		data={
			"lead_id": lead_id,
			"consent_request": consent_request,
			"status": "Approved",
			"openg2p_consent_id": cr_doc.openg2p_consent_id,
			"consent_receipt": receipt.get("signature") if receipt else None,
			"farmer_preview": farmer_preview,
		},
		message="Consent submitted and approved successfully.",
	)


# 6 ───────────────────────────────────────────────────────────────────────────
@handle_api_errors
def get_consent_reasons():
	"""Purposes the partner may raise consent for. → Consent Manager bindings"""
	purposes = list(dict.fromkeys(p for b in OpenG2PClient().get_bindings() for p in b["allowed_purposes"]))
	return success_response(
		data=[{"id": i, "name": code, "description": None} for i, code in enumerate(purposes, start=1)],
		message="Consent reasons retrieved successfully.",
	)


# 7 ───────────────────────────────────────────────────────────────────────────
@validate_request(GetConsentAllowedFieldsSchema)
@handle_api_errors
def get_consent_allowed_fields(**kwargs):
	"""Data scopes the partner may request, per data controller. → Consent Manager bindings"""
	scopes = [
		(b["data_controller"], s) for b in OpenG2PClient().get_bindings() for s in b["allowed_data_scopes"]
	]
	return success_response(
		data=[
			{"id": i, "name": scope, "data_controller": controller}
			for i, (controller, scope) in enumerate(scopes, start=1)
		],
		message="Allowed data fields retrieved successfully.",
	)
