import frappe


def execute():
	"""Stamp existing loan applications as agent-sourced.

	`application_source` replaces a permission query that identified self-service
	applications by testing whether the row's owner held the farmer role -- which
	meant materialising every farmer user into a NOT IN clause on each request.
	Filtering an indexed column on the row itself needs the column populated for
	rows created before it existed.

	Every existing row is stamped 'Agent'. The self-service flow has not been
	released, so no genuine self-service application predates this patch. We
	deliberately do NOT infer the source from an empty `lead_id`: that field is not
	mandatory on A2C Loan Application, so an agent-created row lacking one would be
	misfiled as self-service and vanish from every Development Agent's list.

	Idempotent: only rows with no source set are touched.
	"""
	if not frappe.db.table_exists("A2C Loan Application"):
		return

	if not frappe.db.has_column("A2C Loan Application", "application_source"):
		return

	frappe.db.sql(
		"""
		UPDATE `tabA2C Loan Application`
		SET `application_source` = 'Agent'
		WHERE ifnull(`application_source`, '') = ''
		"""
	)  # bank-scope-exempt
