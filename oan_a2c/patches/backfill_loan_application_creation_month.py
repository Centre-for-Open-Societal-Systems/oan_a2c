"""Fill `creation_month` on loan applications lodged before the column existed."""

import frappe
from frappe.query_builder.functions import Max, Min
from frappe.utils import add_months, get_first_day, getdate

DOCTYPE = "A2C Loan Application"


def execute():
	"""One UPDATE per calendar month, so it stays a set-based write on any database.

	Idempotent: only rows with no month yet are touched.
	"""
	table = frappe.qb.DocType(DOCTYPE)
	oldest, newest = (
		frappe.qb.from_(table)
		.select(Min(table.creation), Max(table.creation))
		.where(table.creation_month.isnull())
		.run()[0]
	)
	if not oldest:
		return

	month = get_first_day(getdate(oldest))
	last = get_first_day(getdate(newest))
	while month <= last:
		(
			frappe.qb.update(table)
			.set(table.creation_month, month)
			.where(table.creation_month.isnull())
			.where(table.creation >= month)
			.where(table.creation < add_months(month, 1))
			.run()
		)
		month = add_months(month, 1)
