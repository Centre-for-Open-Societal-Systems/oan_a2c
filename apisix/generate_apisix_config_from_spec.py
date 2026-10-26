#!/usr/bin/env python3
"""
generate_apisix_config_from_spec.py

Generates the ADC gateway configuration (adc.yaml) and the APISIX Ingress Controller
resources (k8s/templates, the oan-a2c-gateway Helm chart) for the A2C REST API directly
from openapi_v1.public.yaml. Shared logic lives in oan_auth_service/gateway_apisix.py.

The throttling tiers and the `(method, path) -> tier` table are read from the Kong
generator, which stays the single place to change them until Kong is retired.

Usage:
    python3 apisix/generate_apisix_config_from_spec.py
"""

import copy
import importlib.util
from pathlib import Path

from oan_auth_service import gateway_apisix as gw

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
SPEC_PATH = REPO_ROOT / "openapi" / "openapi_v1.public.yaml"
KONG_GENERATOR = REPO_ROOT / "kong" / "generate_kong_config_from_spec.py"
OUTPUT_PATH = SCRIPT_DIR / "adc.yaml"
K8S_DIR = SCRIPT_DIR / "k8s" / "templates"
APP = "oan-a2c"
SITE_NAME = "${FRAPPE_SITE_NAME}"
# False: Frappe's JWT middleware verifies bearer tokens, as in the other OAN apps.
GATEWAY_JWT = False

API_UPSTREAM = gw.upstream(
	host_env="OAN_A2C_UPSTREAM_HOST",
	host_default="a2c-frontend.internal.svc",
	port_env="OAN_A2C_UPSTREAM_PORT",
	port_default=8080,
	# Frappe picks the site from the Host header, so send the site name, not the gateway's.
	pass_host="rewrite",
	upstream_host=SITE_NAME,
	timeout={"connect": 5, "send": 20, "read": 20},
	retries=2,
)
API_UPSTREAM["nodes"][0]["host"] = "${OAN_A2C_UPSTREAM_HOST}"

# Spec security scheme -> auth label. Every other operation is bearer or public.
KEY_AUTH_SCHEMES = {"PartnerApiKeyAuth": "partner-key", "DashboardKeyAuth": "dashboard-key"}

# Key-auth consumers, prefixed with the app because consumer names are global in APISIX.
# Keys are secret references resolved by APISIX from its own
# environment, never literals in this repo. In Kubernetes each key comes from the
# Secret named after the consumer instead (see gateway_apisix.to_crd_consumer).
PARTNER_CONSUMERS = {
	"a2c-openg2p-consent-webhook": "A2C_OPENG2P_CONSENT_WEBHOOK_KEY",
	"a2c-telco-ivr-lead-gateway": "A2C_TELCO_IVR_LEAD_GATEWAY_KEY",
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


def load_kong_generator():
	spec = importlib.util.spec_from_file_location("a2c_kong_generator", KONG_GENERATOR)
	module = importlib.util.module_from_spec(spec)
	spec.loader.exec_module(module)
	return module


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
			plugins["consumer-restriction"] = {"whitelist": sorted(PARTNER_CONSUMERS)}
			plugins["ip-restriction"] = {"whitelist": list(WEBHOOK_ALLOWED_CIDRS)}
			# A route-level proxy-rewrite replaces the service's, so repeat its uri rewrite.
			plugins["proxy-rewrite"] = {
				**copy.deepcopy(rewrite),
				"headers": {"set": {"Authorization": FRAPPE_SERVICE_AUTHORIZATION}},
			}
		else:
			# Frappe treats the dashboard routes as public: this key is the only check.
			plugins["consumer-restriction"] = {"whitelist": sorted(DASHBOARD_CONSUMERS)}


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
		{
			"username": name,
			"labels": {"app": APP},
			"plugins": {"key-auth": {"key": f"$env://{env_name}"}},
		}
		for name, env_name in sorted(keys.items())
	]


def main():
	kong = load_kong_generator()
	spec = gw.load_spec(SPEC_PATH)
	routes = gw.reconcile(spec_routes(spec), kong.TIER_OVERRIDES)
	doc = gw.build_document(
		routes,
		app=APP,
		tiers=kong.TIERS,
		api_upstream=API_UPSTREAM,
		service_plugins=gw.service_plugins(
			methods="GET,POST,PATCH,PUT,DELETE,OPTIONS", credentials=True, max_body_mb=20
		),
		gateway_jwt=GATEWAY_JWT,
	)
	service = doc["services"][0]
	add_key_auth(service)
	service["routes"].append(static_files_route(kong.TIERS["static-assets"]))
	doc["consumers"] = consumers()

	provenance = [
		f"Source Spec: {spec['info']['title']} v{spec['info']['version']}",
		"Generated by generate_apisix_config_from_spec.py -- do not hand-edit;",
		"change the OpenAPI spec or TIER_OVERRIDES in kong/ and re-run.",
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
	main()
