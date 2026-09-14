# A2C API Documentation

**OpenAgriNet · Access to Credit**

REST API Reference for Third-Party Integration

Version 1.0 (Draft for stakeholder review) · September 2026
Prepared by the A2C Architecture Team

**94 endpoints across 10 resource domains**

---

## 1. Overview

This document lists every REST endpoint the A2C (Access to Credit) platform exposes for third-party and internal integration — covering bank onboarding, loan product cataloging, the farmer-facing marketplace, loan underwriting, Fayda national ID consent capture, and platform notifications.

It is intended as a shared reference for engineering, integration partners, and business stakeholders evaluating what the platform can do. Endpoints are grouped into ten resource domains, each matching a distinct area of the product.

**Base URL**

```
https://api.a2c.openagrinet.org/v1
```

**Versioning**

All routes are prefixed with `/v1`. A breaking change to any endpoint's contract will be released under a new version prefix rather than modifying this one in place.

---

## 2. API Domains at a Glance

A summary of the ten domains this document covers, and how many endpoints each contains.

| #   | Domain                                 | Purpose                                                                                                                                                                                                                    | Endpoints |
| --- | -------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------- |
| 01  | **Identity & Access**                  | Registration, login, token lifecycle, password recovery, and the caller's own profile. Every other domain depends on this one — it's the first call any integration makes.                                                 | **11**    |
| 02  | **Bank Onboarding & Administration**   | Bank registration, KYC compliance, organizational profile, and team management. Every path here is scoped to the caller's own bank.                                                                                        | **12**    |
| 03  | **Bank Cataloging**                    | Loan product authoring, marketplace taxonomy, and each bank's custom loan-approval pipeline. Taxonomy reads are open to any signed-in caller; creating new taxonomy terms is a platform-governance action, not a bank one. | **18**    |
| 04  | **Catalog Discovery**                  | The farmer-facing browse experience — discovering loan products and banks across the whole marketplace, open to any signed-in user regardless of role.                                                                     | **7**     |
| 05  | **Applications (Farmer Self-Service)** | The farmer's own loan application draft, from creation through submission to a bank for review.                                                                                                                            | **5**     |
| 06  | **CRM · Leads & Field Ops**            | The lead-capture-to-qualification funnel run by Development Agents — from first contact through field visits to a qualified, bank-ready lead.                                                                              | **15**    |
| 07  | **Loan Underwriting**                  | A Development Agent converts a qualified lead into a formal loan application at a chosen bank; the bank then reviews and moves it through its own approval pipeline.                                                       | **14**    |
| 08  | **Consent Management**                 | Fayda national ID consent capture — locating the farmer, verifying identity by one-time code, and submitting a signed data-sharing consent — plus the inbound webhook that returns the registry's decision.                | **8**     |
| 09  | **Notifications**                      | In-app notifications for the signed-in user, shared across every role.                                                                                                                                                     | **3**     |
| 10  | **Inbound Webhooks**                   | Server-to-server receivers for external systems pushing events into A2C. Authenticated with a partner API key, not a user token.                                                                                           | **1**     |

---

## 3. Authentication

Two credential types cover the whole surface:

1. **Bearer Token** — a JSON Web Token issued at login, sent as `Authorization: Bearer <token>`. Used by every end-user and bank/partner call. Access tokens are short-lived; a refresh token renews them without requiring the user to log in again.
2. **Partner API Key** — used only by the two inbound webhook receivers (Consent Management and Inbound Webhooks domains), which are called by external systems (the national ID registry, telco gateways) rather than a signed-in person.

Endpoints marked “Public” in the Access column require no credential — this is intentionally limited to account creation and password recovery.

---

## 4. How to Read This Document

1. **Method** — the HTTP verb: GET (read), POST (create), PATCH (partial update), PUT (full replace), DELETE (remove).
2. **Endpoint** — the route path. Segments in curly braces, e.g. `{id}`, are placeholders for a real identifier.
3. **Access** — the credential required, and any role restriction beyond “signed in.”

---

## 5. Gateway Configuration (Kong)

All traffic to the A2C API passes through an API gateway (Kong) before reaching the platform. The gateway enforces four things on every request: identity verification, traffic throttling, routing to the correct backend, and request logging. It does not contain business logic — approval rules, bank-scoping, and data validation continue to live in the A2C platform itself, so this layer adds protection without duplicating the platform's own rules.

**Deployment Model**

The gateway configuration is declarative — the entire routing and policy setup is a single reviewable file, deployed the same way as any other infrastructure change: version-controlled and applied through CI, never edited by hand in production. This keeps a complete history of every change to how the API is exposed.

> _Note: the gateway vendor changed its licensing terms in 2025 — some distributions now require a paid license for full functionality. A2C is deployed on the fully open, license-free distribution, consistent with the platform's no-vendor-lock-in principle. This is a deliberate architectural choice, not a temporary workaround._

**Authentication at the Gateway**

The gateway recognizes two credential types:

| Credential             | Used By                                                                  | What the Gateway Checks                                                     |
| ---------------------- | ------------------------------------------------------------------------ | --------------------------------------------------------------------------- |
| **Bearer Token (JWT)** | All signed-in users — farmers, bank staff, development agents            | Signature and expiry only                                                   |
| **Partner API Key**    | The two inbound webhook receivers (Consent Management, Inbound Webhooks) | A valid, provisioned key, plus the caller's IP address against an allowlist |

Role-based permissions — which bank a user belongs to, which actions their role allows — are enforced by the A2C platform itself, not the gateway. This keeps a single, consistent source of truth for authorization rather than duplicating those rules in two places.

**Rate Limiting**

Every endpoint is assigned to one of seven traffic tiers, each with its own allowance. This protects the platform from being overwhelmed and gives integration partners predictable capacity to plan against. A caller that exceeds its limit receives an HTTP 429 response (see Appendix A) rather than the request being queued or delayed.

| Tier                           | Applies To                                                                             | Limit                        |
| ------------------------------ | -------------------------------------------------------------------------------------- | ---------------------------- |
| **Public / Authentication**    | Login, registration, password recovery                                                 | **5 requests/min · 30/hour** |
| **Core Account**               | Profile, notifications                                                                 | **120/min · 4,000/hour**     |
| **Farmer App**                 | Catalog browsing, applications, dashboard                                              | **90/min · 3,000/hour**      |
| **Bank & Partner Integration** | Onboarding, cataloging, underwriting, consent — higher tiers available by partner plan | **300/min · 15,000/hour**    |
| **Internal CRM**               | Lead & field-agent tools                                                               | **600/min · 30,000/hour**    |
| **Inbound Webhooks**           | Registry and telco receivers                                                           | **3,000/min · 120,000/hour** |
| **Document Uploads**           | KYC and supporting documents                                                           | **10/min · 200/hour**        |

**Additional Protections**

- Cross-origin access control (CORS) for browser-based integrations.
- A 20MB request size cap, tightened further on the document-upload endpoints.
- IP allowlisting on both webhook receivers.
- Full request logging with a correlation ID on every call, so a specific request can be traced end-to-end for support purposes.

**Operational Note**

Once the gateway is live, the platform's internal API paths will no longer be reachable directly — every external call must go through the gateway to receive these protections. This is planned as a phased rollout rather than a single cutover, with a monitoring window to confirm no integration still relies on the older direct path before it is closed.

---

## 01. Identity & Access

`/v1/auth · /v1/me`

Registration, login, token lifecycle, password recovery, and the caller's own profile. Every other domain depends on this one — it's the first call any integration makes.

| Method  | Endpoint                    | Description                                                                             | Access       |
| ------- | --------------------------- | --------------------------------------------------------------------------------------- | ------------ |
| `POST`  | `/v1/auth/register`         | Creates a new user account (defaults to the Bank Admin role for organization sign-ups). | Public       |
| `POST`  | `/v1/auth/login`            | Authenticates a user and issues an access token and refresh token.                      | Public       |
| `POST`  | `/v1/auth/token/refresh`    | Exchanges a valid refresh token for a new access token.                                 | Public       |
| `POST`  | `/v1/auth/logout`           | Revokes the caller's refresh token, ending the session.                                 | Bearer Token |
| `POST`  | `/v1/auth/password/forgot`  | Sends a one-time recovery code to the account's registered email.                       | Public       |
| `POST`  | `/v1/auth/password/reset`   | Completes password recovery using the code from the forgot-password step.               | Public       |
| `POST`  | `/v1/auth/password/initial` | Lets a newly invited team member replace their temporary password.                      | Public       |
| `PATCH` | `/v1/me/password`           | Changes the password of the currently signed-in user.                                   | Bearer Token |
| `GET`   | `/v1/me`                    | Returns the signed-in user's identity, roles, and bank affiliation.                     | Bearer Token |
| `GET`   | `/v1/me/profile`            | Returns the signed-in user's full profile details.                                      | Bearer Token |
| `PATCH` | `/v1/me/profile`            | Updates the signed-in user's name, phone, language, or avatar.                          | Bearer Token |

---

## 02. Bank Onboarding & Administration

`/v1/banks/me`

Bank registration, KYC compliance, organizational profile, and team management. Every path here is scoped to the caller's own bank.

| Method  | Endpoint                                    | Description                                                                   | Access                    |
| ------- | ------------------------------------------- | ----------------------------------------------------------------------------- | ------------------------- |
| `POST`  | `/v1/banks`                                 | Registers a new participating bank and links the caller as its administrator. | Bearer Token              |
| `GET`   | `/v1/banks/me`                              | Returns the caller's bank profile.                                            | Bearer Token              |
| `PATCH` | `/v1/banks/me`                              | Updates the bank's profile details.                                           | Bearer Token — Bank Admin |
| `PATCH` | `/v1/banks/me/status`                       | Updates the bank's onboarding status (e.g., In Review → Active).              | Bearer Token — Bank Admin |
| `POST`  | `/v1/banks/me/kyc-documents`                | Uploads the bank's regulatory KYC document.                                   | Bearer Token — Bank Admin |
| `POST`  | `/v1/banks/me/logo`                         | Uploads the bank's marketplace logo image.                                    | Bearer Token — Bank Admin |
| `PUT`   | `/v1/banks/me/contacts`                     | Sets the bank's Grievance Redressal Officer and Operations contact details.   | Bearer Token              |
| `GET`   | `/v1/banks/me/team`                         | Lists all team members (admins and agents) at the bank.                       | Bearer Token — Bank Admin |
| `POST`  | `/v1/banks/me/team`                         | Invites a new team member with a temporary password.                          | Bearer Token — Bank Admin |
| `PATCH` | `/v1/banks/me/team/{userId}`                | Updates a team member's name, role, or active status.                         | Bearer Token — Bank Admin |
| `POST`  | `/v1/banks/me/team/{userId}/password-reset` | Issues a new temporary password for a team member.                            | Bearer Token — Bank Admin |
| `GET`   | `/v1/banks/me/dashboard/stats`              | Returns summary metrics for the bank's products and loan pipeline.            | Bearer Token              |

---

## 03. Bank Cataloging

`/v1/banks/me/products · /v1/taxonomy`

Loan product authoring, marketplace taxonomy, and each bank's custom loan-approval pipeline. Taxonomy reads are open to any signed-in caller; creating new taxonomy terms is a platform-governance action, not a bank one.

| Method  | Endpoint                                | Description                                                    | Access                               |
| ------- | --------------------------------------- | -------------------------------------------------------------- | ------------------------------------ |
| `POST`  | `/v1/banks/me/products`                 | Creates one or more new loan products for the bank.            | Bearer Token                         |
| `GET`   | `/v1/banks/me/products`                 | Lists the bank's loan products with filtering and search.      | Bearer Token                         |
| `GET`   | `/v1/banks/me/products/{id}`            | Returns full detail for one loan product.                      | Bearer Token                         |
| `PATCH` | `/v1/banks/me/products/{id}`            | Updates a loan product's terms.                                | Bearer Token                         |
| `PATCH` | `/v1/banks/me/products/{id}/status`     | Transitions a product's status, e.g. publishing it to Active.  | Bearer Token — Bank Admin to publish |
| `GET`   | `/v1/banks/me/products/{id}/audit-log`  | Returns the status-change history for a product.               | Bearer Token                         |
| `PUT`   | `/v1/banks/me/products/{id}/categories` | Sets which marketplace categories a product belongs to.        | Bearer Token                         |
| `PUT`   | `/v1/banks/me/products/{id}/tags`       | Sets which marketplace tags apply to a product.                | Bearer Token                         |
| `PUT`   | `/v1/banks/me/products/{id}/attributes` | Sets product-specific attributes, e.g. eligible crop types.    | Bearer Token                         |
| `GET`   | `/v1/taxonomy/categories`               | Lists the platform's marketplace categories.                   | Bearer Token                         |
| `GET`   | `/v1/taxonomy/tags`                     | Lists the platform's marketplace tags.                         | Bearer Token                         |
| `GET`   | `/v1/taxonomy/attributes`               | Lists the platform's product attribute definitions.            | Bearer Token                         |
| `POST`  | `/v1/admin/taxonomy/categories`         | Creates a new marketplace category.                            | Bearer Token — Platform Admin        |
| `POST`  | `/v1/admin/taxonomy/tags`               | Creates a new marketplace tag.                                 | Bearer Token — Platform Admin        |
| `POST`  | `/v1/admin/taxonomy/attribute-terms`    | Creates a new product attribute term.                          | Bearer Token — Platform Admin        |
| `GET`   | `/v1/banks/me/pipeline-stages`          | Lists the bank's custom loan-approval pipeline stages.         | Bearer Token                         |
| `POST`  | `/v1/banks/me/pipeline-stages`          | Adds a new stage to the bank's loan-approval pipeline.         | Bearer Token — Bank Admin            |
| `PUT`   | `/v1/banks/me/pipeline-stages`          | Reorders or replaces the bank's entire pipeline configuration. | Bearer Token — Bank Admin            |

---

## 04. Catalog Discovery

`/v1/catalog · /v1/me/dashboard`

The farmer-facing browse experience — discovering loan products and banks across the whole marketplace, open to any signed-in user regardless of role.

| Method   | Endpoint                                 | Description                                                        | Access       |
| -------- | ---------------------------------------- | ------------------------------------------------------------------ | ------------ |
| `GET`    | `/v1/catalog/products`                   | Browses active loan products across every participating bank.      | Bearer Token |
| `GET`    | `/v1/catalog/banks/{bankId}`             | Returns the public storefront details for one bank.                | Bearer Token |
| `GET`    | `/v1/catalog/facets`                     | Returns the filter options shown in the product discovery sidebar. | Bearer Token |
| `GET`    | `/v1/catalog/saved-products`             | Lists the caller's bookmarked loan products.                       | Bearer Token |
| `PUT`    | `/v1/catalog/saved-products/{productId}` | Bookmarks a loan product.                                          | Bearer Token |
| `DELETE` | `/v1/catalog/saved-products/{productId}` | Removes a bookmarked loan product.                                 | Bearer Token |
| `GET`    | `/v1/me/dashboard`                       | Returns the farmer's personal dashboard summary.                   | Bearer Token |

---

## 05. Applications (Farmer Self-Service)

`/v1/applications`

The farmer's own loan application draft, from creation through submission to a bank for review.

| Method  | Endpoint                       | Description                                          | Access       |
| ------- | ------------------------------ | ---------------------------------------------------- | ------------ |
| `POST`  | `/v1/applications`             | Creates a new draft loan application for the farmer. | Bearer Token |
| `GET`   | `/v1/applications`             | Lists the farmer's own applications.                 | Bearer Token |
| `GET`   | `/v1/applications/{id}`        | Returns detail for one of the farmer's applications. | Bearer Token |
| `PATCH` | `/v1/applications/{id}`        | Updates a draft application before submission.       | Bearer Token |
| `POST`  | `/v1/applications/{id}/submit` | Submits a draft application to the bank for review.  | Bearer Token |

---

## 06. CRM · Leads & Field Ops

`/v1/leads · /v1/visit-schedules`

The lead-capture-to-qualification funnel run by Development Agents — from first contact through field visits to a qualified, bank-ready lead.

| Method  | Endpoint                          | Description                                                | Access                           |
| ------- | --------------------------------- | ---------------------------------------------------------- | -------------------------------- |
| `POST`  | `/v1/leads`                       | Creates a new prospective-farmer lead.                     | Bearer Token — Development Agent |
| `GET`   | `/v1/leads`                       | Lists and searches leads.                                  | Bearer Token — Development Agent |
| `GET`   | `/v1/leads/summary`               | Returns lead counts by status.                             | Bearer Token — Development Agent |
| `GET`   | `/v1/leads/metadata`              | Returns dropdown option lists for lead forms.              | Bearer Token — Development Agent |
| `GET`   | `/v1/leads/assignable-users`      | Lists the agents a lead can be assigned to.                | Bearer Token — Development Agent |
| `PATCH` | `/v1/leads/{id}/status`           | Updates a lead's qualification status.                     | Bearer Token — Development Agent |
| `PATCH` | `/v1/leads/{id}/assignment`       | Assigns a lead to a specific agent.                        | Bearer Token — Development Agent |
| `POST`  | `/v1/leads/{id}/comments`         | Adds a note to a lead's timeline.                          | Bearer Token — Development Agent |
| `GET`   | `/v1/leads/{id}/timeline`         | Returns the full activity history for a lead.              | Bearer Token — Development Agent |
| `GET`   | `/v1/leads/{id}/call-logs`        | Returns the call history for a lead.                       | Bearer Token — Development Agent |
| `GET`   | `/v1/leads/{id}/credit-info`      | Returns credit information records collected for a lead.   | Bearer Token — Development Agent |
| `POST`  | `/v1/leads/{id}/credit-info`      | Adds a new credit information record for a lead.           | Bearer Token — Development Agent |
| `GET`   | `/v1/visit-schedules`             | Lists scheduled farmer field visits.                       | Bearer Token — Development Agent |
| `POST`  | `/v1/visit-schedules`             | Schedules a new field visit for a lead.                    | Bearer Token — Development Agent |
| `PATCH` | `/v1/visit-schedules/{id}/status` | Updates a visit's status: completed, cancelled, or missed. | Bearer Token — Development Agent |

---

## 07. Loan Underwriting

`/v1/loan-applications`

A Development Agent converts a qualified lead into a formal loan application at a chosen bank; the bank then reviews and moves it through its own approval pipeline.

| Method   | Endpoint                                               | Description                                                           | Access                           |
| -------- | ------------------------------------------------------ | --------------------------------------------------------------------- | -------------------------------- |
| `POST`   | `/v1/loan-applications`                                | Converts a qualified lead into a formal loan application at a bank.   | Bearer Token — Development Agent |
| `GET`    | `/v1/loan-applications`                                | Lists loan applications for the caller's bank.                        | Bearer Token                     |
| `GET`    | `/v1/loan-applications/summary`                        | Returns loan application totals by pipeline status.                   | Bearer Token                     |
| `GET`    | `/v1/loan-applications/metadata`                       | Returns status dropdown options for loan applications.                | Bearer Token                     |
| `GET`    | `/v1/loan-applications/{id}/full-profile`              | Returns the complete underwriting profile for an application.         | Bearer Token                     |
| `GET`    | `/v1/loan-applications/{id}/basic-profile`             | Returns the applicant's basic profile linked to the originating lead. | Bearer Token — Development Agent |
| `PATCH`  | `/v1/loan-applications/{id}/basic-profile`             | Updates the applicant's contact and location details.                 | Bearer Token — Development Agent |
| `PATCH`  | `/v1/loan-applications/{id}/status`                    | Moves an application to a new stage in the bank's approval pipeline.  | Bearer Token                     |
| `PATCH`  | `/v1/loan-applications/{id}/step`                      | Advances an application's internal processing step.                   | Bearer Token — Development Agent |
| `PATCH`  | `/v1/loan-applications/{id}/officer`                   | Assigns a loan officer to an application.                             | Bearer Token — Development Agent |
| `GET`    | `/v1/loan-applications/{id}/documents`                 | Lists documents attached to an application.                           | Bearer Token — Development Agent |
| `POST`   | `/v1/loan-applications/{id}/documents`                 | Uploads a supporting document to an application.                      | Bearer Token — Development Agent |
| `GET`    | `/v1/loan-applications/{id}/documents/{docId}/content` | Downloads an attached document.                                       | Bearer Token — Development Agent |
| `DELETE` | `/v1/loan-applications/{id}/documents/{docId}`         | Removes an attached document.                                         | Bearer Token — Development Agent |

---

## 08. Consent Management

`/v1/consent`

Fayda national ID consent capture — locating the farmer, verifying identity by one-time code, and submitting a signed data-sharing consent — plus the inbound webhook that returns the registry's decision.

| Method | Endpoint                                    | Description                                                          | Access          |
| ------ | ------------------------------------------- | -------------------------------------------------------------------- | --------------- |
| `GET`  | `/v1/consent/farmers`                       | Searches for a farmer's record by national ID.                       | Bearer Token    |
| `GET`  | `/v1/consent/reasons`                       | Lists the approved reasons a consent request can cite.               | Bearer Token    |
| `GET`  | `/v1/consent/allowed-fields`                | Lists the data fields the partner is authorized to request.          | Bearer Token    |
| `GET`  | `/v1/consent/partners/me/allowed-field-ids` | Returns the same allowed-field list, keyed by ID.                    | Bearer Token    |
| `POST` | `/v1/consent/otp`                           | Requests a one-time verification code from the national ID registry. | Bearer Token    |
| `POST` | `/v1/consent/otp/verify`                    | Verifies the one-time code entered by the farmer.                    | Bearer Token    |
| `POST` | `/v1/consent/requests`                      | Submits the completed, signed consent request for approval.          | Bearer Token    |
| `POST` | `/v1/webhooks/consent-data`                 | Receives the consent decision back from the national ID registry.    | Partner API Key |

---

## 09. Notifications

`/v1/notifications`

In-app notifications for the signed-in user, shared across every role.

| Method   | Endpoint                 | Description                                        | Access       |
| -------- | ------------------------ | -------------------------------------------------- | ------------ |
| `GET`    | `/v1/notifications`      | Lists the caller's notifications and unread count. | Bearer Token |
| `PATCH`  | `/v1/notifications/read` | Marks one or more notifications as read.           | Bearer Token |
| `DELETE` | `/v1/notifications`      | Deletes one or more notifications.                 | Bearer Token |

---

## 10. Inbound Webhooks

`/v1/webhooks`

Server-to-server receivers for external systems pushing events into A2C. Authenticated with a partner API key, not a user token.

| Method | Endpoint             | Description                                                      | Access          |
| ------ | -------------------- | ---------------------------------------------------------------- | --------------- |
| `POST` | `/v1/webhooks/leads` | Receives lead referrals from telco IVR and missed-call gateways. | Partner API Key |

---

## Appendix A: Response Format

Every endpoint returns a consistent JSON envelope, whether the call succeeds or fails.

**Success**

```json
{ "status": "success", "message": "...", "data": { }, "pagination": null }
```

**Error**

```json
{ "status": "error", "message": "...", "code": "VALIDATION_ERROR", "details": { } }
```

Common error codes: `VALIDATION_ERROR`, `AUTHENTICATION_ERROR`, `PERMISSION_DENIED`, `BANK_NOT_ACTIVE`, `BANK_NOT_ONBOARDED`, `PASSWORD_CHANGE_REQUIRED`, `NOT_FOUND`, `RATE_LIMITED`, `INTERNAL_ERROR`.

---

## Appendix B: Pagination

List endpoints accept `page` and `page_size` query parameters and return a `pagination` block alongside the results:

```json
{ "page": 1, "page_size": 20, "total": 42, "total_pages": 3, "has_next": true }
```

---

## Appendix C: Rate Limiting

Requests are throttled per caller to keep the platform stable under load. A caller that exceeds its limit receives HTTP 429 with code `RATE_LIMITED`. Limits vary by endpoint sensitivity — authentication endpoints are the most tightly limited; catalog browsing and partner integration traffic have higher allowances. Contact the A2C integration team for the current limits on your account tier.
