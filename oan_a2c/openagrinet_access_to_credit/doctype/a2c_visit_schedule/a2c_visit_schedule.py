import frappe
from frappe import _
from frappe.model.document import Document

class A2CVisitSchedule(Document):
	def validate(self):
		self.validate_status_transition()

	def validate_status_transition(self):
		if not self.is_new():
			# Fetch the previous status database value
			db_status = frappe.db.get_value("A2C Visit Schedule", self.name, "status")
			if db_status in ("Missed", "Completed") and db_status != self.status:
				frappe.throw(
					_("Cannot update status of a {0} visit.").format(db_status),
					frappe.ValidationError
				)
