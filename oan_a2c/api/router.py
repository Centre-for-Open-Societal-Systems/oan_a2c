"""
REST API Router for OpenAgriNet Access to Credit (OAN A2C).

This module implements a pure REST facade mapping the 94 endpoints specified in
openapi_v1.yaml to their corresponding Frappe backend controller functions.
It handles path variables ({id}, {userId}, etc.), HTTP verbs (GET, POST, PATCH,
PUT, DELETE), parameter extraction/normalization, and standardized JSON envelopes.
"""

import json
import os
import re
from functools import lru_cache
from typing import Any

import frappe
from frappe import _
from werkzeug.exceptions import HTTPException, MethodNotAllowed, NotFound
from werkzeug.routing import Map, Rule
from werkzeug.wrappers import Request, Response

from oan_a2c.api.utils import error_response, success_response


def get_spec_path() -> str:
	"""Locate openapi_v1.yaml relative to this app root."""
	app_path = frappe.get_app_path("oan_a2c")
	spec_path = os.path.join(app_path, "..", "openapi", "openapi_v1.yaml")
	if os.path.exists(spec_path):
		return os.path.abspath(spec_path)
	return ""


@lru_cache(maxsize=1)
def get_routes_spec() -> list[tuple[str, str, str]]:
	"""Load route definitions (METHOD, PATH, ENDPOINT) from openapi_v1.yaml."""
	routes: list[tuple[str, str, str]] = []
	spec_file = get_spec_path()
	if spec_file and os.path.exists(spec_file):
		try:
			import yaml

			with open(spec_file, encoding="utf-8") as f:
				spec = yaml.safe_load(f)
			paths = spec.get("paths", {})
			for path, methods in paths.items():
				for method, details in methods.items():
					if isinstance(details, dict) and "x-legacy-rpc-method" in details:
						routes.append((method.upper(), path, details["x-legacy-rpc-method"]))
		except Exception as e:
			frappe.logger("oan_a2c").error(f"Failed to load openapi_v1.yaml: {e}")

	if not routes:
		routes = _FALLBACK_ROUTES

	return routes


def build_url_map() -> Map:
	"""Compile all REST routes into a Werkzeug URL Map."""
	rules: list[Rule] = []
	routes = get_routes_spec()

	for method, path, endpoint in routes:
		# Convert OpenAPI {param} syntax to Werkzeug <param> syntax
		wz_path = re.sub(r"\{([^}]+)\}", r"<\1>", path)
		# Support both /v1/... and /api/v1/...
		rules.append(Rule(wz_path, endpoint=endpoint, methods=[method]))
		if not wz_path.startswith("/api/"):
			rules.append(Rule(f"/api{wz_path}", endpoint=endpoint, methods=[method]))

	return Map(rules, strict_slashes=False)


# Pre-compiled URL Map
API_URL_MAP = build_url_map()


def expand_path_param_aliases(values: dict[str, Any]) -> dict[str, Any]:
	"""Inject standard domain-specific keyword aliases for path variables.

	This ensures compatibility with controllers expecting either generic {id}
	or domain-specific {product_id}, {lead_id}, {application_id}, {email}, etc.
	"""
	expanded = dict(values)
	if "id" in values:
		val = values["id"]
		expanded.setdefault("product_id", val)
		expanded.setdefault("lead_id", val)
		expanded.setdefault("application_id", val)
		expanded.setdefault("loan_application_id", val)
		expanded.setdefault("schedule_id", val)
		expanded.setdefault("visit_schedule_id", val)

	if "userId" in values:
		val = values["userId"]
		expanded.setdefault("email", val)
		expanded.setdefault("user_id", val)

	if "productId" in values:
		val = values["productId"]
		expanded.setdefault("loan_product", val)
		expanded.setdefault("product_id", val)

	if "bankId" in values:
		val = values["bankId"]
		expanded.setdefault("bank", val)
		expanded.setdefault("bank_id", val)

	if "docId" in values:
		val = values["docId"]
		expanded.setdefault("doc_id", val)
		expanded.setdefault("document_id", val)
		expanded.setdefault("docname", val)

	return expanded


def parse_request_data(request: Request) -> dict[str, Any]:
	"""Extract request payload from query params, json body, and form data."""
	data: dict[str, Any] = {}

	# 1. Query parameters
	if request.args:
		data.update(request.args.to_dict(flat=True))

	# 2. Form data / Multi-part
	if request.form:
		data.update(request.form.to_dict(flat=True))

	# 3. JSON body
	if request.content_type and "application/json" in request.content_type:
		raw_data = request.get_data()
		if raw_data:
			try:
				parsed_json = json.loads(raw_data)
				if isinstance(parsed_json, dict):
					data.update(parsed_json)
			except Exception:
				pass

	return data


def dispatch_rest_request(request: Request) -> Response:
	"""Match incoming request against REST URL map, invoke target controller, and return Response."""
	adapter = API_URL_MAP.bind_to_environ(request.environ)

	try:
		endpoint, path_args = adapter.match(request.path, method=request.method)
	except NotFound:
		frappe.local.response["http_status_code"] = 404
		return Response(
			json.dumps(
				{
					"status": "error",
					"message": f"Endpoint not found: {request.method} {request.path}",
					"code": "NOT_FOUND",
					"details": {},
				}
			),
			status=404,
			mimetype="application/json",
		)
	except MethodNotAllowed as e:
		frappe.local.response["http_status_code"] = 405
		return Response(
			json.dumps(
				{
					"status": "error",
					"message": f"Method {request.method} not allowed for {request.path}",
					"code": "METHOD_NOT_ALLOWED",
					"details": {"valid_methods": list(e.valid_methods or [])},
				}
			),
			status=405,
			mimetype="application/json",
		)

	# Merge path params and request body/query into a unified argument dictionary
	params = parse_request_data(request)
	expanded_path_args = expand_path_param_aliases(path_args)
	params.update(expanded_path_args)

	# Update frappe.local.form_dict so standard Frappe methods access inputs
	if not hasattr(frappe.local, "form_dict") or frappe.local.form_dict is None:
		frappe.local.form_dict = frappe._dict()
	frappe.local.form_dict.update(params)

	# Resolve controller method
	try:
		fn = frappe.get_attr(endpoint)
	except Exception as e:
		frappe.logger("oan_a2c").error(f"Failed to resolve endpoint {endpoint}: {e}")
		return Response(
			json.dumps(
				{
					"status": "error",
					"message": f"Handler resolution error for {endpoint}",
					"code": "INTERNAL_ERROR",
					"details": {},
				}
			),
			status=500,
			mimetype="application/json",
		)

	# Execute target function
	try:
		result = frappe.call(fn, **params)
	except Exception as e:
		if isinstance(e, HTTPException):
			return e.get_response(request.environ)
		raise

	if isinstance(result, Response):
		return result

	# Set HTTP response code if set in frappe.response or frappe.local.response
	status_code = (
		frappe.local.response.get("http_status_code") or frappe.response.get("http_status_code") or 200
	)

	return Response(
		json.dumps(result, default=str),
		status=status_code,
		mimetype="application/json",
	)


# ---------------------------------------------------------------------------
# Static Fallback Routes (derived directly from openapi_v1.yaml)
# ---------------------------------------------------------------------------
_FALLBACK_ROUTES = [
	# Domain 01: Identity & Access
	("POST", "/v1/auth/register", "oan_a2c.api.v1.auth.register_user"),
	("POST", "/v1/auth/login", "oan_a2c.api.auth.login"),
	("POST", "/v1/auth/token/refresh", "oan_a2c.api.auth.refresh"),
	("POST", "/v1/auth/logout", "oan_a2c.api.auth.logout"),
	("POST", "/v1/auth/password/forgot", "oan_a2c.api.auth.forgot_password"),
	("POST", "/v1/auth/password/reset", "oan_a2c.api.auth.reset_password"),
	("POST", "/v1/auth/password/initial", "oan_a2c.api.auth.set_initial_password"),
	("PATCH", "/v1/me/password", "oan_a2c.api.auth.change_password"),
	("GET", "/v1/me", "oan_a2c.api.auth.get_me"),
	("GET", "/v1/me/profile", "oan_a2c.api.auth.get_user_profile"),
	("PATCH", "/v1/me/profile", "oan_a2c.api.auth.update_profile"),
	# Domain 02: Bank Onboarding & Administration
	("POST", "/v1/banks", "oan_a2c.api.v1.seller.onboarding.register_bank"),
	("GET", "/v1/banks/me", "oan_a2c.api.v1.seller.onboarding.get_bank_profile"),
	("PATCH", "/v1/banks/me", "oan_a2c.api.v1.seller.onboarding.update_bank_profile"),
	("PATCH", "/v1/banks/me/status", "oan_a2c.api.v1.seller.onboarding.update_bank_status"),
	("POST", "/v1/banks/me/kyc-documents", "oan_a2c.api.v1.seller.onboarding.upload_kyc_document"),
	("POST", "/v1/banks/me/logo", "oan_a2c.api.v1.seller.onboarding.upload_image"),
	("PUT", "/v1/banks/me/contacts", "oan_a2c.api.v1.seller.onboarding.save_org_contacts"),
	("GET", "/v1/banks/me/team", "oan_a2c.api.v1.seller.onboarding.list_users"),
	("POST", "/v1/banks/me/team", "oan_a2c.api.v1.seller.onboarding.invite_team_member"),
	("PATCH", "/v1/banks/me/team/{userId}", "oan_a2c.api.v1.seller.onboarding.update_user"),
	(
		"POST",
		"/v1/banks/me/team/{userId}/password-reset",
		"oan_a2c.api.v1.seller.onboarding.reset_member_password",
	),
	("GET", "/v1/banks/me/dashboard/stats", "oan_a2c.api.v1.seller.dashboard.get_stats"),
	# Domain 03: Bank Cataloging
	("POST", "/v1/banks/me/products", "oan_a2c.api.v1.seller.loan_products.create_product"),
	("GET", "/v1/banks/me/products", "oan_a2c.api.v1.seller.loan_products.list_products"),
	("GET", "/v1/banks/me/products/{id}", "oan_a2c.api.v1.seller.loan_products.get_product"),
	("PATCH", "/v1/banks/me/products/{id}", "oan_a2c.api.v1.seller.loan_products.update_product"),
	("PATCH", "/v1/banks/me/products/{id}/status", "oan_a2c.api.v1.seller.loan_products.set_product_status"),
	(
		"GET",
		"/v1/banks/me/products/{id}/audit-log",
		"oan_a2c.api.v1.seller.loan_products.get_product_comment",
	),
	("PUT", "/v1/banks/me/products/{id}/categories", "oan_a2c.api.v1.seller.taxonomy.set_product_categories"),
	("PUT", "/v1/banks/me/products/{id}/tags", "oan_a2c.api.v1.seller.taxonomy.set_product_tags"),
	("PUT", "/v1/banks/me/products/{id}/attributes", "oan_a2c.api.v1.seller.taxonomy.set_product_attributes"),
	("GET", "/v1/taxonomy/categories", "oan_a2c.api.v1.seller.taxonomy.get_categories"),
	("GET", "/v1/taxonomy/tags", "oan_a2c.api.v1.seller.taxonomy.get_tags"),
	("GET", "/v1/taxonomy/attributes", "oan_a2c.api.v1.seller.taxonomy.get_attributes"),
	("POST", "/v1/admin/taxonomy/categories", "oan_a2c.api.v1.seller.taxonomy.create_category"),
	("POST", "/v1/admin/taxonomy/tags", "oan_a2c.api.v1.seller.taxonomy.create_tag"),
	("POST", "/v1/admin/taxonomy/attribute-terms", "oan_a2c.api.v1.seller.taxonomy.create_attribute_term"),
	("GET", "/v1/banks/me/pipeline-stages", "oan_a2c.api.v1.seller.loan_stages.get_stages"),
	("POST", "/v1/banks/me/pipeline-stages", "oan_a2c.api.v1.seller.loan_stages.add_stage"),
	("PUT", "/v1/banks/me/pipeline-stages", "oan_a2c.api.v1.seller.loan_stages.sync_stages"),
	# Domain 04: Catalog Discovery
	("GET", "/v1/catalog/products", "oan_a2c.api.v1.farmer.catalog.list_catalog"),
	("GET", "/v1/catalog/banks/{bankId}", "oan_a2c.api.v1.farmer.catalog.get_bank_details"),
	("GET", "/v1/catalog/facets", "oan_a2c.api.v1.farmer.catalog.get_catalog_facets"),
	("GET", "/v1/catalog/saved-products", "oan_a2c.api.v1.farmer.catalog.get_saved_products"),
	("PUT", "/v1/catalog/saved-products/{productId}", "oan_a2c.api.v1.farmer.catalog.save_product"),
	("DELETE", "/v1/catalog/saved-products/{productId}", "oan_a2c.api.v1.farmer.catalog.unsave_product"),
	("GET", "/v1/me/dashboard", "oan_a2c.api.v1.farmer.dashboard.get_dashboard_summary"),
	# Domain 05: Applications (Farmer Self-Service)
	("POST", "/v1/applications", "oan_a2c.api.v1.farmer.applications.create_application"),
	("GET", "/v1/applications", "oan_a2c.api.v1.farmer.applications.list_applications"),
	("GET", "/v1/applications/{id}", "oan_a2c.api.v1.farmer.applications.get_application"),
	("PATCH", "/v1/applications/{id}", "oan_a2c.api.v1.farmer.applications.update_application"),
	("POST", "/v1/applications/{id}/submit", "oan_a2c.api.v1.farmer.applications.submit_application"),
	# Domain 06: CRM - Leads & Field Ops
	("POST", "/v1/leads", "oan_a2c.api.v1.leads.create_lead"),
	("GET", "/v1/leads", "oan_a2c.api.v1.leads.get_leads"),
	("GET", "/v1/leads/summary", "oan_a2c.api.v1.leads.get_lead_summary"),
	("GET", "/v1/leads/metadata", "oan_a2c.api.v1.leads.get_lead_metadata"),
	("GET", "/v1/leads/assignable-users", "oan_a2c.api.v1.leads.get_assignable_users"),
	("PATCH", "/v1/leads/{id}/status", "oan_a2c.api.v1.leads.update_lead_status"),
	("PATCH", "/v1/leads/{id}/assignment", "oan_a2c.api.v1.leads.assign_lead"),
	("POST", "/v1/leads/{id}/comments", "oan_a2c.api.v1.leads.add_lead_comment"),
	("GET", "/v1/leads/{id}/timeline", "oan_a2c.api.v1.leads.get_lead_timeline"),
	("GET", "/v1/leads/{id}/call-logs", "oan_a2c.api.v1.leads.get_lead_call_logs"),
	("GET", "/v1/leads/{id}/credit-info", "oan_a2c.api.v1.leads.get_lead_credit_infos"),
	("POST", "/v1/leads/{id}/credit-info", "oan_a2c.api.v1.leads.add_lead_credit_info"),
	("GET", "/v1/visit-schedules", "oan_a2c.api.v1.leads.get_visit_schedules"),
	("POST", "/v1/visit-schedules", "oan_a2c.api.v1.leads.schedule_visit"),
	("PATCH", "/v1/visit-schedules/{id}/status", "oan_a2c.api.v1.leads.update_visit_schedule_status"),
	# Domain 07: Loan Underwriting
	("POST", "/v1/loan-applications", "oan_a2c.api.v1.loan_applications.create_loan_application"),
	("GET", "/v1/loan-applications", "oan_a2c.api.v1.loan_applications.get_all_loans"),
	("GET", "/v1/loan-applications/summary", "oan_a2c.api.v1.loan_applications.get_loan_summary"),
	("GET", "/v1/loan-applications/metadata", "oan_a2c.api.v1.loan_applications.get_loan_metadata"),
	("GET", "/v1/loan-applications/{id}/full-profile", "oan_a2c.api.v1.loan_applications.get_full_profile"),
	("GET", "/v1/loan-applications/{id}/basic-profile", "oan_a2c.api.v1.loan_applications.get_basic_profile"),
	(
		"PATCH",
		"/v1/loan-applications/{id}/basic-profile",
		"oan_a2c.api.v1.loan_applications.update_basic_profile",
	),
	("PATCH", "/v1/loan-applications/{id}/status", "oan_a2c.api.v1.loan_applications.update_loan_status"),
	("PATCH", "/v1/loan-applications/{id}/step", "oan_a2c.api.v1.loan_applications.update_loan_step"),
	("PATCH", "/v1/loan-applications/{id}/officer", "oan_a2c.api.v1.loan_applications.assign_loan_officer"),
	(
		"GET",
		"/v1/loan-applications/{id}/documents",
		"oan_a2c.api.v1.loan_applications.get_supporting_documents",
	),
	(
		"POST",
		"/v1/loan-applications/{id}/documents",
		"oan_a2c.api.v1.loan_applications.upload_supporting_documents",
	),
	(
		"GET",
		"/v1/loan-applications/{id}/documents/{docId}/content",
		"oan_a2c.api.v1.loan_applications.download_supporting_document",
	),
	(
		"DELETE",
		"/v1/loan-applications/{id}/documents/{docId}",
		"oan_a2c.api.v1.loan_applications.delete_supporting_document",
	),
	# Domain 08: Consent Management
	("GET", "/v1/consent/farmers", "oan_a2c.api.v1.consent.consent.search_farmer"),
	("GET", "/v1/consent/reasons", "oan_a2c.api.v1.consent.consent.get_consent_reasons"),
	("GET", "/v1/consent/allowed-fields", "oan_a2c.api.v1.consent.consent.get_consent_allowed_fields"),
	(
		"GET",
		"/v1/consent/partners/me/allowed-field-ids",
		"oan_a2c.api.v1.consent.consent.get_partner_allowed_data_field_ids",
	),
	("POST", "/v1/consent/otp", "oan_a2c.api.v1.consent.consent.request_otp"),
	("POST", "/v1/consent/otp/verify", "oan_a2c.api.v1.consent.consent.verify_otp"),
	("POST", "/v1/consent/requests", "oan_a2c.api.v1.consent.consent.submit_consent"),
	("POST", "/v1/webhooks/consent-data", "oan_a2c.api.v1.webhook_consent_data.receive_consent_data"),
	# Domain 09: Notifications
	("GET", "/v1/notifications", "oan_a2c.api.v1.notifications.get_notifications"),
	("PATCH", "/v1/notifications/read", "oan_a2c.api.v1.notifications.mark_read"),
	("DELETE", "/v1/notifications", "oan_a2c.api.v1.notifications.clear"),
	# Domain 10: Inbound Webhooks
	("POST", "/v1/webhooks/leads", "oan_a2c.api.v1.webhooks.lead_inbound"),
]
