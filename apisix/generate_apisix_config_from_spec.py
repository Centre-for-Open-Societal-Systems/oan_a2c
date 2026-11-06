#!/usr/bin/env python3
"""
generate_apisix_config_from_spec.py

Generates the ADC gateway configuration (adc.yaml) and the APISIX Ingress Controller
resources (k8s/templates, the oan-a2c-gateway Helm chart) for the A2C REST API directly
from openapi_v1.public.yaml. Shared logic lives in oan_auth_service/gateway_apisix.py.

The throttling tiers and the `(method, path) -> tier` table are TIERS and TIER_OVERRIDES
below.

Usage:
    python3 apisix/generate_apisix_config_from_spec.py
"""

import copy
from pathlib import Path

from oan_auth_service import gateway_apisix as gw

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
SPEC_PATH = REPO_ROOT / "openapi" / "openapi_v1.public.yaml"
OUTPUT_PATH = SCRIPT_DIR / "adc.yaml"
K8S_DIR = SCRIPT_DIR / "k8s" / "templates"
APP = "oan-a2c"
SITE_NAME = "${FRAPPE_SITE_NAME}"
# False: Frappe's JWT middleware verifies bearer tokens, as in the other OAN apps.
GATEWAY_JWT = False

API_UPSTREAM = gw.upstream(
	host_env="OAN_A2C_UPSTREAM_HOST",
	port_env="OAN_A2C_UPSTREAM_PORT",
	# Frappe picks the site from the Host header, so send the site name, not the gateway's.
	pass_host="rewrite",
	upstream_host=SITE_NAME,
	timeout={"connect": 5, "send": 20, "read": 20},
	retries=2,
)
API_UPSTREAM["nodes"][0]["host"] = "${OAN_A2C_UPSTREAM_HOST}"

# Throttling tiers. Defaults are a starting point for a review with real traffic data
# before go-live, not a final SLA. `limit_by: "consumer"` needs the caller authenticated;
# `"ip"` is for tiers with no caller to key on (pre-auth, public files).
TIERS = {
	"public-auth": {
		"limit_by": "ip",
		"minute": 5,
		"hour": 30,
		"policy": "redis",
		"note": "Login/registration/password-recovery. Tight, IP-keyed, to blunt "
		"credential stuffing before it reaches Frappe's own lockout logic.",
	},
	"authenticated-core": {
		"limit_by": "consumer",
		"minute": 120,
		"hour": 4000,
		"policy": "redis",
		"note": "Identity/profile/notifications -- any signed-in caller, low cost per call.",
	},
	"farmer-app": {
		"limit_by": "consumer",
		"minute": 90,
		"hour": 3000,
		"policy": "redis",
		"note": "Consumer-facing app traffic: catalog browse, farmer applications, consent capture.",
	},
	"bank-partner-standard": {
		"limit_by": "consumer",
		"minute": 300,
		"hour": 15000,
		"policy": "redis",
		"note": "Bank/partner integration traffic -- onboarding, cataloging, underwriting reads.",
	},
	"crm-internal": {
		"limit_by": "consumer",
		"minute": 600,
		"hour": 30000,
		"policy": "redis",
		"note": "Development Agent / internal CRM tooling -- trusted, higher-volume, still capped.",
	},
	"webhooks-inbound": {
		"limit_by": "consumer",
		"minute": 3000,
		"hour": 120000,
		"policy": "redis",
		"note": "Server-to-server receivers (OpenG2P, telco IVR). IP-allowlisted separately; "
		"the cap here protects against a misbehaving upstream, not abuse.",
	},
	"static-assets": {
		"limit_by": "ip",
		"minute": 3000,
		"hour": 100000,
		"policy": "redis",
		"note": "Public files (/files/*). IP-keyed of necessity: browsers do not send "
		"Authorization on <img> subresource requests, so there is no consumer to key on. "
		"Deliberately loose -- a single catalog page pulls ~40 images, and carrier-grade NAT "
		"puts many users behind one address. Once a CDN fronts this path the origin sees only "
		"cache misses and this can be tightened.",
	},
	"public-dashboards": {
		"limit_by": "consumer",
		"minute": 600,
		"hour": 20000,
		"policy": "redis",
		"note": "Programme dashboard charts: aggregate-only reads by the OAN dashboards, the one "
		"consumer holding a dashboard key. It calls on behalf of every viewer and caches each chart "
		"for 15 minutes, so its steady load is low. Same number as RATE_LIMIT_PER_MINUTE in "
		"oan_a2c/api/v1/dashboard.py.",
	},
	"uploads": {
		"limit_by": "consumer",
		"minute": 10,
		"hour": 200,
		"policy": "redis",
		"note": "Document upload endpoints -- expensive, low-frequency by nature. Paired with "
		"request-size-limiting; see README for the pre-signed-URL migration note.",
	},
}

# Tier of each operation, keyed by (METHOD, PATH) exactly as openapi_v1.public.yaml spells them.
TIER_OVERRIDES = {
	# Domain 01: Identity & Access
	("POST", "/v1/auth/register"): "public-auth",
	("POST", "/v1/auth/login"): "public-auth",
	("POST", "/v1/auth/refresh"): "public-auth",
	("POST", "/v1/auth/logout"): "public-auth",
	("POST", "/v1/auth/password/forgot"): "public-auth",
	("POST", "/v1/auth/password/reset"): "public-auth",
	("POST", "/v1/auth/password/initial"): "public-auth",
	("PATCH", "/v1/me/password"): "authenticated-core",
	("GET", "/v1/auth/me"): "authenticated-core",
	("GET", "/v1/me/profile"): "authenticated-core",
	("PATCH", "/v1/me/profile"): "authenticated-core",
	# Domain 02: Bank Onboarding & Administration
	("POST", "/v1/banks"): "bank-partner-standard",
	("GET", "/v1/banks/me"): "bank-partner-standard",
	("PATCH", "/v1/banks/me"): "bank-partner-standard",
	("PATCH", "/v1/banks/me/status"): "bank-partner-standard",
	("POST", "/v1/banks/me/kyc-documents"): "uploads",
	# Download is a read, not an upload: same tier as the other bank-partner reads,
	# matching GET /v1/loan-applications/{id}/documents/{docId}/content, which sits on
	# its own domain's read tier rather than "uploads".
	("GET", "/v1/banks/me/kyc-documents"): "bank-partner-standard",
	("POST", "/v1/images"): "uploads",
	("PUT", "/v1/banks/me/contacts"): "bank-partner-standard",
	("GET", "/v1/banks/me/team"): "bank-partner-standard",
	("POST", "/v1/banks/me/team"): "bank-partner-standard",
	("PATCH", "/v1/banks/me/team/{userId}"): "bank-partner-standard",
	("POST", "/v1/banks/me/team/{userId}/password-reset"): "bank-partner-standard",
	("GET", "/v1/banks/me/dashboard/stats"): "bank-partner-standard",
	# Domain 03: Bank Cataloging
	("POST", "/v1/banks/me/products"): "bank-partner-standard",
	("GET", "/v1/banks/me/products"): "bank-partner-standard",
	("GET", "/v1/banks/me/products/{id}"): "bank-partner-standard",
	("PATCH", "/v1/banks/me/products/{id}"): "bank-partner-standard",
	("PATCH", "/v1/banks/me/products/{id}/status"): "bank-partner-standard",
	("GET", "/v1/banks/me/products/{id}/audit-log"): "bank-partner-standard",
	("PUT", "/v1/banks/me/products/{id}/categories"): "bank-partner-standard",
	("PUT", "/v1/banks/me/products/{id}/tags"): "bank-partner-standard",
	("PUT", "/v1/banks/me/products/{id}/attributes"): "bank-partner-standard",
	("GET", "/v1/taxonomy/categories"): "authenticated-core",
	("GET", "/v1/taxonomy/tags"): "authenticated-core",
	("GET", "/v1/taxonomy/attributes"): "authenticated-core",
	("POST", "/v1/admin/taxonomy/categories"): "crm-internal",
	("POST", "/v1/admin/taxonomy/tags"): "crm-internal",
	("POST", "/v1/admin/taxonomy/attribute-terms"): "crm-internal",
	("GET", "/v1/banks/me/pipeline-stages"): "bank-partner-standard",
	("POST", "/v1/banks/me/pipeline-stages"): "bank-partner-standard",
	("PUT", "/v1/banks/me/pipeline-stages"): "bank-partner-standard",
	# Domain 04: Catalog Discovery
	("GET", "/v1/catalog/products"): "farmer-app",
	("GET", "/v1/catalog/banks/{bankId}"): "farmer-app",
	("GET", "/v1/catalog/facets"): "farmer-app",
	("GET", "/v1/catalog/saved-products"): "farmer-app",
	("PUT", "/v1/catalog/saved-products/{productId}"): "farmer-app",
	("DELETE", "/v1/catalog/saved-products/{productId}"): "farmer-app",
	("GET", "/v1/me/dashboard"): "farmer-app",
	# Domain 05: Applications (Farmer self-service)
	("POST", "/v1/applications"): "farmer-app",
	("GET", "/v1/applications"): "farmer-app",
	("GET", "/v1/applications/{id}"): "farmer-app",
	("PATCH", "/v1/applications/{id}"): "farmer-app",
	("POST", "/v1/applications/{id}/submit"): "farmer-app",
	# Domain 06: CRM - Leads & Field Ops
	("POST", "/v1/leads"): "crm-internal",
	("GET", "/v1/leads"): "crm-internal",
	("GET", "/v1/leads/summary"): "crm-internal",
	("GET", "/v1/leads/metadata"): "crm-internal",
	("GET", "/v1/leads/assignable-users"): "crm-internal",
	("PATCH", "/v1/leads/{id}/status"): "crm-internal",
	("PATCH", "/v1/leads/{id}/assignment"): "crm-internal",
	("POST", "/v1/leads/{id}/comments"): "crm-internal",
	("GET", "/v1/leads/{id}/timeline"): "crm-internal",
	("GET", "/v1/leads/{id}/call-logs"): "crm-internal",
	("GET", "/v1/leads/{id}/credit-info"): "crm-internal",
	("POST", "/v1/leads/{id}/credit-info"): "crm-internal",
	("GET", "/v1/visit-schedules"): "crm-internal",
	("POST", "/v1/visit-schedules"): "crm-internal",
	("PATCH", "/v1/visit-schedules/{id}/status"): "crm-internal",
	# Domain 07: Loan Underwriting
	("POST", "/v1/loan-applications"): "crm-internal",
	("GET", "/v1/loan-applications"): "bank-partner-standard",
	("GET", "/v1/loan-applications/summary"): "bank-partner-standard",
	("GET", "/v1/loan-applications/metadata"): "bank-partner-standard",
	("GET", "/v1/loan-applications/basic-profile"): "crm-internal",
	("PATCH", "/v1/loan-applications/basic-profile"): "crm-internal",
	("GET", "/v1/loan-applications/{id}/full-profile"): "bank-partner-standard",
	("GET", "/v1/loan-applications/{id}/basic-profile"): "crm-internal",
	("PATCH", "/v1/loan-applications/{id}/basic-profile"): "crm-internal",
	("PATCH", "/v1/loan-applications/{id}/status"): "bank-partner-standard",
	("PATCH", "/v1/loan-applications/{id}/step"): "crm-internal",
	("PATCH", "/v1/loan-applications/{id}/officer"): "crm-internal",
	("GET", "/v1/loan-applications/{id}/documents"): "crm-internal",
	("POST", "/v1/loan-applications/{id}/documents"): "uploads",
	("GET", "/v1/loan-applications/{id}/documents/{docId}/content"): "crm-internal",
	("DELETE", "/v1/loan-applications/{id}/documents/{docId}"): "crm-internal",
	# Domain 08: Consent Management
	("GET", "/v1/consent/farmers"): "bank-partner-standard",
	("GET", "/v1/consent/reasons"): "bank-partner-standard",
	("GET", "/v1/consent/allowed-fields"): "bank-partner-standard",
	("GET", "/v1/consent/partners/me/allowed-field-ids"): "bank-partner-standard",
	("POST", "/v1/consent/otp"): "bank-partner-standard",
	("POST", "/v1/consent/otp/verify"): "bank-partner-standard",
	("POST", "/v1/consent/requests"): "bank-partner-standard",
	("POST", "/v1/webhooks/consent-data"): "webhooks-inbound",
	# Domain 09: Notifications
	("GET", "/v1/notifications"): "authenticated-core",
	("PATCH", "/v1/notifications"): "authenticated-core",
	("DELETE", "/v1/notifications"): "authenticated-core",
	("GET", "/v1/notifications/{notification_id}"): "authenticated-core",
	("PATCH", "/v1/notifications/{notification_id}"): "authenticated-core",
	("DELETE", "/v1/notifications/{notification_id}"): "authenticated-core",
	# Domain 10: Inbound Webhooks
	("POST", "/v1/webhooks/leads"): "webhooks-inbound",
	# Programme Dashboards (public)
	("GET", "/v1/charts/{chart_id}"): "public-dashboards",
}

# Spec security scheme -> auth label. Every other operation is bearer or public.
KEY_AUTH_SCHEMES = {"PartnerApiKeyAuth": "partner-key", "DashboardKeyAuth": "dashboard-key"}

# Key-auth consumers, prefixed with the app because consumer names are global in APISIX.
# Keys are secret references resolved by APISIX from its own
# environment, never literals in this repo. In Kubernetes each key comes from the
# Secret named after the consumer instead (see gateway_apisix.to_crd_consumer).
PARTNER_CONSUMERS = {"a2c-openg2p-consent-webhook": "A2C_OPENG2P_CONSENT_WEBHOOK_KEY"}
# The telco IVR vendor also calls grievance with the same key, so its consumer is shared
# (gateway_apisix.SHARED_CONSUMERS) and not created by this app's chart.
PARTNER_SHARED_CONSUMERS = [gw.IVR_VENDOR_CONSUMER]
# Each partner endpoint and the one consumer allowed to call it. The endpoints share a
# tier, so build_document puts them in one route; split_partner_routes splits it per
# consumer, so one partner's key cannot call another partner's endpoint.
PARTNER_ENDPOINTS = {
	"/v1/webhooks/consent-data": "a2c-openg2p-consent-webhook",
	"/v1/webhooks/leads": gw.IVR_VENDOR_CONSUMER,
}
DASHBOARD_CONSUMERS = {"a2c-oan-dashboards": "A2C_OAN_DASHBOARDS_KEY"}

# Partner routes reach Frappe as a dedicated, login-disabled service user: the gateway
# replaces whatever Authorization the partner sent with `token <api_key>:<api_secret>`.
# `set` overwrites unconditionally, so a partner cannot smuggle its own credential through.
FRAPPE_SERVICE_AUTHORIZATION = "$env://A2C_FRAPPE_SERVICE_AUTHORIZATION"

# Source addresses allowed on the partner routes, on top of key-auth. Development
# defaults (loopback and RFC1918); narrow to the partners' egress CIDRs before go-live.
WEBHOOK_ALLOWED_CIDRS = [
	"127.0.0.1/32",
	"::1/128",
	"10.0.0.0/8",
	"172.16.0.0/12",
	"192.168.0.0/16",
]

HELM_VALUES = {
	"OAN_A2C_UPSTREAM_HOST": ".Values.web.serviceName",
	"OAN_A2C_UPSTREAM_EXTERNAL_HOST": ".Values.web.externalHosts",
	"FRAPPE_SITE_NAME": ".Values.siteName",
	"WEB_PORT": ".Values.web.port",
}
HELM_REQUIRED = {"OAN_A2C_UPSTREAM_HOST", "FRAPPE_SITE_NAME", "APISIX_REDIS_HOST"}
# The third placeholder is the address of Frappe in another cluster, for a central gateway.
HELM_BACKENDS = {
	f"{APP}-service-v1": (
		"${OAN_A2C_UPSTREAM_HOST}",
		"${WEB_PORT}",
		"${OAN_A2C_UPSTREAM_EXTERNAL_HOST}",
	),
}


def spec_routes(spec: dict) -> list[dict]:
	routes = gw.spec_routes(spec)
	for r in routes:
		op = spec["paths"][r["path"]][r["method"].lower()]
		for requirement in op.get("security", spec.get("security", [])):
			for scheme in requirement:
				r["auth"] = KEY_AUTH_SCHEMES.get(scheme, r["auth"])
	return routes


def by_consumer(limit: dict) -> dict:
	"""A key-auth route knows its caller, so each consumer gets its own bucket."""
	limit = copy.deepcopy(limit)
	for rule in limit["rules"]:
		rule["key"] = rule["key"].replace("$remote_addr", "$consumer_name")
	return limit


def add_key_auth(service: dict) -> None:
	rewrite = service["plugins"]["proxy-rewrite"]
	for route in service["routes"]:
		auth = route["labels"]["auth"]
		if auth not in ("partner-key", "dashboard-key"):
			continue
		plugins = route["plugins"]
		plugins["limit-count"] = by_consumer(plugins["limit-count"])
		plugins["key-auth"] = {"header": "apikey", "hide_credentials": True}
		if auth == "partner-key":
			plugins["ip-restriction"] = {"whitelist": list(WEBHOOK_ALLOWED_CIDRS)}
			# A route-level proxy-rewrite replaces the service's, so repeat its uri rewrite.
			plugins["proxy-rewrite"] = {
				**copy.deepcopy(rewrite),
				"headers": {"set": {"Authorization": FRAPPE_SERVICE_AUTHORIZATION}},
			}
		else:
			# Frappe treats the dashboard routes as public: this key is the only check.
			plugins["consumer-restriction"] = {"whitelist": sorted(DASHBOARD_CONSUMERS)}


def split_partner_routes(service: dict) -> None:
	"""Replace each partner-key route with one route per consumer, holding only the
	endpoints in PARTNER_ENDPOINTS for that consumer. An unlisted endpoint stops the
	generator rather than being left open to every partner."""
	routes = []
	for route in service["routes"]:
		if route["labels"]["auth"] != "partner-key":
			routes.append(route)
			continue
		by_consumer_uris: dict[str, list[str]] = {}
		for uri in route["uris"]:
			if uri not in PARTNER_ENDPOINTS:
				raise gw.GatewayConfigError(f"Partner endpoint {uri} has no consumer in PARTNER_ENDPOINTS.")
			by_consumer_uris.setdefault(PARTNER_ENDPOINTS[uri], []).append(uri)
		for consumer, uris in sorted(by_consumer_uris.items()):
			split = copy.deepcopy(route)
			split["name"] = f"{route['name']}-{consumer.removeprefix('a2c-')}"
			split["uris"] = uris
			split["plugins"]["consumer-restriction"] = {"whitelist": [consumer]}
			routes.append(split)
	service["routes"] = routes


def static_files_route(tier: dict) -> dict:
	"""Public files (/files/*) are not API operations, so they are not in the spec. Not
	/private/files, which Frappe serves only after its permission check. No auth: a
	browser sends no Authorization on <img> requests. The keys are opaque and immutable,
	so responses may be cached indefinitely."""
	return {
		"name": f"{APP}-static-public-files",
		"description": tier["note"],
		"labels": {"app": APP, "tier": "static-assets", "auth": "public"},
		"uris": ["/files/*"],
		"methods": ["GET", "HEAD"],
		"plugins": {
			"limit-count": gw.limit_count(tier, GATEWAY_JWT, "public"),
			"response-rewrite": {
				"headers": {"set": {"Cache-Control": "public, max-age=31536000, immutable"}}
			},
		},
	}


def consumers() -> list[dict]:
	keys = {**PARTNER_CONSUMERS, **DASHBOARD_CONSUMERS}
	return [
		*(
			{
				"username": name,
				"labels": {"app": APP},
				"plugins": {"key-auth": {"key": f"$env://{env_name}"}},
			}
			for name, env_name in sorted(keys.items())
		),
		*(gw.shared_consumer(name, APP) for name in PARTNER_SHARED_CONSUMERS),
	]


def main():
	spec = gw.load_spec(SPEC_PATH)
	routes = gw.reconcile(spec_routes(spec), TIER_OVERRIDES)
	doc = gw.build_document(
		routes,
		app=APP,
		tiers=TIERS,
		api_upstream=API_UPSTREAM,
		service_plugins=gw.service_plugins(
			methods="GET,POST,PATCH,PUT,DELETE,OPTIONS", credentials=True, max_body_mb=20
		),
		gateway_jwt=GATEWAY_JWT,
	)
	service = doc["services"][0]
	add_key_auth(service)
	split_partner_routes(service)
	service["routes"].append(static_files_route(TIERS["static-assets"]))
	doc["consumers"] = consumers()

	provenance = [
		f"Source Spec: {spec['info']['title']} v{spec['info']['version']}",
		"Generated by generate_apisix_config_from_spec.py -- do not hand-edit;",
		"change the OpenAPI spec or TIER_OVERRIDES and re-run.",
	]
	gw.write_document(doc, OUTPUT_PATH, ["A2C ADC config (adc lint|validate|diff|sync)", *provenance])
	gw.write_helm_templates(
		doc,
		K8S_DIR,
		["A2C APISIX Ingress Controller resources", *provenance],
		app=APP,
		backends=HELM_BACKENDS,
		values=HELM_VALUES,
		required=HELM_REQUIRED,
		ints={"WEB_PORT"},
	)
	gw.report(OUTPUT_PATH, routes, doc, spec)


if __name__ == "__main__":
	gw.cli(main)
