"""Consent bootstrap for the self-service farmer.

The consent flow itself is NOT reimplemented here. `oan_a2c.api.v1.consent`
(request_otp -> verify_otp -> submit_consent) is the one implementation, and a
farmer applying for a loan drives exactly the same endpoints a Development Agent
does. All that is missing on the farmer side is the thing every one of those
endpoints takes as its anchor: a lead_id.

A Development Agent always has one, because it works from a lead it created. A
self-registering farmer does not, and cannot derive one from their A2C Farmer
Profile either — that profile does not exist until the consent webhook creates
it (see permissions.get_user_farmer_profile). This module closes that gap by
minting the lead up front, so consent can run before a profile exists.
"""

import time

import frappe
from frappe import _

from oan_a2c.a2c_marketplace.roles import FARMER_ROLE
from oan_a2c.api.utils import handle_api_errors, require_role, success_response

# The A2C Lead `lead_source` value denoting a farmer who applied for themselves,
# as opposed to "Agent Entry" and the call-centre sources.
SELF_SERVICE_SOURCE = "Self Service"

# How long the create-lead lock is held, and how long a request that lost the race
# waits for the winner to commit. The guarded section is a single insert, so both
# are generous; the wait only ever happens on a farmer's very first call.
_LEAD_LOCK_TTL_SECONDS = 15
_LEAD_WAIT_ATTEMPTS = 20
_LEAD_WAIT_INTERVAL_SECONDS = 0.25


def _find_self_service_lead(user):
	"""The farmer's existing self-service lead, or None.

	get_list applies farmer_own_lead_query, so this can only ever match a lead
	belonging to the caller — the owner filter is belt-and-braces.
	"""
	rows = frappe.get_list(
		"A2C Lead",
		filters={"lead_source": SELF_SERVICE_SOURCE, "owner": user},
		fields=["name"],
		order_by="creation desc",
		limit_page_length=1,
	)
	return frappe.get_doc("A2C Lead", rows[0]["name"]) if rows else None


def get_or_create_self_service_lead(user=None):
	"""Return the A2C Lead this farmer's consent is anchored on, creating it if needed.

	Idempotent by design. Consent is a property of the farmer, not of a single
	product: it carries a validity window and it is what produces the farmer's
	profile. Minting a lead per visit to the apply page would mean re-consenting
	for every product browsed, and would litter the pipeline with dead leads.
	So the farmer's existing self-service lead is reused whenever there is one.

	Concurrency-safe. A bare check-then-insert is not: two requests arriving
	together (React's double-invoked effect in development, a double click, two
	tabs) both find nothing and both insert. That does not merely duplicate the
	lead — A2C Lead autonames `LD-####`, so both inserts take the same `tabSeries`
	row FOR UPDATE and one dies with a QueryDeadlockError. Creation is therefore
	serialised per user on a Redis lock, with the check repeated inside it.

	Returns the A2C Lead document.
	"""
	if not user:
		user = frappe.session.user

	existing = _find_self_service_lead(user)
	if existing:
		return existing

	cache = frappe.cache()
	lock_key = f"oan_a2c:self_service_lead_lock:{user}"
	# SET NX EX in one round trip: a get-then-set would reintroduce the very race
	# this is here to close.
	acquired = cache.set(lock_key, b"1", nx=True, ex=_LEAD_LOCK_TTL_SECONDS)

	if not acquired:
		# Another request is mid-create. Its row is invisible here until it
		# commits, so poll rather than trying to create a second one.
		for _attempt in range(_LEAD_WAIT_ATTEMPTS):
			time.sleep(_LEAD_WAIT_INTERVAL_SECONDS)
			existing = _find_self_service_lead(user)
			if existing:
				return existing
		frappe.throw(
			_("Could not start your application just now. Please try again."),
			frappe.ValidationError,
		)

	try:
		# Re-check under the lock: the winner may have committed between the
		# first check and acquiring this.
		existing = _find_self_service_lead(user)
		if existing:
			return existing

		return _create_self_service_lead(user)
	finally:
		cache.delete(lock_key)


def _create_self_service_lead(user):
	"""Insert the farmer's self-service lead. Callers must hold the create lock."""
	user_row = frappe.db.get_value(
		"User", user, ["first_name", "last_name", "mobile_no", "phone", "email"], as_dict=True
	)
	if not user_row:
		frappe.throw(_("User account not found."), frappe.DoesNotExistError)

	phone_number = user_row.get("mobile_no") or user_row.get("phone")
	if not phone_number:
		# phone_number is reqd on A2C Lead, and it is also how the consent webhook
		# matches the incoming registry profile back to this User account. Failing
		# here with the reason beats a bare mandatory-field error from the insert.
		frappe.throw(
			_("Your account has no phone number. Add one to your profile before applying."),
			frappe.ValidationError,
		)

	# An A2C Farmer Profile may already exist if a Development Agent onboarded this
	# farmer earlier; link it so the lead is recognised as theirs by profile as well
	# as by owner. Absent one, the consent webhook fills it in.
	profile_name = frappe.db.get_value("A2C Farmer Profile", {"user": user}, "name")

	lead = frappe.get_doc(
		{
			"doctype": "A2C Lead",
			"lead_source": SELF_SERVICE_SOURCE,
			"status": "Active",
			"first_name": user_row.get("first_name"),
			"last_name": user_row.get("last_name"),
			"phone_number": phone_number,
			"email": user_row.get("email") or user,
			"farmer_profile": profile_name,
		}
	)
	lead.insert(ignore_permissions=False)
	return lead


@frappe.whitelist(allow_guest=False, methods=["POST"])
@handle_api_errors
@require_role([FARMER_ROLE])
def start_consent(**kwargs):
	"""Hand the apply-for-a-loan page the lead its consent step runs against.

	The response also reports where that lead already stands, so re-entering the
	flow resumes rather than restarting: a farmer whose consent was approved on an
	earlier application does not get asked for an OTP again.
	"""
	lead = get_or_create_self_service_lead()

	consent = (
		frappe.db.get_value(
			"A2C Consent Request",
			{"lead": lead.name},
			["name", "status", "otp_verified_at"],
			as_dict=True,
			order_by="creation desc",
		)
		or {}
	)

	return success_response(
		data={
			"lead_id": lead.name,
			"consent_request": consent.get("name"),
			"consent_status": consent.get("status"),
			"otp_verified": bool(consent.get("otp_verified_at")),
			# The webhook writes farmer_profile onto the lead once the registry data
			# lands, which is the real "consent finished" signal — status alone flips
			# to Approved a moment before the profile exists.
			"consent_completed": bool(lead.farmer_profile) and consent.get("status") == "Approved",
		},
		message="Consent context retrieved successfully",
	)
