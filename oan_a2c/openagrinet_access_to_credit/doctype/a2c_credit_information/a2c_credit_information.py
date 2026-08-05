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


def sync_lead_loan_amount(doc, method=None):
	"""Denormalize this credit info's loan_amount onto its A2C Lead.

	`A2C Lead.loan_amount` is a read-only snapshot used so the leads list can
	sort/filter by amount at the DB layer (loan_amount natively lives here, on a
	linked doctype). Leads are effectively 1:1 with Credit Information, so the
	lead simply mirrors its credit row; on trash we clear it.

	Best-effort: a sync failure must not fail the credit-info write.
	"""
	if not doc.lead:
		return
	try:
		value = 0 if method == "on_trash" else (doc.loan_amount or 0)
		# db_set on the Lead avoids a full doc load/validate cycle and doesn't
		# bump modified, keeping this a pure denormalization side-channel.
		frappe.db.set_value("A2C Lead", doc.lead, "loan_amount", value, update_modified=False)
	except Exception:
		frappe.logger().warning(
			f"Could not sync loan_amount to A2C Lead {doc.lead} from Credit Information {doc.name}"
		)
