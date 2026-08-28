import frappe
from frappe.model.document import Document


class A2CLoanProductAuditEvent(Document):
	pass


def log_product_audit_event(
	product_doc,
	event_type: str = "Status Changed",
	from_status: str | None = None,
	to_status: str | None = None,
	reason: str | None = None,
	changed_fields: dict | str | None = None,
	user: str | None = None,
):
	"""Record an immutable audit event for an A2C Loan Product.

	Captures status transition, reason, performing user, and field diffs.
	"""
	performing_user = user or getattr(frappe.session, "user", "Administrator")

	description_parts = []
	if from_status and to_status:
		description_parts.append(f"Status updated from '{from_status}' to '{to_status}'.")
	elif to_status:
		description_parts.append(f"Status set to '{to_status}'.")

	if changed_fields:
		if isinstance(changed_fields, dict):
			diff_lines = [f"• {k}: '{v[0]}' -> '{v[1]}'" for k, v in changed_fields.items()]
			description_parts.append("Modified fields:\n" + "\n".join(diff_lines))
		else:
			description_parts.append(f"Modified fields: {changed_fields}")

	if reason:
		description_parts.append(f"Reason: {reason}")

	event_title = (
		f"Product Status Changed: {from_status or 'New'} -> {to_status}"
		if to_status
		else f"Product {event_type}"
	)

	audit_doc = frappe.get_doc(
		{
			"doctype": "A2C Loan Product Audit Event",
			"loan_product": product_doc.name,
			"bank": product_doc.bank,
			"event_type": event_type,
			"from_status": from_status,
			"to_status": to_status,
			"event_title": event_title,
			"event_description": "\n\n".join(description_parts),
			"reason": reason,
			"performed_by": performing_user,
		}
	)
	audit_doc.insert(ignore_permissions=True)
	return audit_doc
