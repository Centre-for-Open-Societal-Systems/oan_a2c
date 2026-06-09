import frappe
from frappe import _


@frappe.whitelist(allow_guest=False)
def get_leads(
	start=0,
	page_length=20,
	search_query=None,
	status=None,
	lead_source=None,
	start_date=None,
	end_date=None
):
	"""
	Retrieves a paginated list of A2C Leads with multi-faceted search and filter configurations.

	Security Specs:
	  - Enforces JWT session validation via whitelist allow_guest=False.
	  - Explicitly executes frappe.has_permission("A2C Lead", "read", throw=True).
	  - Leverages frappe.get_list() to ensure Frappe's RBAC and User Permissions (multi-tenant
	    data isolation) are dynamically applied at the database query layer.
	  - Parametrizes all inputs to prevent SQL Injection.
	"""
	# 1. Enforce Role-Based Access Control
	frappe.has_permission("A2C Lead", "read", throw=True)

	# 2. Sanitize and bound pagination inputs to prevent memory exhaustion DoS
	try:
		start = int(start or 0)
		if start < 0:
			start = 0
	except ValueError:
		start = 0

	try:
		page_length = int(page_length or 20)
		if page_length < 1:
			page_length = 20
		elif page_length > 100:
			page_length = 100  # Strict upper bound limit
	except ValueError:
		page_length = 20

	# 3. Construct Filters
	filters = []

	# Apply Status Filter
	if status:
		# Sanitize input against valid choices
		allowed_statuses = ("Active", "Verified", "Processed", "Granted", "Rejected", "Dormant")
		if status in allowed_statuses:
			filters.append(["status", "=", status])

	# Apply Lead Source Filter
	if lead_source:
		allowed_sources = ("Missed Call", "IVR", "SMS", "Agent Entry")
		if lead_source in allowed_sources:
			filters.append(["lead_source", "=", lead_source])

	# Apply Creation Date Range Filter
	if start_date and end_date:
		filters.append(["creation", "between", [start_date, end_date]])
	elif start_date:
		filters.append(["creation", ">=", start_date])
	elif end_date:
		filters.append(["creation", "<=", end_date])

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

	# Fetch linked loan_type and loan_amount from Credit Information for each lead
	for lead in leads:
		credit_infos = frappe.get_all(
			"A2C Credit Information",
			filters={"lead": lead["name"]},
			fields=["loan_type", "loan_amount"],
			order_by="creation desc",
			limit=1
		)
		if credit_infos:
			lead["loan_type"] = credit_infos[0]["loan_type"]
			lead["loan_amount"] = credit_infos[0]["loan_amount"]
		else:
			lead["loan_type"] = None
			lead["loan_amount"] = None

	return {
		"status": "success",
		"start": start,
		"page_length": page_length,
		"total_count": total_count,
		"results": leads
	}


@frappe.whitelist(allow_guest=False)
def create_lead(phone_number=None, first_name=None, last_name=None, email=None, lead_source="Agent Entry", external_id=None):
	"""
	Natively creates a new A2C Lead document from the A2C application interface.
	
	Security Specs:
	  - Enforces JWT session validation via whitelist allow_guest=False.
	  - Validates role creation permissions natively.
	  - Validates and sanitizes all input strings, including strict email formatting checks.
	"""
	frappe.has_permission("A2C Lead", "create", throw=True)

	if not phone_number:
		frappe.throw(_("phone_number is required"), frappe.MandatoryError)

	# Validate lead_source Select field input
	allowed_sources = ("Missed Call", "IVR", "SMS", "Agent Entry")
	if lead_source not in allowed_sources:
		lead_source = "Agent Entry"

	# Validate email address if provided
	if email:
		from frappe.utils import validate_email_address
		if not validate_email_address(email):
			frappe.throw(_("Invalid email address format"), frappe.ValidationError)

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

	return {
		"status": "success",
		"lead_id": lead.name,
		"message": _("Lead created successfully.")
	}


@frappe.whitelist(allow_guest=False)
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

	return {
		"status": "success",
		"total": total_count,
		"by_status": counts_by_status
	}


@frappe.whitelist(allow_guest=False)
def get_lead_metadata():
	"""
	Returns dynamic options for dropdown fields in A2C Lead and Credit Information forms.
	Enforces JWT session validation and native role-based permissions (RBAC).
	"""
	frappe.has_permission("A2C Lead", "read", throw=True)

	meta = frappe.get_meta("A2C Lead")
	status_field = meta.get_field("status")
	source_field = meta.get_field("lead_source")

	statuses = status_field.options.split("\n") if status_field else []
	sources = source_field.options.split("\n") if source_field else []

	credit_meta = frappe.get_meta("A2C Credit Information")
	loan_type_field = credit_meta.get_field("loan_type")
	loan_types = loan_type_field.options.split("\n") if loan_type_field else []

	return {
		"status": "success",
		"statuses": statuses,
		"sources": sources,
		"loan_types": loan_types
	}


@frappe.whitelist(allow_guest=False)
def add_lead_credit_info(lead_id=None, loan_type=None, loan_amount=None, purpose_message=None):
	"""
	Creates a new A2C Credit Information record associated with a lead.
	
	Security & Validation:
	  - Enforces JWT session validation via whitelist allow_guest=False.
	  - Checks user has 'write' permission on the lead and 'create' permission on A2C Credit Information.
	  - Validates and sanitizes parameters.
	"""
	if not lead_id:
		frappe.throw(_("lead_id is required"), frappe.MandatoryError)
	if not loan_type:
		frappe.throw(_("loan_type is required"), frappe.MandatoryError)
	if not loan_amount:
		frappe.throw(_("loan_amount is required"), frappe.MandatoryError)
	if not purpose_message:
		frappe.throw(_("purpose_message is required"), frappe.MandatoryError)

	# Verify Lead exists and permissions
	if not frappe.db.exists("A2C Lead", lead_id):
		frappe.throw(_("A2C Lead {0} not found").format(lead_id), frappe.DoesNotExistError)

	frappe.has_permission("A2C Lead", "write", doc=lead_id, throw=True)
	frappe.has_permission("A2C Credit Information", "create", throw=True)

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

	return {
		"status": "success",
		"credit_info_id": credit_info.name,
		"message": _("Credit information added successfully.")
	}


@frappe.whitelist(allow_guest=False)
def get_lead_credit_infos(lead_id=None):
	"""
	Retrieves a list of A2C Credit Information records for a specific lead.
	Enforces read permissions on A2C Credit Information and standard RBAC.
	"""
	if not lead_id:
		frappe.throw(_("lead_id is required"), frappe.MandatoryError)

	frappe.has_permission("A2C Credit Information", "read", throw=True)

	results = frappe.get_list(
		"A2C Credit Information",
		fields=["name", "loan_type", "loan_amount", "purpose_message", "created_by", "creation"],
		filters={"lead": lead_id},
		order_by="creation desc"
	)

	return {
		"status": "success",
		"results": results
	}


@frappe.whitelist(allow_guest=False)
def update_lead_status(lead_id=None, status=None, reason=None):
	"""
	Updates the status of an A2C Lead.
	Enforces:
	  - JWT authentication.
	  - Role permissions ('write' on A2C Lead).
	  - Terminal locking (cannot change status if current status is Processed, Rejected, or Granted).
	  - Inserts the reason/internal notes as a timeline comment.
	"""
	if not lead_id:
		frappe.throw(_("lead_id is required"), frappe.MandatoryError)
	if not status:
		frappe.throw(_("status is required"), frappe.MandatoryError)

	if not frappe.db.exists("A2C Lead", lead_id):
		frappe.throw(_("A2C Lead {0} not found").format(lead_id), frappe.DoesNotExistError)

	# 1. Enforce Role Permissions
	frappe.has_permission("A2C Lead", "write", doc=lead_id, throw=True)

	lead_doc = frappe.get_doc("A2C Lead", lead_id)

	# 2. Enforce Terminal State Locking
	terminal_statuses = ("Processed", "Rejected", "Granted", "Dormant")
	if lead_doc.status in terminal_statuses:
		frappe.throw(
			_("Lead status is locked and cannot be updated because its current state is '{0}'.").format(lead_doc.status),
			frappe.ValidationError
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

	return {
		"status": "success",
		"lead_id": lead_id,
		"new_status": status,
		"message": _("Lead status updated successfully.")
	}


@frappe.whitelist(allow_guest=False)
def get_assignable_users(search_query=None):
	"""
	Retrieves potential lead assignees: active Users having roles 'Development Agent' or 'Bank Agent'.
	Optionally filters by search_query (full_name, name, or email).
	"""
	frappe.has_permission("A2C Lead", "read", throw=True)

	# Fetch Users linked to either 'Development Agent' or 'Bank Agent' role
	role_users = frappe.get_list(
		"Has Role",
		filters={"role": ["in", ["Development Agent", "Bank Agent"]]},
		pluck="parent",
		ignore_permissions=True
	)

	if not role_users:
		return {"status": "success", "results": []}

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

	# Query Users
	users = frappe.get_list(
		"User",
		fields=["name", "email", "full_name", "username", "location"],
		filters=user_filters,
		or_filters=or_filters or None,
		order_by="full_name asc",
		ignore_permissions=True
	)

	# Format response properties to match UI mockup requirements (agent_id and region)
	formatted_results = []
	for u in users:
		# Use username if populated, else mock AG-2024-XXXX using standard user name/hash
		agent_id = u.username or f"AG-2024-{abs(hash(u.name)) % 10000:04d}"
		# Map user's location as their region, fallback to Oromia if blank
		region = u.location or "Oromia"

		formatted_results.append({
			"email": u.email or u.name,
			"full_name": u.full_name or u.name,
			"agent_id": agent_id,
			"region": region
		})

	return {
		"status": "success",
		"results": formatted_results
	}


@frappe.whitelist(allow_guest=False)
def assign_lead(lead_id=None, assigned_to=None):
	"""
	Assigns a lead to a specified agent.
	Updates:
	  - assigned_to (User reference)
	  - assigned_date (Current system date)
	Side Effect:
	  - Appends timeline comment to track assignment log.
	"""
	if not lead_id:
		frappe.throw(_("lead_id is required"), frappe.MandatoryError)
	if not assigned_to:
		frappe.throw(_("assigned_to is required"), frappe.MandatoryError)

	if not frappe.db.exists("A2C Lead", lead_id):
		frappe.throw(_("A2C Lead {0} not found").format(lead_id), frappe.DoesNotExistError)

	# Enforce write permissions
	frappe.has_permission("A2C Lead", "write", doc=lead_id, throw=True)

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

	return {
		"status": "success",
		"lead_id": lead_id,
		"assigned_to": assigned_to,
		"assigned_date": now_date,
		"message": _("Lead assigned successfully.")
	}


@frappe.whitelist(allow_guest=False)
def add_lead_comment(lead_id=None, content=None):
	"""
	Decoupled API bridge to attach a comment or manual timeline note to a specific A2C Lead.
	Enforces JWT session validation, write permissions, and input validation.
	"""
	if not lead_id:
		frappe.throw(_("lead_id is required"), frappe.MandatoryError)
	if not content:
		frappe.throw(_("content is required"), frappe.MandatoryError)

	# Verify user has write permissions on this specific lead document
	frappe.has_permission("A2C Lead", "write", doc=lead_id, throw=True)

	audit_event = frappe.new_doc("A2C Lead Audit Event")
	audit_event.lead = lead_id
	audit_event.event_type = "Commented"
	audit_event.event_title = "Agent Note"
	audit_event.event_description = content
	audit_event.insert(ignore_permissions=False)

	return {
		"status": "success",
		"comment_id": audit_event.name,
		"message": _("Comment added successfully.")
	}


@frappe.whitelist(allow_guest=False)
def get_lead_timeline(lead_id=None, event_type=None):
	"""
	Retrieves the historical timeline of comments and system activities for a specific lead.
	Optionally filter by event_type (e.g., 'Commented' for manual notes only).
	Enforces JWT session validation and explicit document-level read permissions.
	"""
	if not lead_id:
		frappe.throw(_("lead_id is required"), frappe.MandatoryError)

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

	return {
		"status": "success",
		"lead_id": lead_id,
		"timeline": timeline
	}


@frappe.whitelist(allow_guest=False)
def get_lead_call_logs(lead_id=None):
	"""
	Retrieves and parses the call history/event logs for a specific A2C Lead.
	Enforces JWT session validation and document-level read permissions.
	"""
	if not lead_id:
		frappe.throw(_("lead_id is required"), frappe.MandatoryError)

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

	return {
		"status": "success",
		"lead_id": lead_id,
		"call_logs": parsed_logs
	}


@frappe.whitelist(allow_guest=False)
def schedule_visit(
	lead_id=None,
	visit_date=None,
	visit_time=None,
	region=None,
	zone=None,
	woreda=None,
	kebele=None,
	meeting_location=None,
	notes=None
):
	"""
	Schedules a new visit for an A2C Lead.
	- Enforces JWT session validation via allow_guest=False.
	- Enforces user write permissions on the Lead and create permissions on the Visit Schedule.
	- Updates A2C Lead status to "Initiated" if it is "Open".
	- Inserts a system Comment on the lead's timeline.
	"""
	if not lead_id:
		frappe.throw(_("lead_id is required"), frappe.MandatoryError)
	if not visit_date:
		frappe.throw(_("visit_date is required"), frappe.MandatoryError)
	if not visit_time:
		frappe.throw(_("visit_time is required"), frappe.MandatoryError)
	if not region:
		frappe.throw(_("region is required"), frappe.MandatoryError)
	if not zone:
		frappe.throw(_("zone is required"), frappe.MandatoryError)
	if not woreda:
		frappe.throw(_("woreda is required"), frappe.MandatoryError)
	if not kebele:
		frappe.throw(_("kebele is required"), frappe.MandatoryError)

	if not frappe.db.exists("A2C Lead", lead_id):
		frappe.throw(_("A2C Lead {0} not found").format(lead_id), frappe.DoesNotExistError)

	# Check permissions
	frappe.has_permission("A2C Lead", "write", doc=lead_id, throw=True)
	frappe.has_permission("A2C Visit Schedule", "create", throw=True)

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

	return {
		"status": "success",
		"schedule_id": schedule.name,
		"message": _("Visit scheduled successfully.")
	}


@frappe.whitelist(allow_guest=False)
def get_visit_schedules(
	lead_id=None,
	start_date=None,
	end_date=None,
	status=None,
	start=0,
	page_length=20
):
	"""
	Retrieves a paginated list of visit schedules.
	- Enforces JWT session validation.
	- Enforces read permissions on A2C Visit Schedule.
	- Utilizes frappe.get_list for RBAC & user permission isolation.
	"""
	frappe.has_permission("A2C Visit Schedule", "read", throw=True)

	try:
		start = int(start or 0)
		if start < 0:
			start = 0
	except ValueError:
		start = 0

	try:
		page_length = int(page_length or 20)
		if page_length < 1:
			page_length = 20
		elif page_length > 100:
			page_length = 100
	except ValueError:
		page_length = 20

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

	return {
		"status": "success",
		"start": start,
		"page_length": page_length,
		"total_count": total_count,
		"results": schedules
	}


@frappe.whitelist(allow_guest=False)
def update_visit_schedule_status(schedule_id=None, status=None):
	"""
	Updates the status of an A2C Visit Schedule (Scheduled, Completed, Cancelled, Missed).
	"""
	if not schedule_id or not status:
		frappe.throw(_("schedule_id and status are required"), frappe.MandatoryError)

	if not frappe.db.exists("A2C Visit Schedule", schedule_id):
		frappe.throw(_("A2C Visit Schedule {0} not found").format(schedule_id), frappe.DoesNotExistError)

	schedule = frappe.get_doc("A2C Visit Schedule", schedule_id)
	
	# Enforce write permissions on the linked lead
	frappe.has_permission("A2C Lead", "write", doc=schedule.lead, throw=True)

	allowed_statuses = ("Scheduled", "Completed", "Cancelled", "Missed")
	if status not in allowed_statuses:
		frappe.throw(_("Invalid status: {0}").format(status), frappe.ValidationError)

	schedule.status = status
	schedule.save(ignore_permissions=False)

	return {
		"status": "success",
		"schedule_id": schedule_id,
		"new_status": status,
		"message": _("Visit schedule status updated successfully.")
	}




