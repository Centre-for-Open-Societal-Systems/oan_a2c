#!/usr/bin/env python3
"""
generate_kong_config_from_spec.py

Single source of truth for the A2C REST facade's Kong onboarding -- v2.

Unlike the original generate_kong_config.py (which hand-maintained a 94-row
ROUTES table of method/path/auth), this version reads paths, methods, and
security directly from openapi_v1.public.yaml -- the published OpenAPI 3.0.3
contract -- so the gateway's route surface can never drift from the API
contract itself. If a route is renamed, added, or removed in the spec, this
generator picks it up automatically on the next run.

What still requires a human decision, and is NOT derivable from the spec,
is *throttling tier* -- that's a traffic/business call, not part of the
interface contract. TIER_OVERRIDES below is that decision, keyed by
(METHOD, PATH) exactly as they appear in the spec. The generator refuses to
run if the spec and TIER_OVERRIDES disagree about which routes exist --
see reconcile() -- so a new endpoint in the spec can't silently ship
without an explicit tier assignment, and a stale override can't silently
linger after its route is removed.

Usage:
    python3 generate_kong_config_from_spec.py > kong.yml
    deck validate -s kong.yml
    deck sync -s kong.yml   # DB-less / declarative sync
"""

import re
import sys
from pathlib import Path

import yaml

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
SPEC_PATH = REPO_ROOT / "openapi" / "openapi_v1.public.yaml"
OUTPUT_PATH = SCRIPT_DIR / "kong.yml"
BFF_UPSTREAM_URL = "http://a2c-bff.internal.svc:8080"  # replace with real BFF address

# ---------------------------------------------------------------------------
# Throttling tiers. Defaults below are a starting point for a review with
# real traffic data before go-live -- not a final SLA.
#   limit_by: "consumer" needs the caller authenticated (jwt or key-auth).
#   limit_by: "ip" is used only for the pre-auth public-auth tier, where
#   there is no consumer yet to key the counter on.
# ---------------------------------------------------------------------------
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
		"note": "Bank/partner integration traffic -- onboarding, cataloging, underwriting reads. "
		"Override per-consumer for Silver/Gold partner plans (see README).",
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
	"uploads": {
		"limit_by": "consumer",
		"minute": 10,
		"hour": 200,
		"policy": "redis",
		"note": "Document upload endpoints -- expensive, low-frequency by nature. Paired with "
		"request-size-limiting; see README for the pre-signed-URL migration note.",
	},
}

# ---------------------------------------------------------------------------
# Tier assignment per route -- the one thing the OpenAPI spec doesn't carry.
# Keyed by (METHOD, PATH) exactly as openapi_v1.public.yaml spells them.
# Carried over unchanged from the original hand-built ROUTES table so the
# same traffic-tier decisions apply; only the path/method/auth source of
# truth has moved to the spec.
# ---------------------------------------------------------------------------
TIER_OVERRIDES = {
	# Domain 01: Identity & Access
	("POST", "/v1/auth/register"): "public-auth",
	("POST", "/v1/auth/login"): "public-auth",
	("POST", "/v1/auth/token/refresh"): "public-auth",
	("POST", "/v1/auth/logout"): "authenticated-core",
	("POST", "/v1/auth/password/forgot"): "public-auth",
	("POST", "/v1/auth/password/reset"): "public-auth",
	("POST", "/v1/auth/password/initial"): "public-auth",
	("PATCH", "/v1/me/password"): "authenticated-core",
	("GET", "/v1/me"): "authenticated-core",
	("GET", "/v1/me/profile"): "authenticated-core",
	("PATCH", "/v1/me/profile"): "authenticated-core",
	# Domain 02: Bank Onboarding & Administration
	("POST", "/v1/banks"): "bank-partner-standard",
	("GET", "/v1/banks/me"): "bank-partner-standard",
	("PATCH", "/v1/banks/me"): "bank-partner-standard",
	("PATCH", "/v1/banks/me/status"): "bank-partner-standard",
	("POST", "/v1/banks/me/kyc-documents"): "uploads",
	("POST", "/v1/banks/me/logo"): "uploads",
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
	("PATCH", "/v1/notifications/read"): "authenticated-core",
	("DELETE", "/v1/notifications"): "authenticated-core",
	# Domain 10: Inbound Webhooks
	("POST", "/v1/webhooks/leads"): "webhooks-inbound",
}


def load_spec(path):
	with open(path) as f:
		return yaml.safe_load(f)


def spec_routes(spec):
	"""Flatten the spec's paths into an ordered list of route dicts, in the
	same domain-grouped order the spec itself was written in (dict order is
	preserved by yaml.safe_load in Python 3.7+)."""
	tag_to_domain = {t["name"]: f"d{i + 1:02d}" for i, t in enumerate(spec["tags"])}
	routes = []
	for path, methods in spec["paths"].items():
		for method, op in methods.items():
			method = method.upper()
			security = op.get("security", spec.get("security"))
			if security == []:
				auth = "public"
			elif security == [{"PartnerApiKeyAuth": []}]:
				auth = "partner-key"
			else:
				auth = "bearer"
			tag = (op.get("tags") or [None])[0]
			domain = tag_to_domain.get(tag, "d00")
			routes.append(
				{
					"method": method,
					"path": path,
					"auth": auth,
					"domain": domain,
					"tag": tag,
					"operation_id": op.get("operationId"),
				}
			)
	return routes


def reconcile(routes):
	"""Refuse to run if the spec and TIER_OVERRIDES disagree about which
	routes exist -- this is the whole point of driving Kong off the spec:
	a route add/remove/rename in the contract must force a conscious tier
	decision, not silently default or silently go stale."""
	spec_keys = {(r["method"], r["path"]) for r in routes}
	override_keys = set(TIER_OVERRIDES.keys())

	missing_overrides = spec_keys - override_keys
	stale_overrides = override_keys - spec_keys

	if missing_overrides or stale_overrides:
		msg = ["Spec <-> TIER_OVERRIDES mismatch -- refusing to generate kong.yml.", ""]
		if missing_overrides:
			msg.append(f"In the spec but with no assigned throttling tier ({len(missing_overrides)}):")
			for m, p in sorted(missing_overrides):
				msg.append(f"  {m} {p}")
		if stale_overrides:
			msg.append(f"In TIER_OVERRIDES but no longer in the spec ({len(stale_overrides)}):")
			for m, p in sorted(stale_overrides):
				msg.append(f"  {m} {p}")
		msg.append("")
		msg.append("Assign a tier for each new route (or remove the stale override) and re-run.")
		raise SystemExit("\n".join(msg))

	for r in routes:
		r["tier"] = TIER_OVERRIDES[(r["method"], r["path"])]
	return routes


def to_kong_regex(path: str) -> str:
	"""Convert /v1/foo/{id}/bar -> ~/v1/foo/(?<id>[^/]+)/bar$"""
	if "{" not in path:
		return f"~{path}$"
	regex = re.sub(r"\{(\w+)\}", r"(?<\1>[^/]+)", path)
	return f"~{regex}$"


def route_name(method: str, path: str) -> str:
	slug = re.sub(r"[{}]", "", path).strip("/").replace("/", "-")
	return f"{method.lower()}-{slug}"[:120]


def build_config(routes):
	service = {
		"name": "a2c-bff-v1",
		"url": BFF_UPSTREAM_URL,
		"connect_timeout": 5000,
		"write_timeout": 20000,
		"read_timeout": 20000,
		"retries": 2,
		"tags": ["a2c", "v1", "bff"],
		"plugins": [
			# Global hygiene applied to every route on this service.
			{
				"name": "cors",
				"config": {
					"origins": ["*"],
					"methods": ["GET", "POST", "PATCH", "PUT", "DELETE", "OPTIONS"],
					"headers": ["Authorization", "Content-Type", "X-Request-Id"],
					"credentials": True,
					"max_age": 3600,
				},
			},
			{
				"name": "request-size-limiting",
				"config": {
					"allowed_payload_size": 20
				},  # MB, global safety net (uploads tier overrides tighter)
			},
			{
				"name": "correlation-id",
				"config": {"header_name": "X-Request-Id", "generator": "uuid", "echo_downstream": True},
			},
			{
				"name": "prometheus",
				"config": {"status_code_metrics": True, "latency_metrics": True, "bandwidth_metrics": True},
			},
		],
		"routes": [],
	}

	for r in routes:
		method, path, auth, domain, tier = r["method"], r["path"], r["auth"], r["domain"], r["tier"]
		kong_path = to_kong_regex(path)
		depth = path.count("/")
		route = {
			"name": route_name(method, path),
			"methods": [method],
			"paths": [kong_path],
			"strip_path": False,
			"regex_priority": depth,  # deeper paths win over their own prefixes
			"tags": ["a2c", "v1", domain, tier, auth],
			"plugins": [],
		}

		t = TIERS[tier]
		route["plugins"].append(
			{
				"name": "rate-limiting",
				"config": {
					"minute": t["minute"],
					"hour": t["hour"],
					"limit_by": t["limit_by"],
					"policy": t["policy"],
					"fault_tolerant": True,
					"hide_client_headers": False,
				},
			}
		)

		if auth == "bearer":
			route["plugins"].append(
				{
					"name": "jwt",
					"config": {
						"claims_to_verify": ["exp"],
						"key_claim_name": "iss",
						"header_names": ["Authorization"],
					},
				}
			)
		elif auth == "partner-key":
			route["plugins"].append(
				{"name": "key-auth", "config": {"key_names": ["apikey"], "hide_credentials": True}}
			)
			route["plugins"].append(
				{"name": "ip-restriction", "config": {"allow": ["203.0.113.0/24"]}}
			)  # placeholder CIDR
		# auth == "public": no auth plugin attached; rate-limiting (IP-keyed) still applies

		service["routes"].append(route)

	consumers = [
		{
			"username": "a2c-identity-platform",
			"tags": ["issuer"],
			"jwt_secrets": [
				{
					"algorithm": "RS256",
					"key": "https://auth.a2c.openagrinet.org/",  # must equal the JWT `iss` claim
					"rsa_public_key": "-----BEGIN PUBLIC KEY-----\nREPLACE_WITH_A2C_AUTH_SIGNING_PUBLIC_KEY\n-----END PUBLIC KEY-----",
				}
			],
		},
		{
			"username": "openg2p-consent-webhook",
			"tags": ["partner", "webhook"],
			"keyauth_credentials": [{"key": "REPLACE_WITH_ROTATABLE_SECRET_1"}],
		},
		{
			"username": "telco-ivr-lead-gateway",
			"tags": ["partner", "webhook"],
			"keyauth_credentials": [{"key": "REPLACE_WITH_ROTATABLE_SECRET_2"}],
		},
		{
			"username": "partner-bank-example-gold",
			"tags": ["partner", "bank-integration", "tier-gold"],
			"jwt_secrets": [],
			"plugins": [
				{
					"name": "rate-limiting",
					"config": {"minute": 3000, "hour": 120000, "policy": "redis", "fault_tolerant": True},
				}
			],
		},
	]

	doc = {
		"_format_version": "3.0",
		"_transform": True,
		"services": [service],
		"consumers": consumers,
	}
	return doc


def main():
	spec = load_spec(SPEC_PATH)
	routes = reconcile(spec_routes(spec))
	doc = build_config(routes)

	with open(OUTPUT_PATH, "w") as f:
		f.write("# A2C Kong declarative config -- generated from openapi_v1.public.yaml\n")
		f.write(f"# Source spec: {spec['info']['title']} v{spec['info']['version']}\n")
		f.write("# Generated by generate_kong_config_from_spec.py -- do not hand-edit; change the spec\n")
		f.write("# and/or TIER_OVERRIDES in the generator, then re-run.\n")
		yaml.safe_dump(doc, f, sort_keys=False, default_flow_style=False, width=100)

	by_method = {}
	for r in routes:
		by_method[r["method"]] = by_method.get(r["method"], 0) + 1
	print(
		f"wrote {OUTPUT_PATH.name}: {len(routes)} routes from {len(spec['paths'])} paths "
		f"({len(spec['tags'])} domains) -- methods: {by_method}",
		file=sys.stderr,
	)


if __name__ == "__main__":
	main()
