"""Notification API + shared writer for the notification bell.

- Endpoints require a JWT/session (allow_guest=False); they operate on
  Notification Log records for frappe.session.user only.
- notify_users() is the shared, best-effort writer reused by doctype lifecycle
  hooks (A2C Loan Application / A2C Loan Product). It mirrors notify_lead_event
  in api/utils.py: it persists a Notification Log (which also pushes realtime via
  Frappe) and never raises, so a business write is not failed by a notification.
"""

import frappe
from pydantic import BaseModel, Field

from oan_a2c.api.utils import handle_api_errors, success_response, to_tz_aware_iso, validate_request


def notify_users(users, subject, message=None, doctype=None, docname=None, notification_type="Alert"):
	"""Create a Notification Log for each user in `users`. Best-effort, never raises.

	The acting user (frappe.session.user) is excluded here as a safety net so no
	caller can accidentally notify the actor about their own action.
	"""
	actor = frappe.session.user
	recipients = {u for u in (users or []) if u and u != actor}
	for user in recipients:
		try:
			frappe.get_doc(
				{
					"doctype": "Notification Log",
					"for_user": user,
					"type": notification_type,
					"subject": subject,
					"email_content": message or subject,
					"document_type": doctype,
					"document_name": docname,
				}
			).insert(ignore_permissions=True)
		except Exception:
			frappe.logger().warning(f"Could not create Notification Log for user {user}")


# --- Request schemas -------------------------------------------------------


class GetNotificationsSchema(BaseModel):
	read_status: str = Field("all", pattern="^(unread|read|all)$")
	limit: int = Field(20, ge=1, le=100)
	start: int = Field(0, ge=0)


class MarkReadSchema(BaseModel):
	notification_ids: list[str] | None = None
	mark_all: bool = False


class ClearSchema(BaseModel):
	notification_ids: list[str] | None = None
	clear_all: bool = False


# --- Endpoints -------------------------------------------------------------


@frappe.whitelist(allow_guest=False)
@validate_request(GetNotificationsSchema)
@handle_api_errors
def get_notifications(**kwargs):
	"""Paginated notifications for the current user, plus the unread badge count."""
	read_status = kwargs["read_status"]
	limit = kwargs["limit"]
	start = kwargs["start"]
	user = frappe.session.user

	filters = {"for_user": user}
	if read_status == "unread":
		filters["read"] = 0
	elif read_status == "read":
		filters["read"] = 1

	notifications = frappe.get_all(
		"Notification Log",
		filters=filters,
		fields=[
			"name as id",
			"subject",
			"email_content",
			"document_type",
			"document_name",
			"read",
			"creation",
		],
		order_by="creation desc",
		limit_page_length=limit,
		limit_start=start,
	)

	for n in notifications:
		if n.get("creation"):
			n["creation"] = to_tz_aware_iso(n["creation"])

	total = frappe.db.count("Notification Log", filters=filters)
	unread_count = frappe.db.count("Notification Log", filters={"for_user": user, "read": 0})

	pagination = {
		"page": (start // limit) + 1,
		"limit": limit,
		"total": total,
		"total_pages": -(-total // limit),
		"has_next": start + limit < total,
	}

	return success_response(
		data={"unread_count": unread_count, "notifications": notifications},
		message="Notifications retrieved successfully",
		pagination=pagination,
	)


@frappe.whitelist(allow_guest=False)
@validate_request(MarkReadSchema)
@handle_api_errors
def mark_read(**kwargs):
	"""Mark notifications read: the given ids, or all unread when mark_all is set.

	Scoped to for_user = current user, so a user can only touch their own records.
	"""
	user = frappe.session.user
	filters = {"for_user": user, "read": 0}
	if not kwargs["mark_all"]:
		ids = kwargs["notification_ids"] or []
		if not ids:
			return success_response(data={"updated": 0}, message="No notifications to update")
		filters["name"] = ["in", ids]

	names = frappe.get_all("Notification Log", filters=filters, pluck="name")
	for name in names:
		frappe.db.set_value("Notification Log", name, "read", 1, update_modified=False)

	return success_response(data={"updated": len(names)}, message="Notifications marked as read")


@frappe.whitelist(allow_guest=False)
@validate_request(ClearSchema)
@handle_api_errors
def clear(**kwargs):
	"""Delete notifications: the given ids, or all of the user's when clear_all is set."""
	user = frappe.session.user
	filters = {"for_user": user}
	if not kwargs["clear_all"]:
		ids = kwargs["notification_ids"] or []
		if not ids:
			return success_response(data={"deleted": 0}, message="No notifications to clear")
		filters["name"] = ["in", ids]

	names = frappe.get_all("Notification Log", filters=filters, pluck="name")
	for name in names:
		frappe.delete_doc("Notification Log", name, ignore_permissions=True, force=True)

	return success_response(data={"deleted": len(names)}, message="Notifications cleared")
