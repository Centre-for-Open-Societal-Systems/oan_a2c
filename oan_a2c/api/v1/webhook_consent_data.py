import json
from typing import Any, Optional
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


class SelectedDataSchema(BaseModel):
	pass


class FarmerInfoSchema(BaseModel):
	# `id` is the OpenG2P farmer id and is the identity this webhook upserts an
	# A2C Farmer Profile on, so it is required rather than optional. OpenG2P always
	# sends it; declaring that here means a malformed payload is rejected at
	# validation with a clear error, instead of falling through to a weaker key and
	# silently creating a profile nothing can later match.
	id: int = Field(..., ge=1)
	farmer_id: Any | None = None
	name: str | None = None


class ConsentInfoSchema(BaseModel):
	id: int | None = None
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
	selected_data: dict[str, Any] | None = None


def normalize_field_key(key):
	"""Reduce an OpenG2P field label to a bare lowercase alphanumeric token so
	that casing, spacing and punctuation drift do not break lookups, e.g.
	"Number of Females ( Family )" and "Number of Females (Family)" both map to
	"numberoffemalesfamily"."""
	return "".join(ch for ch in str(key).lower() if ch.isalnum())


def build_field_getter(farmer_info_dict):
	"""Return a spelling-tolerant getter over an OpenG2P farmer info dict.

	The returned `get(label, default=None)` normalizes the requested label the
	same way as the stored keys, so field lookups keep working even when
	OpenG2P changes a label's casing, spacing or punctuation.
	"""
	normalized = {normalize_field_key(k): v for k, v in (farmer_info_dict or {}).items()}

	def get(label, default=None):
		return normalized.get(normalize_field_key(label), default)

	return get


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

		if updates_dict:
			frappe.db.set_value("A2C Consent Request", consent_doc_name, updates_dict)

		# Parse Farmer Data
		# Both `farmer` and its `id` are required by the schema, so this is always a
		# populated block by the time validation has passed.
		farmer_data = validated.farmer

		raw_selected_data = validated.selected_data or {}
		farmer_info_dict = {}
		# Find the first dictionary inside selected_data that contains farmer info
		if isinstance(raw_selected_data, dict):
			for _key, val in raw_selected_data.items():
				if isinstance(val, dict):
					farmer_info_dict = val
					break

		# Spelling-tolerant accessor over the farmer info dict.
		g = build_field_getter(farmer_info_dict)

		full_name = g("Full Name", "")
		if full_name:
			name_parts = full_name.split(" ")
		else:
			name_parts = (farmer_data.name or "").split(" ")

		first_name = name_parts[0] if len(name_parts) > 0 else ""
		last_name = " ".join(name_parts[1:]) if len(name_parts) > 1 else ""

		# Mobile could be list or string
		mobile_data = g("Mobile Number", g("Phone Number", []))
		if isinstance(mobile_data, list) and mobile_data:
			phone_number = str(mobile_data[0])
		elif isinstance(mobile_data, str):
			phone_number = mobile_data
		else:
			phone_number = ""

		email = g("Email", "")

		# Fetch Consent Request to check links
		consent_doc = frappe.get_doc("A2C Consent Request", consent_doc_name)
		# Resolved from the consent request, never from A2C Lead.consent_id: that field
		# only holds the lead's *latest* attempt, so a superseded request -- which is
		# exactly what an in-flight redelivery is -- would no longer be found. A
		# self-service consent has no lead at all, which is not an error.
		lead_id = consent_doc.reference_name if consent_doc.reference_doctype == "A2C Lead" else None

		# Parse Source of income
		source_of_income_list = g("Source of Income", [])
		source_of_income = (
			", ".join([s.get("name") for s in source_of_income_list if isinstance(s, dict)])
			if isinstance(source_of_income_list, list)
			else source_of_income_list
		)

		# Parse farmland size
		farmland_size_data = g("Farmland Size (Hectares)", [])
		if isinstance(farmland_size_data, list):
			farmland_size_hectares = ", ".join([str(x) for x in farmland_size_data])
		else:
			farmland_size_hectares = farmland_size_data

		# Parse Certification ID
		land_ids = g("Land ID", [])
		if isinstance(land_ids, list):
			certification_id = ", ".join([str(x) for x in land_ids])
		else:
			certification_id = land_ids

		cert_photos = g("Certificate Provided", [])
		certification_photo_url = (
			cert_photos[0]
			if isinstance(cert_photos, list) and len(cert_photos) > 0
			else (cert_photos if isinstance(cert_photos, str) else None)
		)

		# OpenG2P provides the certificate photo as an external URL. Download it
		# and store a local Frappe File attachment on the lead; on failure this
		# falls back to keeping the original URL.
		if certification_photo_url and lead_id:
			certification_photo_url = download_cert_photo_to_file(certification_photo_url, lead_id)

		fayda_id_list = g("Fayda ID", [])
		national_id_list = g("National ID", [])

		id_type = ""
		id_number = ""

		if fayda_id_list:
			id_type = "uid"
			id_number = (
				fayda_id_list[0] if isinstance(fayda_id_list, list) and fayda_id_list else str(fayda_id_list)
			)
		elif national_id_list:
			id_type = "national_id"
			id_number = (
				national_id_list[0]
				if isinstance(national_id_list, list) and national_id_list
				else str(national_id_list)
			)

		education_level = g("Education Level", "")

		region_data = g("Region")
		region = region_data.get("name") if isinstance(region_data, dict) else (region_data or "")

		woreda_data = g("Woreda")
		woreda = woreda_data.get("name") if isinstance(woreda_data, dict) else (woreda_data or "")

		kebele_data = g("Kebele")
		kebele = kebele_data.get("name") if isinstance(kebele_data, dict) else (kebele_data or "")

		updates = {
			"first_name": first_name,
			"last_name": last_name,
			"region": region,
			"woreda": woreda,
			"kebele": kebele,
			"language": g("Language"),
			"id_type": id_type,
			"id_number": id_number,
			"farmer_id": farmer_data.id,
			"consent_id": consent_doc_name,
			"phone_number": phone_number,
			"email": email,
			"lead_id": lead_id,
			"date_of_birth": g("Date of Birth"),
			"gender": (g("Gender") or "").capitalize(),
			"marital_status": (g("Marital Status") or "").capitalize(),
			"size_of_family": frappe.utils.cint(g("Size of Family")),
			"number_of_children": frappe.utils.cint(g("Number of Children")),
			"no_of_females_family": frappe.utils.cint(g("Number of Females (Family)")),
			"source_of_income": source_of_income,
			"education_level": education_level,
			"family_member_owns_land_independently": frappe.utils.cint(g("Other Family Member Own Land")),
			"total_farmland_size_as_landowner": frappe.utils.flt(g("Total Owned Land")),
			"total_farmland_size_as_crop_sharing": frappe.utils.flt(g("Total Crop Sharing Land")),
			"total_farmland_size_as_rented": frappe.utils.flt(g("Total Rented Land")),
			"farmland_size_hectares": farmland_size_hectares,
			"land_ownership_status": g("Land Ownership Status"),
			"certification_id": certification_id,
			"certification_photo_url": certification_photo_url,
		}

		# Identity for the upsert. `farmer_id` (the OpenG2P farmer id) is the key; the
		# schema guarantees it is present, so a redelivery of the same webhook always
		# resolves to the same profile instead of inserting another one.
		#
		# Phone number is only a secondary match, for profiles created before
		# farmer_id was populated. It must never be the primary key: it is unique on
		# this doctype, so two farmers who share or inherit a number would collapse
		# onto one profile -- and since farmer_profile is what scopes loan-application
		# visibility, that hands one farmer's applications to the other.
		existing_profile_name = frappe.db.get_value(
			"A2C Farmer Profile", {"farmer_id": farmer_data.id}, "name"
		)
		if not existing_profile_name and phone_number:
			existing_profile_name = frappe.db.get_value(
				"A2C Farmer Profile", {"phone_number": phone_number}, "name"
			)

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
		if phone_number and not farmer_profile.user:
			# Find matching user by phone and role
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

			if farmer_user and frappe.db.exists("Has Role", {"parent": farmer_user, "role": "A2C Farmer"}):
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


@frappe.whitelist(allow_guest=False)
@validate_request(ReceiveConsentDataSchema)
@handle_api_errors
def receive_consent_data(**kwargs):
	"""
	Authenticated webhook receiver for OpenG2P consent data.
	Requires `Authorization: token <api_key>:<api_secret>` and write permission
	on A2C Consent Request. Used by direct callers (Postman, Odoo server action).
	"""
	frappe.logger().info(f"🔗 Webhook received. Keys: {list(kwargs.keys())}")

	consent_doc_name = validate_and_enqueue_consent(kwargs, enforce_permission=True)

	frappe.response["http_status_code"] = 202
	return success_response(
		data={"consent_request": consent_doc_name}, message="Data accepted for background processing"
	)
