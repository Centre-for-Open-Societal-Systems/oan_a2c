import frappe
from frappe import _
from frappe.model.document import Document


class A2CCreditInformation(Document):
	def before_save(self):
		self._validate_lead_existence()
		self._validate_loan_amount()
		self._populate_creator()

	def _validate_lead_existence(self):
		if not self.lead:
			frappe.throw(_("Lead is required"), frappe.MandatoryError)
		if not frappe.db.exists("A2C Lead", self.lead):
			frappe.throw(_("A2C Lead {0} does not exist").format(self.lead), frappe.DoesNotExistError)

	def _validate_loan_amount(self):
		try:
			amount = float(self.loan_amount or 0)
		except (ValueError, TypeError):
			frappe.throw(_("Loan Amount must be a valid number"), frappe.ValidationError)

		if amount <= 0:
			frappe.throw(_("Loan Amount must be a positive non-zero number"), frappe.ValidationError)

	def _populate_creator(self):
		if not self.created_by:
			self.created_by = frappe.session.user
