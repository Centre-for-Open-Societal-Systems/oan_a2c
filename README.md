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

# Must equal the `key` of the Kong issuer consumer (kong/kong.yml) and the token `iss` claim.
bench --site mysite.localhost set-config jwt_issuer "https://auth.a2c.openagrinet.org/"

# Refresh-token lifetimes in seconds. The frontend session cookies expect 1 day, or 30 days
# with "remember me"; oan_auth_service defaults to 30 and 90 days.
bench --site mysite.localhost set-config jwt_refresh_token_ttl 86400
bench --site mysite.localhost set-config jwt_refresh_token_ttl_remember_me 2592000

# Roles a caller may pick at POST /api/v1/auth/register without being an administrator.
bench --site mysite.localhost set-config jwt_self_registerable_roles '["A2C Bank Admin", "A2C Farmer"]' --parse

# Development Agents sign in only through the OpenG2P IAM service (SSO). oan_auth_service
# validates IAM-issued tokens by asking IAM, creates the local user on first sign-in, and
# sets its roles to the roles it holds on the IAM client named by iam_application. Give
# Development Agents the client role `A2C Development Agent` (role names are used as-is).
# iam_application must equal the frontend's IAM_APPLICATION. A local (password) account
# holding that role is refused on every API call.
bench --site mysite.localhost set-config iam_base_url "https://iam.example.org"
bench --site mysite.localhost set-config iam_application "<iam client id for a2c>"

# Frappe's Fernet key for data at rest (Password fieldtypes, 2FA secrets). Must
# stay stable for the life of the site: rotate it and every previously encrypted
# value becomes undecryptable. Not used for token signing.
bench --site mysite.localhost set-config encryption_key "your-encryption-key-here"

# HMAC key for consent receipt signatures.
bench --site mysite.localhost set-config secret_key "your-secret-key-here"
```

### OpenG2P Integration

Used by the consent engine to interface with the OpenG2P service for data sharing and verification:

```bash
bench --site mysite.localhost set-config openg2p_base_url "https://api.openg2p.example.com"
bench --site mysite.localhost set-config openg2p_db "openg2p_database_name"
bench --site mysite.localhost set-config openg2p_username "api_username"
bench --site mysite.localhost set-config openg2p_password "api_password"
```

_Note: In Dockerized environments (like `frappe_docker`), you may alternatively pass these via environment variables if your container startup scripts template the site configs._

## Running Tests

To run the automated test suite for this application:

```bash
bench --site mysite.localhost run-tests --app oan_a2c
```

## API Contract & Gateway

The A2C REST API is documented at three levels — a stakeholder-readable overview, a formal machine-readable contract, and the deployment/gateway setup:

- **[`A2C_API_Specification.md`](docs/A2C_API_Specification.md)** — human-readable API reference: all 94 endpoints across the 10 resource domains (Identity & Access, Bank Onboarding & Administration, Bank Cataloging, Catalog Discovery, Applications, CRM/Leads & Field Ops, Loan Underwriting, Consent Management, Notifications, Inbound Webhooks), with a method/endpoint/description/access table per domain. Also covers authentication, gateway configuration, and response-format/pagination/rate-limiting appendices. Best starting point for anyone evaluating the API who isn't loading it into a tool.
- **[`openapi/openapi_v1.public.yaml`](openapi/openapi_v1.public.yaml)** — the formal OpenAPI 3.0.3 contract: the same 94 endpoints with full request/response schemas and per-operation auth requirements. Load into Swagger UI, Postman, or a codegen tool.
- **[`kong/README_kong_onboarding.md`](kong/README_kong_onboarding.md)** — how the API is deployed and secured behind Kong: authentication, throttling tiers, and onboarding a new external integration.

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
