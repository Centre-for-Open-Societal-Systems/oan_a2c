import frappe
from frappe.custom.doctype.property_setter.property_setter import make_property_setter

from oan_a2c.api.utils import workflow_status_options


def setup_workflow_property_setters():
	"""Keep the loan application status Select options aligned with the workflow."""
	options = workflow_status_options("A2C Loan Application")
	if not options:
		return

	frappe.db.delete(
		"Property Setter",
		{"doc_type": "A2C Loan Application", "field_name": "status", "property": "options"},
	)
	make_property_setter(
		"A2C Loan Application",
		"status",
		"options",
		options,
		"Text",
	)
