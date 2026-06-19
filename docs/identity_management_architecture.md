# OAN Access to Credit (A2C) Identity Management Architecture

## Overview
This document outlines the architectural approach for integrating Keycloak as the Identity and Access Management (IAM) provider for the OpenAgriNet (OAN) Frappe application, using a strictly stateless JWT (Bearer token) architecture to support both Mobile and Web headess clients.

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

---

## 2. Dual-Mode JWT Validation 

Frappe's legacy architecture generates and validates symmetric `HS256` tokens. Keycloak issues asymmetric `RS256` tokens signed via a JSON Web Key Set (JWKS). 

To ensure backward compatibility, the Frappe authentication middleware (`oan_a2c/api/middleware.py`) uses a **Dual-Mode Gateway**:

1. **Header Inspection:** Upon receiving an API request, the middleware intercepts the header and inspects the unverified JWT algorithm (`alg`).
2. **Native Mode (Fallback):** If `alg == 'HS256'`, the middleware decodes the token using the system's local `encryption_key`.
3. **Keycloak Mode:** If `alg == 'RS256'`, the middleware fetches the Keycloak public keys from the configured JWKS endpoint (`http://<KEYCLOAK_URL>/realms/<REALM>/protocol/openid-connect/certs`).
4. **Caching:** The JWKS public keys are cached in-memory using `PyJWKClient` with a Time-To-Live (TTL) to prevent network latency on every request.

---

## 3. Just-In-Time (JIT) User Provisioning

When a valid Keycloak RS256 token reaches Frappe, the middleware ensures the user exists locally so that foreign key constraints (such as `owner` or `assigned_to` fields) function correctly.

**Workflow:**
1. The middleware extracts the `email` (or `sub`) claim from the validated payload.
2. It performs a fast lookup in the `tabUser` table.
3. **Provisioning:** If the user does not exist, the middleware programmatically creates a new Frappe `User` document on the fly using the `given_name`, `family_name`, and `email` claims from the token.

---

## 4. Role Synchronization & Mapping

Keycloak acts as the master authority for roles (e.g., `Development Agent`, `Bank Agent`). We synchronize these roles automatically upon every authenticated request.

1. **Token Claims:** Keycloak natively embeds assigned roles into the JWT payload under the `realm_access.roles` JSON block. The Frappe middleware reads this array directly.
2. **Dynamic Binding (Option A):** Upon successful token validation, the Frappe middleware cross-references the Keycloak roles against Frappe's `tabRole` table. Any Keycloak roles that do not exist in Frappe are safely ignored.
3. **Synchronization:** The middleware diffs the valid roles against Frappe's `Has Role` table for the corresponding user. It dynamically adds or revokes roles to ensure the Frappe database perfectly mirrors the Keycloak configuration in real-time, while preserving native system roles like "All" and "Guest".
