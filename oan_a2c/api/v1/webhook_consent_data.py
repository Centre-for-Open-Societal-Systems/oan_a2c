import json
from typing import Annotated, Any, Optional
from typing import Any as DummyAny

import frappe
from pydantic import BaseModel, Field, ValidationError

from oan_a2c.api.utils import (
	from_tz_aware_iso,
	handle_api_errors,
	notify_lead_event,
	success_response,
	validate_request,
)
from oan_a2c.api.v1.consent.bene360 import extract_profile_fields


class SelectedDataSchema(BaseModel):
	pass


class FarmerInfoSchema(BaseModel):
	# `id` is the OpenG2P farmer id and is the identity this webhook upserts an
	# A2C Farmer Profile on, so it is required rather than optional. OpenG2P always
	# sends it; declaring that here means a malformed payload is rejected at
	# validation with a clear error, instead of falling through to a weaker key and
	# silently creating a profile nothing can later match. A string is the registry
	# record id from the Agent API (the Aggregation Layer path); an int is the
	# legacy OpenG2P farmer id.
	id: Annotated[int, Field(ge=1)] | Annotated[str, Field(min_length=1)]
	farmer_id: Any | None = None
	name: str | None = None


class ConsentInfoSchema(BaseModel):
	# Consent Manager consent ids are strings; legacy OpenG2P ids were ints.
	id: int | str | None = None
	consent_creation_request_id: str = Field(..., min_length=1)
	consent_type: str | None = None
	status: str | None = None
	approved_at: str | None = None
	validity_from: str | None = None
	validity_to: str | None = None


class ReceiveConsentDataSchema(BaseModel):
	source: str | None = None
	event_type: str | None = None
	published_at: str | None = None
	consent: ConsentInfoSchema
	farmer: FarmerInfoSchema
	# OpenG2P always sends this as a JSON object. Declaring the shape rather than
	# `Any` keeps a malformed payload a clean 400 at validation, instead of falling
	# through to a profile built entirely from defaults.
	selected_data: dict[str, Any] | None = None


def download_cert_photo_to_file(url, lead_id):
	"""Download a certificate photo from an external URL and store it as a
	Frappe File attached to the A2C Lead. Returns the local ``file_url`` on
	success, or the original ``url`` unchanged if the download fails (non-fatal).
	"""
	if not url or not isinstance(url, str) or not url.lower().startswith(("http://", "https://")):
		return url

	try:
		import ipaddress
		import os
		import socket
		from urllib.parse import urlparse

		import requests
		from frappe.utils.file_manager import save_file

		parsed = urlparse(url)
		if not parsed.hostname:
			return url
		try:
			ip_str = socket.gethostbyname(parsed.hostname)
			ip = ipaddress.ip_address(ip_str)
			if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_unspecified:
				frappe.logger().warning(
					f"SSRF blocked: cert photo URL {url} resolved to internal IP {ip_str}"
				)
				return url
		except Exception as e:
			frappe.logger().warning(f"SSRF check failed for hostname {parsed.hostname}: {e}")
			return url

		resp = requests.get(url, timeout=15)

		resp.raise_for_status()

		fname = os.path.basename(urlparse(url).path) or "certificate.jpg"
		saved = save_file(
			fname=fname,
			content=resp.content,
			dt="A2C Lead",
			dn=lead_id,
			is_private=1,
		)
		return saved.file_url
	except Exception as e:
		frappe.logger().warning(f"Certificate photo download failed for {url}: {e}")
		frappe.log_error(frappe.get_traceback(), "Cert Photo Download")
		return url


def process_consent_data(data, consent_doc_name, consent_request_id):
	"""
	Background worker function that safely processes the OpenG2P payload.
	"""
	# Set user context based on A2C Consent Request owner (Option 1 & 2)
	owner = frappe.db.get_value("A2C Consent Request", consent_doc_name, "owner")
	if not owner or not frappe.db.exists("User", owner):
		raise frappe.PermissionError(
			f"Cannot process consent data: Consent Request {consent_doc_name} lacks a valid owner user."
		)
	# nosemgrep: frappe-setuser -- reviewed: background worker sets context to the consent request owner
	frappe.set_user(owner)

	# Bound before the try so it's always safe to reference in the except block,
	# even if the failure happens before the lead link is resolved below.
	lead_id = None

	try:
		validated = ReceiveConsentDataSchema.model_validate(data)
		consent_info = validated.consent

		# Update Consent Request status and validity details
		updates_dict = {}
		new_status = consent_info.status
		if new_status:
			updates_dict["status"] = new_status.capitalize() if new_status.islower() else new_status

		if validated.published_at:
			# published_at is a tz-aware ISO 8601 string (see to_tz_aware_iso);
			# websub_delivered_at is a Datetime column that rejects it, so
			# normalize to a naive system-tz datetime before persisting.
			updates_dict["websub_delivered_at"] = from_tz_aware_iso(validated.published_at)

		if consent_info.validity_from:
			updates_dict["validity_from"] = consent_info.validity_from.split(" ")[0].split("T")[0]

		if consent_info.validity_to:
			updates_dict["validity_to"] = consent_info.validity_to.split(" ")[0].split("T")[0]

		raw_selected_data = validated.selected_data or {}
		raw_consent_data_str = json.dumps(raw_selected_data)
		# Only written when the payload actually carried data. OpenG2P redelivers
		# status-only events with no selected_data, and an unconditional write would
		# overwrite the previously captured blob with "{}".
		if validated.selected_data is not None:
			updates_dict["raw_consent_data"] = raw_consent_data_str

		if updates_dict:
			frappe.db.set_value("A2C Consent Request", consent_doc_name, updates_dict)

		# Parse Farmer Data
		# Both `farmer` and its `id` are required by the schema, so this is always a
		# populated block by the time validation has passed.
		farmer_data = validated.farmer
		# selected_data is the Aggregation Layer's beneficiary360 record.
		profile_fields = extract_profile_fields(raw_selected_data)
		if not profile_fields.get("first_name"):
			name_parts = (farmer_data.name or "").split(" ")
			profile_fields["first_name"] = name_parts[0]
			profile_fields["last_name"] = " ".join(name_parts[1:])
		phone_number = profile_fields.get("phone_number") or ""

		# Fetch Consent Request to check links
		consent_doc = frappe.get_doc("A2C Consent Request", consent_doc_name)
		# Resolved from the consent request, never from A2C Lead.consent_id: that field
		# only holds the lead's *latest* attempt, so a superseded request -- which is
		# exactly what an in-flight redelivery is -- would no longer be found. A
		# self-service consent has no lead at all, which is not an error.
		lead_id = consent_doc.reference_name if consent_doc.reference_doctype == "A2C Lead" else None

		updates = {
			**profile_fields,
			"farmer_id": farmer_data.id,
			"consent_id": consent_doc_name,
			# None on a status-only redelivery, which the loop below skips, so the
			# blob captured by an earlier delivery survives.
			"farmer_data_json": raw_consent_data_str if validated.selected_data is not None else None,
		}

		# Identity for the upsert is the *account*, not the Fayda identity: one profile
		# per registered farmer, so a redelivery of the same webhook resolves to the
		# same profile while two accounts presenting the same Fayda identity get one
		# profile each.
		#
		# Neither `farmer_id` nor `phone_number` may key this. farmer_profile is what
		# scopes loan-application visibility, so resolving on a shared identity would
		# rebind an existing profile and hand one account's applications to another.
		# Two profiles for one human is a data-quality problem to reconcile later --
		# a strictly better failure than a silent cross-account transfer.
		#
		# An agent-driven consent has no farmer account at all (the owner is the
		# Development Agent), so there the lead is the identity.
		from oan_a2c.a2c_marketplace.permissions import is_farmer

		consenting_farmer = owner if owner and is_farmer(owner) else None

		existing_profile_name = None
		if consenting_farmer:
			existing_profile_name = frappe.db.get_value(
				"A2C Farmer Profile", {"user": consenting_farmer}, "name"
			)
		elif lead_id:
			existing_profile_name = frappe.db.get_value("A2C Lead", lead_id, "farmer_profile")

		if existing_profile_name:
			farmer_profile = frappe.get_doc("A2C Farmer Profile", existing_profile_name)
		else:
			farmer_profile = frappe.new_doc("A2C Farmer Profile")

		for k, v in updates.items():
			if v is not None and v != "":
				farmer_profile.set(k, v)

		# Binding a profile to a User grants that account visibility of this farmer's
		# applications, so it is only done on an exact match against a user who
		# already holds the farmer role -- never as a side effect of a loose lookup.
		#
		# A profile already bound to a *different* account is never rebound: that is
		# the cross-account transfer this upsert exists to prevent. It can only be
		# reached down the lead path, where the profile was created for an agent.
		if consenting_farmer:
			if not farmer_profile.user:
				farmer_profile.user = consenting_farmer
			elif farmer_profile.user != consenting_farmer:
				frappe.log_error(
					title="A2C: farmer profile already bound to another account",
					message=(
						f"Profile {existing_profile_name} is bound to {farmer_profile.user}; "
						f"consent {consent_doc_name} was raised by {consenting_farmer}. "
						"Binding left unchanged."
					),
				)
		elif not farmer_profile.user and phone_number:
			import re

			candidates = [c for c in (phone_number, re.sub(r"\D", "", phone_number)) if c]
			farmer_user = None
			for field in ("mobile_no", "phone"):
				for cand in candidates:
					match = frappe.db.get_value("User", {field: cand, "enabled": 1}, "name")
					if match:
						farmer_user = match
						break
				if farmer_user:
					break
			# `user` is 1:1 with the profile, so a user who already has one of their own
			# cannot claim this one -- the insert would fail the unique constraint, and
			# the intent (let a farmer claim the profile an agent built for them) does
			# not apply once they have their own.
			if (
				farmer_user
				and is_farmer(farmer_user)
				and not frappe.db.exists("A2C Farmer Profile", {"user": farmer_user})
			):
				farmer_profile.user = farmer_user

		# ignore_permissions=True is required because this background job processes webhooks
		# from OpenG2P asynchronously. The user context set (or Administrator fallback) may
		# not have direct write permissions on A2C Farmer Profile, but the system must persist
		# the verified profile details. Approved by: Lead Architect.
		if existing_profile_name:
			farmer_profile.save(ignore_permissions=True)
		else:
			farmer_profile.insert(ignore_permissions=True)

		if lead_id:
			lead_doc = frappe.get_doc("A2C Lead", lead_id)
			# db_set is used here to link the farmer profile back to the lead, bypassing
			# validation, because this is an automated background webhook update. Approved by: Lead Architect.
			lead_doc.db_set("farmer_profile", farmer_profile.name)

		frappe.db.commit()
		frappe.logger().info(f"✅ SUCCESS: Background webhook data saved for consent {consent_doc_name}")

		notify_lead_event(
			lead_id,
			subject=frappe._("Consent data received for lead"),
			message=frappe._("Farmer profile details from OpenG2P have been saved for consent {0}.").format(
				consent_doc_name
			),
		)

	except Exception as e:
		frappe.db.rollback()
		# Log to Frappe Desk visible Error Log (Option 1)
		frappe.log_error(frappe.get_traceback(), f"Background Webhook Error for Consent {consent_doc_name}")

		try:
			frappe.db.set_value("A2C Consent Request", consent_doc_name, "status", "Failed")
			frappe.db.commit()
		except Exception:
			# If we cannot even record the failure, the request is left in an
			# unknown state — surface that explicitly rather than losing it.
			frappe.log_error(
				frappe.get_traceback(),
				f"Failed to mark Consent Request {consent_doc_name} as Failed",
			)

		notify_lead_event(
			lead_id,
			subject=frappe._("Consent data processing failed"),
			message=frappe._(
				"Processing the OpenG2P consent webhook for {0} failed. See the Error Log."
			).format(consent_doc_name),
			notification_type="Alert",
		)

		raise e


def validate_and_enqueue_consent(data, enforce_permission=True, sync=False, enqueue_after_commit=False):
	"""
	Internal: validate an OpenG2P consent payload and enqueue background
	processing. Returns the resolved A2C Consent Request name.

	Callable in-process (e.g. from the WebSub hub endpoint) without going
	through HTTP auth. When called from the authenticated receiver, pass
	enforce_permission=True so the caller's write permission is checked.

	Pass sync=True to run process_consent_data inline. Avoid this from within
	an open request transaction: process_consent_data manages its own
	rollback/commit, so an inline failure rolls back the caller's transaction
	and commits a "Failed" status underneath it. The direct-response path
	instead uses enqueue_after_commit=True so processing runs in its own
	isolated transaction only after the request commits — identical to the
	real WebSub webhook path.
	"""
	try:
		validated_data = ReceiveConsentDataSchema.model_validate(data)
	except ValidationError as e:
		frappe.throw(frappe._("Invalid webhook payload format: {0}").format(str(e)), frappe.ValidationError)

	consent_info = validated_data.consent
	consent_id = consent_info.id

	# Find Consent Request
	consent_docs = frappe.get_all(
		"A2C Consent Request", filters={"openg2p_consent_id": str(consent_id)}, fields=["name"], limit=1
	)

	if not consent_docs:
		frappe.throw(
			frappe._("Consent Request not found with OpenG2P ID: {0}").format(consent_id),
			frappe.DoesNotExistError,
		)

	consent_doc_name = consent_docs[0].name

	# Pre-validate linked lead existence (Option 3)
	ref = frappe.db.get_value(
		"A2C Consent Request", consent_doc_name, ["reference_doctype", "reference_name"], as_dict=True
	)
	lead_id = ref.reference_name if ref and ref.reference_doctype == "A2C Lead" else None
	if lead_id and not frappe.db.exists("A2C Lead", lead_id):
		frappe.throw(frappe._("Linked Lead not found: {0}").format(lead_id), frappe.DoesNotExistError)

	# Enforce write permissions on the Consent Request (authenticated path only)
	if enforce_permission:
		frappe.has_permission("A2C Consent Request", "write", doc=consent_doc_name, throw=True)

	if sync:
		process_consent_data(
			data=data,
			consent_doc_name=consent_doc_name,
			consent_request_id=str(consent_id),
		)
	else:
		frappe.enqueue(
			method=process_consent_data,
			queue="default",
			enqueue_after_commit=enqueue_after_commit,
			data=data,
			consent_doc_name=consent_doc_name,
			consent_request_id=str(consent_id),
			job_name=f"process_consent_{consent_id}",
		)

	return consent_doc_name


@validate_request(ReceiveConsentDataSchema)
@handle_api_errors
def receive_consent_data(**kwargs):
	"""
	Authenticated webhook receiver for OpenG2P consent data.
	Requires `Authorization: token <api_key>:<api_secret>` and write permission
	on A2C Consent Request. Used by direct callers (Postman, Odoo server action).

	Behind the APISIX gateway the header is supplied by the gateway's proxy-rewrite,
	not by the partner: the partner presents a `key-auth` credential, the gateway
	replaces it with the Frappe service user's key/secret. That service user therefore
	needs a role carrying write DocPerm on A2C Consent Request — enforce_permission
	below is a real check, not a formality.
	"""
	frappe.logger().info(f"🔗 Webhook received. Keys: {list(kwargs.keys())}")

	consent_doc_name = validate_and_enqueue_consent(kwargs, enforce_permission=True)

	frappe.response["http_status_code"] = 202
	return success_response(
		data={"consent_request": consent_doc_name}, message="Data accepted for background processing"
	)
