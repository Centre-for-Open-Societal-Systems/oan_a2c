'''
	  - Enforces JWT session validation via whitelist allow_guest=False.
	  - Explicitly executes frappe.has_permission.
	  - Leverages frappe.get_list() to ensure Frappe's RBAC and User Permissions (multi-tenant
	    data isolation) are dynamically applied at the database query layer.
'''
import frappe
import zlib
from frappe import _
from frappe.utils import sanitize_html, strip_html
from oan_a2c.api.utils import success_response, handle_api_errors, parse_multi_value, validate_request, SafeDate, SafeEmail
from pydantic import BaseModel, Field, field_validator
from typing import Optional, Literal

class GetLeadsSchema(BaseModel):
	start: Optional[int] = Field(None, ge=0)
	page_length: Optional[int] = Field(None, ge=1, le=100)
	search_query: Optional[str] = None
	status: Optional[str] = None
	lead_source: Optional[str] = None
	loan_type: Optional[str] = None
	start_date: SafeDate = None
	end_date: SafeDate = None
	min_loan_amount: Optional[float] = None
	max_loan_amount: Optional[float] = None


class CreateLeadSchema(BaseModel):
	phone_number: str = Field(..., min_length=1)
	first_name: Optional[str] = None
	last_name: Optional[str] = None
	email: SafeEmail = None
	lead_source: Optional[Literal["Missed Call", "IVR", "SMS", "Agent Entry"]] = None
	external_id: Optional[str] = None

class AddLeadCreditInfoSchema(BaseModel):
	lead_id: str = Field(..., min_length=1)
	loan_type: str = Field(..., min_length=1)
	loan_amount: float = Field(..., gt=0)
	purpose_message: str = Field(..., min_length=1)

class LeadIDSchema(BaseModel):
	lead_id: str = Field(..., min_length=1)

class UpdateLeadStatusSchema(BaseModel):
	lead_id: str = Field(..., min_length=1)
	status: Literal["Active", "Verified", "Processed", "Granted", "Rejected", "Dormant"]
	reason: Optional[str] = None

class GetAssignableUsersSchema(BaseModel):
	search_query: Optional[str] = None
	start: Optional[int] = Field(None, ge=0)
	page_length: Optional[int] = Field(None, ge=1, le=100)

class AssignLeadSchema(BaseModel):
	lead_id: str = Field(..., min_length=1)
	assigned_to: str = Field(..., min_length=1)

class AddLeadCommentSchema(BaseModel):
	lead_id: str = Field(..., min_length=1)
	content: str = Field(..., min_length=1)

class GetLeadTimelineSchema(BaseModel):
	lead_id: str = Field(..., min_length=1)
	event_type: Optional[str] = None

class ScheduleVisitSchema(BaseModel):
	lead_id: str = Field(..., min_length=1)
	visit_date: str = Field(..., min_length=1)
	visit_time: str = Field(..., min_length=1)
	region: str = Field(..., min_length=1)
	zone: str = Field(..., min_length=1)
	woreda: str = Field(..., min_length=1)
	kebele: str = Field(..., min_length=1)
	meeting_location: Optional[str] = None
	notes: Optional[str] = None

class GetVisitSchedulesSchema(BaseModel):
	lead_id: Optional[str] = None
	start_date: SafeDate = None
	end_date: SafeDate = None
	status: Optional[str] = None
	start: Optional[int] = Field(None, ge=0)
	page_length: Optional[int] = Field(None, ge=1, le=100)

class UpdateVisitScheduleStatusSchema(BaseModel):
	schedule_id: str = Field(..., min_length=1)
	status: Literal["Scheduled", "Completed", "Cancelled", "Missed"]

@frappe.whitelist(allow_guest=False)
@validate_request(GetLeadsSchema)
@handle_api_errors
def get_leads(**kwargs):
	"""
	Retrieves a paginated list of A2C Leads with multi-faceted search and filter configurations.

	Security Specs:
	  - Parametrizes all inputs to prevent SQL Injection.
	"""
	start = kwargs.get("start") or 0
	page_length = kwargs.get("page_length") or 20
	search_query = kwargs.get("search_query")
	status = kwargs.get("status")
	lead_source = kwargs.get("lead_source")
	loan_type = kwargs.get("loan_type")
	start_date = kwargs.get("start_date")
	end_date = kwargs.get("end_date")
	min_loan_amount = kwargs.get("min_loan_amount")
	max_loan_amount = kwargs.get("max_loan_amount")

	# 1. Enforce Role-Based Access Control
	frappe.has_permission("A2C Lead", "read", throw=True)

	# 3. Construct Filters
	filters = []

	# Apply Status Filter (single or comma-separated multi-value)
	# Note: Should we check if the status string is valid and raise a ValidationError if not?
	if status:
		allowed_statuses = ("Active", "Verified", "Processed", "Granted", "Rejected", "Dormant")
		valid_statuses = parse_multi_value(status, allowed_statuses)
		if valid_statuses:
			filters.append(["status", "in", valid_statuses])

	# Apply Lead Source Filter (single or comma-separated multi-value)
	if lead_source:
		allowed_sources = ("Missed Call", "IVR", "SMS", "Agent Entry")
		valid_sources = parse_multi_value(lead_source, allowed_sources)
		if valid_sources:
			filters.append(["lead_source", "in", valid_sources])

	# Apply Creation Date Range Filter
	if start_date and end_date:
		filters.append(["creation", "between", [start_date, end_date]])
	elif start_date:
		filters.append(["creation", ">=", start_date])
	elif end_date:
		filters.append(["creation", "<=", end_date])

	# Apply Loan Amount / Loan Type Filter via the linked A2C Credit Information.
	# Amount range and loan_type (single or comma-separated multi-value) are intersected
	# in one subquery so a lead must satisfy all supplied credit criteria.
	valid_loan_types = []
	if loan_type:
		allowed_loan_types = tuple(
			o.strip()
			for o in (frappe.get_meta("A2C Credit Information").get_field("loan_type").options or "").split("\n")
			if o.strip()
		)
		valid_loan_types = parse_multi_value(loan_type, allowed_loan_types)

	if min_loan_amount is not None or max_loan_amount is not None or valid_loan_types:
		credit_filters = {}
		if min_loan_amount is not None and max_loan_amount is not None:
			credit_filters['loan_amount'] = ("between", [min_loan_amount, max_loan_amount])
		elif min_loan_amount is not None:
			credit_filters['loan_amount'] = (">=", min_loan_amount)
		elif max_loan_amount is not None:
			credit_filters['loan_amount'] = ("<=", max_loan_amount)

		if valid_loan_types:
			credit_filters['loan_type'] = ("in", valid_loan_types)

		matching_credit_leads = frappe.get_all(
			"A2C Credit Information",
			filters=credit_filters,
			pluck="lead",
			distinct=True
		)

		if matching_credit_leads:
			filters.append(["name", "in", matching_credit_leads])
		else:
			# Ensure no leads match if the credit criteria yielded no matching credit infos
			filters.append(["name", "in", ["__NONE__"]])

	# 4. Construct Search Or-Filters
	or_filters = []
	if search_query:
		# Search by Lead ID (name), Phone Number, or External ID
		search_query_param = f"%{search_query}%"
		or_filters.append(["name", "like", search_query_param])
		or_filters.append(["phone_number", "like", search_query_param])
		or_filters.append(["external_id", "like", search_query_param])

	# 5. Fetch Total Record Count (Respecting RBAC and User Permissions via get_list counted select)
	count_res = frappe.get_list(
		"A2C Lead",
		filters=filters,
		or_filters=or_filters or None,
		fields=[{"COUNT": "*"}]
	)
	total_count = count_res[0].get("COUNT(*)") if count_res else 0

	# 6. Fetch Paginated Records
	leads = frappe.get_list(
		"A2C Lead",
		fields=["name", "phone_number", "external_id", "lead_source", "status", "assigned_to", "assigned_date", "creation"],
		filters=filters,
		or_filters=or_filters or None,
		limit_start=start,
		page_length=page_length,
		order_by="creation desc"
	)

	# Fetch linked loan_type and loan_amount from Credit Information for each lead in a single query (resolving N+1 query issue)
	if leads:
		lead_names = [lead["name"] for lead in leads]
		all_credit_infos = frappe.get_all(
			"A2C Credit Information",
			filters={"lead": ["in", lead_names]},
			fields=["lead", "loan_type", "loan_amount"],
			order_by="creation desc"
		)

		latest_credit_map = {}
		for info in all_credit_infos:
			lead_name = info["lead"]
			if lead_name not in latest_credit_map:
				latest_credit_map[lead_name] = info

		for lead in leads:
			info = latest_credit_map.get(lead["name"])
			lead["loan_type"] = info.get("loan_type") if info else None
			lead["loan_amount"] = info.get("loan_amount") if info else None

		# Fold each lead's latest visit (date + status) into the list response in a single
		# query scoped to this page's leads, instead of the frontend bulk-prefetching every
		# Visit Schedule on each render. Visit Schedule remains the source of truth (read on
		# demand here); no field is stored on the Lead and no write-back hook fires on visit
		# changes, so there is nothing to keep in sync and no staleness.
		all_visits = frappe.get_all(
			"A2C Visit Schedule",
			filters={"lead": ["in", lead_names]},
			fields=["lead", "visit_date", "status"],
			order_by="visit_date desc, visit_time desc"
		)

		latest_visit_map = {}
		for visit in all_visits:
			lead_name = visit["lead"]
			if lead_name not in latest_visit_map:
				latest_visit_map[lead_name] = visit

		for lead in leads:
			visit = latest_visit_map.get(lead["name"])
			lead["visit_date"] = visit.get("visit_date") if visit else None
			lead["schedule_status"] = visit.get("status") if visit else None

	total_pages = -(-total_count // page_length)
	has_next = start + page_length < total_count

	pagination = {
		"page": (start // page_length) + 1,
		"limit": page_length,
		"total": total_count,
		"total_pages": total_pages,
		"has_next": has_next
	}

	return success_response(
		data=leads,
		message="Leads retrieved successfully",
		pagination=pagination
	)


@frappe.whitelist(allow_guest=False)
@validate_request(CreateLeadSchema)
@handle_api_errors
def create_lead(**kwargs):
	"""
	Natively creates a new A2C Lead document from the A2C application interface.
	
	Security Specs:
	  - Enforces JWT session validation via whitelist allow_guest=False.
	  - Validates role creation permissions natively.
	  - Validates and sanitizes all input strings, including strict email formatting checks.
	"""
	frappe.has_permission("A2C Lead", "create", throw=True)

	phone_number = kwargs.get("phone_number")
	first_name = kwargs.get("first_name")
	last_name = kwargs.get("last_name")
	email = kwargs.get("email")
	lead_source = kwargs.get("lead_source", "Agent Entry")
	external_id = kwargs.get("external_id")

	# Acquire a database-level transaction row/gap lock via raw SQL FOR UPDATE to prevent TOCTOU
	# race conditions during concurrent API requests.
	# Alternative unique constraints cannot be enforced on the database layer because some values
	# (such as external_id) are optional or may have blank/empty string values, which are not
	# guaranteed to be unique under standard database unique index constraints (where empty strings
	# trigger duplicate key errors in MariaDB/MySQL).
	if phone_number:
		frappe.db.sql("SELECT name FROM `tabA2C Lead` WHERE phone_number = %s FOR UPDATE", (phone_number,))
		if frappe.db.exists("A2C Lead", {"phone_number": phone_number}):
			frappe.throw(_("Lead with phone number {0} already exists").format(phone_number), frappe.DuplicateEntryError)

	if external_id:
		frappe.db.sql("SELECT name FROM `tabA2C Lead` WHERE external_id = %s FOR UPDATE", (external_id,))
		if frappe.db.exists("A2C Lead", {"external_id": external_id}):
			frappe.throw(_("Lead with external ID {0} already exists").format(external_id), frappe.DuplicateEntryError)

	lead = frappe.new_doc("A2C Lead")
	lead.phone_number = phone_number
	lead.first_name = first_name
	lead.last_name = last_name
	lead.email = email
	lead.lead_source = lead_source
	lead.external_id = external_id
	lead.status = "Active"
	lead.insert(ignore_permissions=False)

	audit_event = frappe.new_doc("A2C Lead Audit Event")
	audit_event.lead = lead.name
	audit_event.event_type = "Created"
	audit_event.event_title = "Lead Created"
	audit_event.event_description = f"Imported from {lead_source}" if lead_source else "Manually created"
	audit_event.insert()

	return success_response(
		data={
			"lead_id": lead.name,
			"lead": {
				"name": lead.name,
				"phone_number": lead.phone_number,
				"first_name": lead.first_name,
				"last_name": lead.last_name,
				"email": lead.email,
				"lead_source": lead.lead_source,
				"external_id": lead.external_id,
				"status": lead.status,
			}
		},
		message="Lead created successfully."
	)


@frappe.whitelist(allow_guest=False)
@handle_api_errors
def get_lead_summary():
	"""
	Returns aggregated lead counts: total count and status-wise counts.
	Enforces JWT session validation and native role-based permissions (RBAC).
	"""
	# 1. Enforce Role-Based Access Control
	frappe.has_permission("A2C Lead", "read", throw=True)

	allowed_statuses = ("Active", "Verified", "Processed", "Granted", "Rejected", "Dormant")
	counts_by_status = {}
	total_count = 0

	for status in allowed_statuses:
		cnt_res = frappe.get_list(
			"A2C Lead",
			filters={"status": status},
			fields=[{"COUNT": "*"}]
		)
		count = cnt_res[0].get("COUNT(*)") if cnt_res else 0
		counts_by_status[status] = count
		total_count += count
		
	return success_response(
		data={
			"total": total_count,
			"by_status": counts_by_status
		},
		message="Lead summary retrieved successfully"
	)


@frappe.whitelist(allow_guest=False)
@handle_api_errors
def get_lead_metadata():
	"""
	Returns dynamic options for dropdown fields in A2C Lead and Credit Information forms.
	Enforces JWT session validation and native role-based permissions (RBAC).
	"""
	frappe.has_permission("A2C Lead", "read", throw=True)

	# This payload is derived purely from doctype Select options, which only change on
	# migrate. Cache it for an hour instead of recomputing meta on every call (this endpoint
	# fires on every New Lead form mount). The cache key is global (not user-scoped) because
	# the options are identical for all users; RBAC is still enforced above on every request.
	cache_key = "a2c_lead_metadata"
	cached = frappe.cache().get_value(cache_key)
	if cached:
		return success_response(data=cached, message="Lead metadata retrieved successfully")

	meta = frappe.get_meta("A2C Lead")
	status_field = meta.get_field("status")
	source_field = meta.get_field("lead_source")

	statuses = status_field.options.split("\n") if status_field else []
	sources = source_field.options.split("\n") if source_field else []

	credit_meta = frappe.get_meta("A2C Credit Information")
	loan_type_field = credit_meta.get_field("loan_type")
	loan_types = loan_type_field.options.split("\n") if loan_type_field else []

	data = {
		"statuses": statuses,
		"sources": sources,
		"loan_types": loan_types
	}
	frappe.cache().set_value(cache_key, data, expires_in_sec=3600)

	return success_response(data=data, message="Lead metadata retrieved successfully")


@frappe.whitelist(allow_guest=False)
@validate_request(AddLeadCreditInfoSchema)
@handle_api_errors
def add_lead_credit_info(**kwargs):
	"""
	Creates a new A2C Credit Information record associated with a lead.
	
	Security & Validation:
	  - Enforces JWT session validation via whitelist allow_guest=False.
	  - Checks user has 'write' permission on the lead and 'create' permission on A2C Credit Information.
	  - Validates and sanitizes parameters.
	"""
	lead_id = kwargs.get("lead_id")
	loan_type = kwargs.get("loan_type")
	loan_amount = kwargs.get("loan_amount")
	purpose_message = kwargs.get("purpose_message")

	if purpose_message:
		purpose_message = strip_html(purpose_message)

	# Verify permissions first to prevent pre-auth resource enumeration
	frappe.has_permission("A2C Lead", "write", doc=lead_id, throw=True)
	frappe.has_permission("A2C Credit Information", "create", throw=True)

	if not frappe.db.exists("A2C Lead", lead_id):
		frappe.throw(_("A2C Lead {0} not found").format(lead_id), frappe.DoesNotExistError)

	# Validate loan_type Select field input
	meta = frappe.get_meta("A2C Credit Information")
	loan_type_field = meta.get_field("loan_type")
	allowed_types = loan_type_field.options.split("\n") if loan_type_field else []
	if loan_type not in allowed_types:
		frappe.throw(_("Invalid loan type: {0}").format(loan_type), frappe.ValidationError)

	credit_info = frappe.new_doc("A2C Credit Information")
	credit_info.lead = lead_id
	credit_info.loan_type = loan_type
	credit_info.loan_amount = loan_amount
	credit_info.purpose_message = purpose_message
	credit_info.insert(ignore_permissions=False)

	# Insert Audit Event
	audit_event = frappe.new_doc("A2C Lead Audit Event")
	audit_event.lead = lead_id
	audit_event.event_type = "Credit Info Added"
	audit_event.event_title = "Credit Info Added"
	audit_event.event_description = _("Credit Information added: {0} for ETB {1:,.2f}.").format(
		loan_type, float(loan_amount)
	)
	audit_event.insert()

	return success_response(
		data={"credit_info_id": credit_info.name},
		message="Credit information added successfully."
	)


@frappe.whitelist(allow_guest=False)
@validate_request(LeadIDSchema)
@handle_api_errors
def get_lead_credit_infos(**kwargs):
	"""
	Retrieves a list of A2C Credit Information records for a specific lead.
	Enforces read permissions on A2C Credit Information and standard RBAC.
	"""
	lead_id = kwargs.get("lead_id")

	frappe.has_permission("A2C Lead", "read", doc=lead_id, throw=True)
	frappe.has_permission("A2C Credit Information", "read", throw=True)

	results = frappe.get_list(
		"A2C Credit Information",
		fields=["name", "loan_type", "loan_amount", "purpose_message", "created_by", "creation"],
		filters={"lead": lead_id},
		order_by="creation desc"
	)

	return success_response(
		data=results,
		message="Lead credit information retrieved successfully"
	)


@frappe.whitelist(allow_guest=False)
@validate_request(UpdateLeadStatusSchema)
@handle_api_errors
def update_lead_status(**kwargs):
	"""
	Updates the status of an A2C Lead.
	Enforces:
	  - JWT authentication.
	  - Role permissions ('write' on A2C Lead).
	  - Terminal locking (cannot change status if current status is Processed, Rejected, Granted, or Dormant).
	  - Inserts the reason/internal notes as a timeline comment.
	"""
	lead_id = kwargs.get("lead_id")
	status = kwargs.get("status")
	reason = kwargs.get("reason")

	if reason:
		reason = sanitize_html(reason)

	# 1. Enforce Role Permissions before existence check to prevent pre-auth resource enumeration
	frappe.has_permission("A2C Lead", "write", doc=lead_id, throw=True)

	if not frappe.db.exists("A2C Lead", lead_id):
		frappe.throw(_("A2C Lead {0} not found").format(lead_id), frappe.DoesNotExistError)

	lead_doc = frappe.get_doc("A2C Lead", lead_id)

	# 2. Enforce Terminal State Locking
	terminal_statuses = ("Granted", "Rejected", "Dormant")
	if lead_doc.status in terminal_statuses:
		frappe.throw(
			_("Lead status is locked and cannot be updated because its current state is '{0}'.").format(lead_doc.status),
			frappe.ValidationError
		)

	if lead_doc.status == "Processed" and status not in ("Granted", "Rejected"):
		frappe.throw(
			_("A 'Processed' lead can only be changed to 'Granted' or 'Rejected'."),			frappe.ValidationError
		)

	# 3. Validate target status
	allowed_statuses = ("Active", "Verified", "Processed", "Granted", "Rejected", "Dormant")
	if status not in allowed_statuses:
		frappe.throw(_("Invalid status: {0}").format(status), frappe.ValidationError)

	old_status = lead_doc.status
	lead_doc.status = status
	lead_doc.save(ignore_permissions=False)

	# 4. Insert Timeline Audit Event
	description = _("Changed to {0}").format(status)
	if reason:
		description += f"\nReason: {reason}"
	description += f"\nUpdated by: {frappe.session.user}"

	audit_event = frappe.new_doc("A2C Lead Audit Event")
	audit_event.lead = lead_id
	audit_event.event_type = "Status Changed"
	audit_event.event_title = "Status Updated"
	audit_event.event_description = description
	audit_event.insert()

	return success_response(
		data={
			"lead_id": lead_id,
			"new_status": status
		},
		message="Lead status updated successfully."
	)


@frappe.whitelist(allow_guest=False)
@validate_request(GetAssignableUsersSchema)
@handle_api_errors
def get_assignable_users(**kwargs):
	"""
	Retrieves potential lead assignees: active Users having roles 'Development Agent' or 'Bank Agent'.
	Optionally filters by search_query (full_name, name, or email) and supports pagination.
	"""
	frappe.has_permission("A2C Lead", "read", throw=True)

	search_query = kwargs.get("search_query")
	start_idx = kwargs.get("start") or 0
	page_len = kwargs.get("page_length") or 20

	# Fetch Users linked to either 'Development Agent' or 'Bank Agent' role.
	# ignore_permissions=True is required here because standard users (like Bank Agent)
	# do not have permission to read/query the Has Role doctype.
	role_users = frappe.get_list(
		"Has Role",
		filters={"role": ["in", ["Development Agent", "Bank Agent"]]},
		pluck="parent",
		ignore_permissions=True
	)

	if not role_users:
		return success_response(
			data=[],
			message="Assignable users retrieved successfully",
			pagination={
				"start": start_idx,
				"page_length": page_len,
				"total_count": 0,
				"has_next": False
			}
		)

	# TODO: Implement tenant/bank isolation context checks if scoped assignment requirements are introduced in the future.
	# TODO: Create or use a dedicated Bank Agent mapping/relation table for get and assignable tasks in the future.
	# Construct DB query filters
	user_filters = {
		"name": ["in", list(set(role_users))],
		"enabled": 1
	}

	# If search_query is supplied, perform fuzzy matching
	or_filters = []
	if search_query:
		fuzzy = f"%{search_query}%"
		or_filters.append(["full_name", "like", fuzzy])
		or_filters.append(["email", "like", fuzzy])
		or_filters.append(["name", "like", fuzzy])

	# Query total count for pagination.
	# ignore_permissions=True is required because standard users lack permissions to query the system User table.
	count_res = frappe.get_list(
		"User",
		filters=user_filters,
		or_filters=or_filters or None,
		fields=[{"COUNT": "*"}],
		ignore_permissions=True
	)
	total_count = count_res[0].get("COUNT(*)") if count_res else 0

	# Query Users.
	# ignore_permissions=True is required because standard users lack permissions to query the system User table.
	users = frappe.get_list(
		"User",
		fields=["name", "email", "full_name", "username", "location"],
		filters=user_filters,
		or_filters=or_filters or None,
		order_by="full_name asc",
		limit_start=start_idx,
		page_length=page_len,
		ignore_permissions=True
	)

	# Format response properties to match UI mockup requirements (agent_id and region)
	formatted_results = []
	for u in users:
		# Use username if populated, else mock AG-2024-XXXX using standard user name/hash
		agent_id = u.username or f"AG-2024-{zlib.adler32(u.name.encode('utf-8')) % 10000:04d}"
		# Map user's location as their region, fallback to Oromia if blank
		region = u.location or "Oromia"

		formatted_results.append({
			"email": u.email or u.name,
			"full_name": u.full_name or u.name,
			"agent_id": agent_id,
			"region": region
		})

	has_next = (start_idx + page_len) < total_count
	# Note: This pagination shape ({start, page_length, total_count, has_next}) is intentional
	# and conforms to the API specification / contract established in docs/api-flow-backend.md.
	pagination = {
		"start": start_idx,
		"page_length": page_len,
		"total_count": total_count,
		"has_next": has_next
	}

	return success_response(
		data=formatted_results,
		message="Assignable users retrieved successfully",
		pagination=pagination
	)


@frappe.whitelist(allow_guest=False)
@validate_request(AssignLeadSchema)
@handle_api_errors
def assign_lead(**kwargs):
	"""
	Assigns a lead to a specified agent.
	Updates:
	  - assigned_to (User reference)
	  - assigned_date (Current system date)
	Side Effect:
	  - Appends timeline comment to track assignment log.
	"""
	lead_id = kwargs.get("lead_id")
	assigned_to = kwargs.get("assigned_to")

	# Enforce write permissions first to prevent pre-auth resource enumeration
	frappe.has_permission("A2C Lead", "write", doc=lead_id, throw=True)

	if not frappe.db.exists("A2C Lead", lead_id):
		frappe.throw(_("A2C Lead {0} not found").format(lead_id), frappe.DoesNotExistError)

	# Verify assignee is a valid enabled User
	if not frappe.db.exists("User", {"email": assigned_to, "enabled": 1}):
		# Fallback check by username/name
		if not frappe.db.exists("User", {"name": assigned_to, "enabled": 1}):
			frappe.throw(_("User '{0}' is not a valid active agent").format(assigned_to), frappe.DoesNotExistError)

	# Retrieve user's full name for timeline logging
	assignee_name = frappe.db.get_value("User", {"email": assigned_to}, "full_name") or frappe.db.get_value("User", assigned_to, "full_name") or assigned_to

	from frappe.utils import today
	now_date = today()

	lead_doc = frappe.get_doc("A2C Lead", lead_id)
	lead_doc.assigned_to = assigned_to
	lead_doc.assigned_date = now_date
	lead_doc.save(ignore_permissions=False)

	# Log Audit Event
	audit_event = frappe.new_doc("A2C Lead Audit Event")
	audit_event.lead = lead_id
	audit_event.event_type = "Assigned"
	audit_event.event_title = "Assigned to Owner"
	audit_event.event_description = _("Assigned to {0}").format(assignee_name)
	audit_event.insert()

	return success_response(
		data={
			"lead_id": lead_id,
			"assigned_to": assigned_to,
			"assigned_date": now_date
		},
		message="Lead assigned successfully."
	)


@frappe.whitelist(allow_guest=False)
@validate_request(AddLeadCommentSchema)
@handle_api_errors
def add_lead_comment(**kwargs):
	"""
	Decoupled API bridge to attach a comment or manual timeline note to a specific A2C Lead.
	Enforces JWT session validation, write permissions, and input validation.
	"""
	lead_id = kwargs.get("lead_id")
	content = kwargs.get("content")

	content = sanitize_html(content)

	# Verify user has write permissions on this specific lead document
	frappe.has_permission("A2C Lead", "write", doc=lead_id, throw=True)

	audit_event = frappe.new_doc("A2C Lead Audit Event")
	audit_event.lead = lead_id
	audit_event.event_type = "Commented"
	audit_event.event_title = "Agent Note"
	audit_event.event_description = content
	audit_event.insert(ignore_permissions=False)

	return success_response(
		data={"comment_id": audit_event.name},
		message="Comment added successfully."
	)


@frappe.whitelist(allow_guest=False)
@validate_request(GetLeadTimelineSchema)
@handle_api_errors
def get_lead_timeline(**kwargs):
	"""
	Retrieves the historical timeline of comments and system activities for a specific lead.
	Optionally filter by event_type (e.g., 'Commented' for manual notes only).
	Enforces JWT session validation and explicit document-level read permissions.
	"""
	lead_id = kwargs.get("lead_id")
	event_type = kwargs.get("event_type")

	# Verify user has read permissions on this specific lead document
	frappe.has_permission("A2C Lead", "read", doc=lead_id, throw=True)

	filters = {"lead": lead_id}
	if event_type:
		filters["event_type"] = event_type

	timeline = frappe.get_list(
		"A2C Lead Audit Event",
		fields=["name", "event_type", "event_title", "event_description", "creation", "owner"],
		filters=filters,
		order_by="creation desc"
	)

	return success_response(
		data={
			"lead_id": lead_id,
			"timeline": timeline
		},
		message="Lead timeline retrieved successfully"
	)


@frappe.whitelist(allow_guest=False)
@validate_request(LeadIDSchema)
@handle_api_errors
def get_lead_call_logs(**kwargs):
	"""
	Retrieves and parses the call history/event logs for a specific A2C Lead.
	Enforces JWT session validation and document-level read permissions.
	"""
	lead_id = kwargs.get("lead_id")

	# Verify user has read permissions on this specific lead document
	frappe.has_permission("A2C Lead", "read", doc=lead_id, throw=True)

	call_notes = frappe.db.get_value("A2C Lead", lead_id, "call_notes") or ""

	parsed_logs = []
	raw_lines = [line.strip() for line in call_notes.split("\n") if line.strip()]

	for line in raw_lines:
		# A line looks like: "Source: Missed Call | Ref ID: TELCO-778899 | Timestamp: 2026-05-27T12:00:00Z"
		parts = [p.strip() for p in line.split(" | ")]
		log_entry = {}
		for part in parts:
			if ":" in part:
				key, val = part.split(":", 1)
				log_entry[key.strip().lower().replace(" ", "_")] = val.strip()
		if log_entry:
			parsed_logs.append(log_entry)

	return success_response(
		data={
			"lead_id": lead_id,
			"call_logs": parsed_logs
		},
		message="Lead call logs retrieved successfully"
	)


@frappe.whitelist(allow_guest=False)
@validate_request(ScheduleVisitSchema)
@handle_api_errors
def schedule_visit(**kwargs):
	"""
	Schedules a new visit for an A2C Lead.
	- Enforces JWT session validation via allow_guest=False.
	- Enforces user write permissions on the Lead and create permissions on the Visit Schedule.
	- Inserts a system Comment on the lead's timeline.
	"""
	lead_id = kwargs.get("lead_id")
	visit_date = kwargs.get("visit_date")
	visit_time = kwargs.get("visit_time")
	region = kwargs.get("region")
	zone = kwargs.get("zone")
	woreda = kwargs.get("woreda")
	kebele = kwargs.get("kebele")
	meeting_location = kwargs.get("meeting_location")
	notes = kwargs.get("notes")

	if notes:
		notes = sanitize_html(notes)

	# Check permissions first to prevent pre-auth resource enumeration
	frappe.has_permission("A2C Lead", "write", doc=lead_id, throw=True)
	frappe.has_permission("A2C Visit Schedule", "create", throw=True)

	if not frappe.db.exists("A2C Lead", lead_id):
		frappe.throw(_("A2C Lead {0} not found").format(lead_id), frappe.DoesNotExistError)

	schedule = frappe.new_doc("A2C Visit Schedule")
	schedule.lead = lead_id
	schedule.visit_date = visit_date
	schedule.visit_time = visit_time
	schedule.region = region
	schedule.zone = zone
	schedule.woreda = woreda
	schedule.kebele = kebele
	schedule.meeting_location = meeting_location
	schedule.notes = notes
	schedule.scheduled_by = frappe.session.user
	schedule.status = "Scheduled"
	schedule.insert(ignore_permissions=False)

	# Insert Audit Event
	audit_event = frappe.new_doc("A2C Lead Audit Event")
	audit_event.lead = lead_id
	audit_event.event_type = "Visit Scheduled"
	audit_event.event_title = "Visit Scheduled"
	audit_event.event_description = _("Visit scheduled for {0} at {1}.").format(
		visit_date, visit_time
	)
	audit_event.insert()

	return success_response(
		data={"schedule_id": schedule.name},
		message="Visit scheduled successfully."
	)


@frappe.whitelist(allow_guest=False)
@validate_request(GetVisitSchedulesSchema)
@handle_api_errors
def get_visit_schedules(**kwargs):
	"""
	Retrieves a paginated list of visit schedules.
	- Enforces JWT session validation.
	- Enforces read permissions on A2C Visit Schedule.
	- Utilizes frappe.get_list for RBAC & user permission isolation.
	"""
	lead_id = kwargs.get("lead_id")
	start_date = kwargs.get("start_date")
	end_date = kwargs.get("end_date")
	status = kwargs.get("status")
	start = kwargs.get("start") if kwargs.get("start") is not None else 0
	page_length = kwargs.get("page_length") if kwargs.get("page_length") is not None else 20

	frappe.has_permission("A2C Visit Schedule", "read", throw=True)
	if lead_id:
		frappe.has_permission("A2C Lead", "read", doc=lead_id, throw=True)

	filters = []
	if lead_id:
		filters.append(["lead", "=", lead_id])
	if status:
		filters.append(["status", "=", status])

	if start_date and end_date:
		filters.append(["visit_date", "between", [start_date, end_date]])
	elif start_date:
		filters.append(["visit_date", ">=", start_date])
	elif end_date:
		filters.append(["visit_date", "<=", end_date])

	count_res = frappe.get_list(
		"A2C Visit Schedule",
		filters=filters,
		fields=[{"COUNT": "*"}]
	)
	total_count = count_res[0].get("COUNT(*)") if count_res else 0

	schedules = frappe.get_list(
		"A2C Visit Schedule",
		fields=[
			"name", "lead", "visit_date", "visit_time",
			"meeting_location", "region", "zone",
			"woreda", "kebele", "status", "scheduled_by", "creation"
		],
		filters=filters,
		limit_start=start,
		page_length=page_length,
		order_by="visit_date desc, visit_time desc"
	)

	total_pages = -(-total_count // page_length)
	has_next = start + page_length < total_count

	pagination = {
		"page": (start // page_length) + 1,
		"limit": page_length,
		"total": total_count,
		"total_pages": total_pages,
		"has_next": has_next
	}

	return success_response(
		data=schedules,
		message="Visit schedules retrieved successfully",
		pagination=pagination
	)


@frappe.whitelist(allow_guest=False)
@validate_request(UpdateVisitScheduleStatusSchema)
@handle_api_errors
def update_visit_schedule_status(**kwargs):
	"""
	Updates the status of an A2C Visit Schedule (Scheduled, Completed, Cancelled, Missed).
	"""
	schedule_id = kwargs.get("schedule_id")
	status = kwargs.get("status")

	lead = None
	if frappe.db.exists("A2C Visit Schedule", schedule_id):
		lead = frappe.db.get_value("A2C Visit Schedule", schedule_id, "lead")

	if lead:
		frappe.has_permission("A2C Lead", "write", doc=lead, throw=True)
	else:
		frappe.has_permission("A2C Lead", "write", throw=True)
		frappe.throw(_("A2C Visit Schedule {0} not found").format(schedule_id), frappe.DoesNotExistError)

	schedule = frappe.get_doc("A2C Visit Schedule", schedule_id)

	schedule.status = status
	schedule.save(ignore_permissions=False)

	return success_response(
		data={
			"schedule_id": schedule_id,
			"new_status": status
		},
		message="Visit schedule status updated successfully."
	)




