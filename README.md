# OpenAgriNet - Access to Credit (A2C) Marketplace

Backend implementation for the Access to Credit (A2C) Application, providing a centralized marketplace for farmers to access loan products and manage consent-driven credit information sharing.

## Overview

This Frappe application acts as the backend for the A2C marketplace. It includes:

- **Loan Product Management**: Banks can define and manage loan products (interest rates, limits, statuses).
- **Lead Generation & Assignment**: Processing applications, capturing credit data, and assigning them to bank agents.
- **Consent Engine**: Interfaces with OpenG2P for managing farmer consent for personal data sharing.
- **Notification System**: Robust alert mechanisms for bank administrators and agents.
- **API Endpoints**: Full API suite for frontend interaction, including webhooks and JWT-based authentication.

## Installation

To install this application on your Frappe bench, run the following commands:

```bash
# Get the application
bench get-app https://github.com/faizmagic/oan_a2c.git

# Install it on your site (replace mysite.localhost with your site name)
bench --site mysite.localhost install-app oan_a2c
```

## Configuration

This application requires specific keys to be configured in your site's `site_config.json`. You can set these using the `bench set-config` command.

### JWT & Encryption Settings

Used for securing authentication and session tokens:

```bash
# Auth is served by oan_auth_service (install it first: it is a required app).
# Access tokens are signed RS256. Generate a key with:
#   openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:3072 -out sites/mysite.localhost/keys/jwt_v1.pem
# jwt_private_keys maps key id ("kid") -> PEM path (or PEM text); jwt_current_kid is the
# id new tokens are signed with. Rotate by adding a new id, pointing jwt_current_kid at
# it, then dropping the old id once the longest-lived access token has expired.
bench --site mysite.localhost set-config jwt_private_keys '{"v1": "keys/jwt_v1.pem"}' --parse
bench --site mysite.localhost set-config jwt_current_kid "v1"

# Must equal the token `iss` claim, and OAN_JWT_ISSUER of the gateway's issuer consumer when
# APISIX verifies tokens itself (GATEWAY_JWT in apisix/generate_apisix_config_from_spec.py).
bench --site mysite.localhost set-config jwt_issuer "https://auth.a2c.openagrinet.org/"

# Refresh-token lifetimes in seconds. The frontend session cookies expect 1 day, or 30 days
# with "remember me"; oan_auth_service defaults to 30 and 90 days.
bench --site mysite.localhost set-config jwt_refresh_token_ttl 86400
bench --site mysite.localhost set-config jwt_refresh_token_ttl_remember_me 2592000

# Roles a caller may pick at POST /api/v1/auth/register without being an administrator.
bench --site mysite.localhost set-config jwt_self_registerable_roles '["A2C Bank Admin", "A2C Farmer"]' --parse

# Development Agents sign in only through the OpenG2P IAM service (SSO). IAM logins are
# not site config: each is one `OAN IAM Service` row (base URL, provider id, issuer,
# application). On Kubernetes the chart writes them from `iamServices`
# (helm/oan-a2c-service/values.yaml); locally a System Manager adds them in the desk.
# See "Delegated login" in oan_auth_service's README. Give Development Agents the client role
# `A2C Development Agent` on the row's `application` (role names are used as-is). A local
# (password) account holding that role is refused on every API call.

# Frappe's Fernet key for data at rest (Password fieldtypes, 2FA secrets). Must
# stay stable for the life of the site: rotate it and every previously encrypted
# value becomes undecryptable. Not used for token signing.
bench --site mysite.localhost set-config encryption_key "your-encryption-key-here"

# HMAC key for consent receipt signatures.
bench --site mysite.localhost set-config secret_key "your-secret-key-here"
```

### OpenG2P Integration

The consent flow (`api/v1/consent/`) calls three OpenG2P services:

| Step                                        | Service                                                                                       | Auth                                                  |
| ------------------------------------------- | --------------------------------------------------------------------------------------------- | ----------------------------------------------------- |
| `search_farmer`                             | Registry Agent API `POST /agent_portal/vc/lookup_beneficiary`                                 | the caller's own agent-realm bearer token (forwarded) |
| `request_otp` / `resend_otp` / `verify_otp` | Consent Manager `/consent/v1/partner-portal/consent-requests` (`method: otp`)                 | partner-realm token                                   |
| `submit_consent`                            | Aggregation Layer `POST /dci/registry/sync/search` (`beneficiary360`, `authorize.consent_id`) | partner-realm token                                   |

Configure it in Desk under **A2C OpenG2P Settings** (single doctype, System Manager / A2C Administrator):
endpoint URLs, the partner Keycloak client (`client_credentials`, or `password` for the dev client),
`partner_id` (must match the token's `partner_id` claim), the consent purpose, data controller and data scopes.
Secrets are `Password` fields, encrypted with the site's `encryption_key`.
On Kubernetes the chart writes these fields on every deploy: the URLs and `partner_id` from `openg2pSettings`,
the partner credentials from the Secret `oan-a2c-openg2p-partner` (see helm/oan-a2c-service/values.yaml).

The old `openg2p_base_url` / `openg2p_db` / `openg2p_username` / `openg2p_password` site_config keys are no longer read.

## Running Tests

To run the automated test suite for this application:

```bash
bench --site mysite.localhost run-tests --app oan_a2c
```

## API Contract & Gateway

The A2C REST API is documented at three levels — a stakeholder-readable overview, a formal machine-readable contract, and the deployment/gateway setup:

- **[`A2C_API_Specification.md`](docs/A2C_API_Specification.md)** — human-readable API reference: all 101 endpoints across the 10 resource domains (Identity & Access, Bank Onboarding & Administration, Bank Cataloging, Catalog Discovery, Applications, CRM/Leads & Field Ops, Loan Underwriting, Consent Management, Notifications, Inbound Webhooks), with a method/endpoint/description/access table per domain. Also covers authentication, gateway configuration, and response-format/pagination/rate-limiting appendices. Best starting point for anyone evaluating the API who isn't loading it into a tool.
- **[`openapi/openapi_v1.public.yaml`](openapi/openapi_v1.public.yaml)** — the formal OpenAPI 3.0.3 contract: the same 101 endpoints with full request/response schemas and per-operation auth requirements. Load into Swagger UI, Postman, or a codegen tool.
- **[`apisix/README.md`](apisix/README.md)** — how the API is deployed and secured behind the APISIX gateway: authentication, throttling tiers, and partner consumers.

## Contributing

Install the pre-commit hooks before your first commit — CI runs the same checks on every pull
request:

```bash
cd apps/oan_a2c && pre-commit install
```

`docs/merge-hygiene.md` covers the conventions that keep `patches.txt`, the test suite and the
API docs mergeable when several branches are in flight. Read it before adding a patch entry, a
test class, or an endpoint section.

## License

Please see `license.txt` for license information.
