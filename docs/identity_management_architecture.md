# OAN Access to Credit (A2C) Identity Management Architecture

## Overview
This document outlines the architectural approach for integrating Keycloak as the Identity and Access Management (IAM) provider for the OpenAgriNet (OAN) Frappe application, using a strictly stateless JWT (Bearer token) architecture to support both Mobile and Web headless clients.

The primary goals of this architecture are:
1. **Stateless Scalability:** Frappe remains completely stateless. No cookies or server-side sessions are used; all authentication is managed via per-request JWT validation.
2. **Centralized Identity:** Keycloak acts as the Single Source of Truth (SSoT) for user identities, credentials, and roles.
3. **Zero Downtime Migration:** A "Dual-Mode" JWT validation strategy ensures that legacy systems and test scripts using Frappe's native HS256 tokens do not break during the migration.

---

## 1. Authentication Flow (Headless OIDC)

Unlike traditional server-side applications, the Mobile and Web frontends interact directly with Keycloak.

**Workflow:**
1. The Mobile or Web application utilizes standard OIDC flows (e.g., AppAuth, PKCE) to authenticate the user against the Keycloak authorization server.
2. Keycloak issues an `access_token` (an RS256-signed JWT) to the client application.
3. For every subsequent API request to Frappe, the client attaches this token in the header: `Authorization: Bearer <TOKEN>`.

**Note:** A2C acts purely as an OAuth2 **Resource Server** — it validates tokens but never initiates the OAuth2 flow. The frontend applications are the OAuth2 **Clients**. Frappe's built-in Social Login Key feature is not used because it requires browser-based redirects and cookie sessions, which contradict the headless/stateless architecture.

---

## 2. Dual-Mode JWT Validation 

Frappe's legacy architecture generates and validates symmetric `HS256` tokens. Keycloak issues asymmetric `RS256` tokens signed via a JSON Web Key Set (JWKS). 

To ensure backward compatibility, the Frappe authentication middleware (`oan_a2c/api/middleware.py`) uses a **Dual-Mode Gateway**:

1. **Header Inspection:** Upon receiving an API request, the middleware intercepts the header and inspects the unverified JWT algorithm (`alg`).
2. **Native Mode (Fallback):** If `alg == 'HS256'`, the middleware decodes the token using the system's local `encryption_key`.
3. **Keycloak Mode:** If `alg == 'RS256'`, the middleware fetches the Keycloak public keys from the configured JWKS endpoint (`<KEYCLOAK_URL>/realms/<REALM>/protocol/openid-connect/certs`).
4. **Caching:** The JWKS public keys are cached in-memory using `PyJWKClient` with a configurable TTL (default: 300 seconds) to prevent network latency on every request.
5. **Algorithm Security:** The `alg` from the unverified header is never passed to `jwt.decode()`. Each validation path hardcodes its expected algorithm (`algorithms=["HS256"]` or `algorithms=["RS256"]`) to prevent algorithm confusion attacks. Tokens with `alg: none` are rejected.

**Validation Claims (RS256 / Keycloak tokens):**
- `exp` — Token expiration (always validated)
- `iss` — Issuer must match `<keycloak_url>/realms/<realm>` (prevents tokens from rogue servers)
- `aud` — Audience must match `keycloak_client_id` (prevents tokens intended for other OAN apps)

---

## 3. Just-In-Time (JIT) User Provisioning

When a valid Keycloak RS256 token reaches Frappe, the middleware ensures the user exists locally so that foreign key constraints (such as `owner` or `assigned_to` fields) function correctly.

**Workflow:**
1. The middleware extracts the `email` claim from the validated payload.
2. It performs a fast lookup in the `tabUser` table.
3. **Provisioning:** If the user does not exist, the middleware programmatically creates a new Frappe `User` document on the fly using the `given_name`, `family_name`, and `email` claims from the token.
4. The provisioned user has `user_type = "System User"` and receives no welcome email.
5. The `All` and `Guest` system roles are automatically assigned by Frappe's User creation logic.

**Note:** User Permissions (e.g., linking a Bank Agent to a Participating Bank) are NOT assigned during JIT provisioning. These remain a separate configuration step.

---

## 4. Role Synchronization & Mapping

Keycloak acts as the master authority for roles (e.g., `Development Agent`, `Bank Agent`). We synchronize these roles automatically upon every authenticated RS256 request.

1. **Token Claims:** Keycloak natively embeds assigned roles into the JWT payload under the `realm_access.roles` JSON block. The Frappe middleware reads this array directly.
2. **Dynamic Binding:** Upon successful token validation, the Frappe middleware cross-references the Keycloak roles against Frappe's `tabRole` table. Any Keycloak roles that do not exist in Frappe are safely ignored.
3. **Synchronization:** The middleware diffs the valid roles against Frappe's `Has Role` table for the corresponding user. It dynamically adds or revokes roles to ensure the Frappe database mirrors the Keycloak configuration in real-time.
4. **Protected System Roles:** The following Frappe system roles are **never** added or removed by the sync: `All`, `Guest`, `System Manager`, `Administrator`, `Script Manager`, `Desk User`.
5. **HS256 Tokens:** The legacy HS256 path does **not** trigger role sync. Roles for legacy users are managed entirely within Frappe.

**Keycloak Role Naming Requirement:** Role names in Keycloak must exactly match the role names defined in Frappe's `tabRole` table (e.g., `Bank Agent`, `Development Agent`). Case-sensitive, space-sensitive.

---

## 5. Configuration

The following keys must be present in `site_config.json` for Keycloak integration:

| Key | Required | Default | Description |
|-----|----------|---------|-------------|
| `keycloak_url` | Yes | — | Base URL of the Keycloak server as seen by **clients** (e.g., `http://localhost:8080`). Used for `iss` claim validation. |
| `keycloak_jwks_base_url` | No | (falls back to `keycloak_url`) | Base URL of the Keycloak server as reachable from the **Frappe container** (e.g., `http://host.docker.internal:8080`). Used for JWKS key fetching. Only needed in Docker environments where the container hostname differs from the client hostname. |
| `keycloak_realm` | Yes | — | Keycloak realm name (e.g., `oan`) |
| `keycloak_client_id` | Yes | — | Client ID for audience validation (e.g., `oan-a2c`) |
| `keycloak_jwks_cache_ttl` | No | `300` | JWKS key cache TTL in seconds |
| `encryption_key` | Yes | — | Local site encryption key (for legacy HS256 tokens) |

**Example (Docker environment):**
```json
{
  "keycloak_url": "http://localhost:8080",
  "keycloak_jwks_base_url": "http://host.docker.internal:8080",
  "keycloak_realm": "oan",
  "keycloak_client_id": "oan-a2c",
  "keycloak_jwks_cache_ttl": 300,
  "encryption_key": "..."
}
```

### 5.2 Keycloak Server Configuration Guide

To configure Keycloak to work seamlessly with the A2C Frappe backend, follow these settings on your Keycloak Admin console:

#### A. Realm Configuration
1. Create a new Realm named **`oan`** (or matches `keycloak_realm` in site config).

#### B. Client Configuration (`oan-a2c`)
1. Navigate to **Clients** → **Create Client**.
2. Set **Client ID** to **`oan-a2c`**.
3. Under **Capability config**:
   * **Client Authentication**: `Off` (enables public client PKCE flow for headless mobile and web apps).
   * **Authorization**: `Off`.
   * **Authentication flow**: Check both **Standard Flow** (Authorization Code) and **Direct Access Grants** (Password Grant).
4. Under **Access settings**:
   * **Valid redirect URIs**: Add your frontend URL (e.g., `http://localhost:3000/*`) and Postman's redirect URL: `https://oauth.pstmn.io/v1/browser-callback`.
   * **Web Origins**: Add `*` or specific frontend origins to allow CORS preflight requests.
5. Click **Save**.

#### C. Configure Audience Mapper (Required for `aud` Claim Validation)
By default, Keycloak does not add the client ID to the access token's audience claim. You must add a protocol mapper:
1. Navigate to **Clients** → **`oan-a2c`** → **Client scopes** tab.
2. Click on the dedicated scope named **`oan-a2c-dedicated`** (Type: `Dedicated`).
3. Click the **Mappers** tab → click **Configure a new mapper** (or **Add mapper** → **By configuration**).
4. Select **Audience** from the list.
5. Set the values:
   * **Name**: `audience-mapper`
   * **Included Client Audience**: Choose **`oan-a2c`** from the dropdown.
   * **Add to ID token**: `On`
   * **Add to access token**: `On` (Crucial)
6. Click **Save**.

#### D. User Roles Definition
The Frappe role sync middleware matches token roles directly against database roles:
1. Navigate to **Realm Roles** → **Create Role**.
2. Create roles that match your Frappe Role names **exactly** (case-sensitive and space-sensitive), such as:
   * **`Bank Agent`**
   * **`Development Agent`**
3. Map these roles to your users in the **Users** → **Role Mapping** tab. Keycloak will inject these into the `realm_access.roles` claim of the issued JWT, which the middleware automatically synchronizes.

---

## 6. API Endpoints

### 6.1 User Profile Introspection (`whoami`)

Frontends that authenticate via Keycloak need a way to fetch A2C-specific user metadata (full name, roles, linked bank) since they never call the legacy `auth.login` endpoint.

- **Endpoint:** `GET /api/method/oan_a2c.api.auth.whoami`
- **Authentication Required:** Yes (JWT Bearer Token — either HS256 or RS256)
- **Success Response (HTTP 200):**
  ```json
  {
    "message": {
      "status": "success",
      "user": {
        "email": "agent@coopbank.com",
        "full_name": "Abebe Bikila",
        "roles": ["Bank Agent", "All", "Guest"],
        "bank": "Cooperative Bank of Oromia"
      }
    }
  }
  ```

---

## 7. Postman Verification & Testing Guide

The Postman Collection ([postman_collection.json](file:///workspace/development/frappe-bench/apps/oan_a2c/postman/postman_collection.json)) has been updated with Keycloak configurations. You can test the end-to-end integration using two distinct methods:

### 7.1 Keycloak Client Redirect Configuration
To test interactive login flows, Keycloak must be configured to allow Postman's redirect URL:
1. Log in to the Keycloak Admin Console (`http://localhost:8080/admin/`).
2. Go to **Clients** → **`oan-a2c`**.
3. Under **Access settings**, add the following URL to **Valid redirect URIs**:
   `https://oauth.pstmn.io/v1/browser-callback`
4. Click **Save**.

### 7.2 Method A: Keycloak Login (Password Grant)
This is an automated, non-interactive request.
1. Open the Postman request **`Authentication & IAM`** → **`4. Keycloak Login (Password Grant)`**.
2. Click **Send**.
3. This sends an urlencoded POST request to Keycloak's token endpoint using the collection variables (`keycloak_username`, `keycloak_password`, etc.).
4. A Postman Test script automatically runs on success, saving the returned `access_token` into the collection variable `jwt_token`.
5. You can now immediately run any of the Business APIs (like `Get Leads`), which will use this token automatically.

### 7.3 Method B: Keycloak Login (Authorization Code)
This method brings up the actual Keycloak login page inside your system web browser.
1. Open the Postman request **`Authentication & IAM`** → **`5. Keycloak Login (Authorization Code)`**.
2. Go to the **Authorization** tab of the request.
3. Scroll down and click **Get New Access Token**.
4. Postman will open your web browser showing the Keycloak login screen.
5. Log in with your Keycloak credentials.
6. Once returned to Postman, click **Proceed** → **Use Token**.
7. Click **Send** to hit the `/whoami` endpoint using the newly acquired session token.

### 7.4 Requesting User Profile (Whoami)
The request **`Authentication & IAM`** → **`6. Get Profile (Whoami)`** is preconfigured with:
- `Host` header set to `{{host_header}}` (defaults to `development.localhost` for local Docker routing).
- `Authorization` header set to `Bearer {{jwt_token}}` to decode and test user JIT and role mapping.

