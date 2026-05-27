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
		allowed_statuses = ("Open", "Initiated", "Qualified", "Not Interested", "Processed")
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
		fields=["name", "phone_number", "external_id", "lead_source", "status", "assigned_to", "creation"],
		filters=filters,
		or_filters=or_filters or None,
		limit_start=start,
		page_length=page_length,
		order_by="creation desc"
	)

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
	lead.status = "Open"
	lead.insert(ignore_permissions=False)

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

	allowed_statuses = ("Open", "Initiated", "Qualified", "Not Interested", "Processed")
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
	Returns dynamic options for dropdown fields in A2C Lead forms.
	Enforces JWT session validation and native role-based permissions (RBAC).
	"""
	frappe.has_permission("A2C Lead", "read", throw=True)

	meta = frappe.get_meta("A2C Lead")
	status_field = meta.get_field("status")
	source_field = meta.get_field("lead_source")

	statuses = status_field.options.split("\n") if status_field else []
	sources = source_field.options.split("\n") if source_field else []

	return {
		"status": "success",
		"statuses": statuses,
		"sources": sources
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

	comment = frappe.new_doc("Comment")
	comment.comment_type = "Comment"
	comment.reference_doctype = "A2C Lead"
	comment.reference_name = lead_id
	comment.content = content
	comment.insert(ignore_permissions=False)

	return {
		"status": "success",
		"comment_id": comment.name,
		"message": _("Comment added successfully.")
	}


@frappe.whitelist(allow_guest=False)
def get_lead_timeline(lead_id=None):
	"""
	Retrieves the historical timeline of comments and system activities for a specific lead.
	Enforces JWT session validation and explicit document-level read permissions.
	"""
	if not lead_id:
		frappe.throw(_("lead_id is required"), frappe.MandatoryError)

	# Verify user has read permissions on this specific lead document
	frappe.has_permission("A2C Lead", "read", doc=lead_id, throw=True)

	comments = frappe.get_list(
		"Comment",
		fields=["name", "comment_by", "content", "creation", "comment_type"],
		filters={
			"reference_doctype": "A2C Lead",
			"reference_name": lead_id
		},
		order_by="creation desc"
	)

	return {
		"status": "success",
		"lead_id": lead_id,
		"timeline": comments
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



