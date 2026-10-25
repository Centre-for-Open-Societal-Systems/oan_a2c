"""Base class for tests that call the REST API the way the gateway does.

Requests go through the JWT middleware and `dispatch_rest_request`, so routing,
auth and the response envelope are covered, not just the endpoint function.
Inheriting `IntegrationTestCase` rolls the database back after the class; a test
that needs to cross a commit should not use this base.
"""

import json

import frappe
from frappe.tests import IntegrationTestCase
from oan_auth_service.testing import api_request
from oan_auth_service.tests.utils import configured_keys

from oan_a2c.api.router import dispatch_rest_request, ensure_routes_registered


def set_column(doctype, column, value, **where):
	"""Write one column directly, for what the ORM refuses to change: a name, or a frozen field."""
	table = frappe.qb.DocType(doctype)
	query = frappe.qb.update(table).set(table[column], value)
	for field, expected in where.items():
		query = query.where(table[field] == expected)
	query.run()


def force_delete_loan(name):
	"""Loan is submittable; clear docstatus so a submitted (Approved/Rejected) record deletes without the cancel-first guard."""
	if frappe.db.exists("A2C Loan Application", name):
		frappe.db.set_value("A2C Loan Application", name, "docstatus", 0, update_modified=False)
		frappe.delete_doc("A2C Loan Application", name, ignore_permissions=True, force=True)


class ApiTestCase(IntegrationTestCase):
	def api(self, method: str, path: str, **kwargs):
		"""Send a request; returns `(status_code, body)` with the JSON body decoded."""
		# A worker registers the routes and the JWT namespace on its first request;
		# a test process has to do the same before the middleware can see the path.
		ensure_routes_registered()
		# Keys are set here, not in setUp: subclasses define their own setUp.
		with configured_keys():
			response = api_request(dispatch_rest_request, method, path, **kwargs)
		return response.status_code, json.loads(response.get_data(as_text=True))

	def make_user(self, email: str, roles: list[str]) -> str:
		"""An enabled user holding `roles`, removed again when the class rolls back."""
		if not frappe.db.exists("User", email):
			frappe.get_doc(
				{
					"doctype": "User",
					"email": email,
					"first_name": email.split("@")[0],
					"send_welcome_email": 0,
					"enabled": 1,
					"roles": [{"role": role} for role in roles],
				}
			).insert(ignore_permissions=True)
		return email
