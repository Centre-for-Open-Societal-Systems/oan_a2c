# OpenAgriNet Access to Credit (A2C) - Authentication & Organization Onboarding API Reference

This document provides a tabular, bordered-ready specification for all APIs covering **Identity & Authentication**, **Password Lifecycle**, **User Profile Management**, and **Organization (Bank) Onboarding & Administration** in `oan_a2c`.

---

## 1. Master Summary Tables

### 1.1 Identity, Authentication & Profile APIs

| Method    | Endpoint                    | Legacy RPC Method                                   | Auth Required  | Rate Limit    | Purpose                                                                                                           |
| :-------- | :-------------------------- | :-------------------------------------------------- | :------------- | :------------ | :---------------------------------------------------------------------------------------------------------------- |
| **POST**  | `/v1/auth/login`            | `oan_auth_service.api.v1.auth.login`                | Public (Guest) | Kong per-IP   | Authenticate; returns 15-min RS256 access token, refresh token, roles. Identifier may be email, phone or user id. |
| **POST**  | `/v1/auth/register`         | `oan_auth_service.api.v1.auth.register_user`        | Public (Guest) | Kong per-IP   | Self-register for roles in `jwt_self_registerable_roles`; returns the first token pair.                           |
| **POST**  | `/v1/auth/refresh`          | `oan_auth_service.api.v1.auth.refresh`              | Public (Guest) | Kong per-IP   | Single-use rotation of the refresh token; returns a new token pair.                                               |
| **POST**  | `/v1/auth/logout`           | `oan_auth_service.api.v1.auth.logout`               | Public (Guest) | Kong per-IP   | Revokes the refresh token.                                                                                        |
| **POST**  | `/v1/auth/password/initial` | `oan_auth_service.api.v1.auth.set_initial_password` | Public (Guest) | 10/5min (IP)  | Replaces a temporary admin-issued password (`oan_must_change_password=1`).                                        |
| **POST**  | `/v1/auth/password/forgot`  | `oan_auth_service.api.v1.auth.forgot_password`      | Public (Guest) | Kong per-IP   | Starts password recovery by email link or SMS one-time code.                                                      |
| **POST**  | `/v1/auth/password/reset`   | `oan_auth_service.api.v1.auth.reset_password`       | Public (Guest) | 10/h (IP)     | Completes the reset with `key`, or `usr` + `otp`.                                                                 |
| **PATCH** | `/v1/me/password`           | `oan_a2c.api.auth.change_password`                  | Bearer (JWT)   | 5/5min (User) | Authenticated user changes password after verifying existing password.                                            |
| **GET**   | `/v1/auth/me`               | `oan_auth_service.api.v1.auth.get_me`               | Bearer (JWT)   | Standard      | Returns identity and roles. a2c adds `profiles.a2c` with user type and linked bank context.                       |
| **GET**   | `/v1/me/profile`            | `oan_a2c.api.auth.get_user_profile`                 | Bearer (JWT)   | Standard      | Returns full user profile (personal details, contact, organization, employee ID, member since).                   |
| **PATCH** | `/v1/me/profile`            | `oan_a2c.api.auth.update_profile`                   | Bearer (JWT)   | Standard      | Updates personal details (name, phone, language, gender, and verified avatar image).                              |
| **POST**  | `/v1/consent/otp`           | `oan_a2c.api.v1.consent.consent.request_otp`        | Bearer (JWT)   | 3/min (User)  | Dispatches Fayda National ID OTP for identity authentication.                                                     |
| **POST**  | `/v1/consent/otp/verify`    | `oan_a2c.api.v1.consent.consent.verify_otp`         | Bearer (JWT)   | 5/min (User)  | Verifies Fayda National ID OTP code for consent capture.                                                          |

---

### 1.2 Organization (Bank) Onboarding & Administration APIs

| Method    | Endpoint                                    | Legacy RPC Method                                        | Allowed Role(s)      | Rate Limit | Purpose                                                                                                                         |
| :-------- | :------------------------------------------ | :------------------------------------------------------- | :------------------- | :--------- | :------------------------------------------------------------------------------------------------------------------------------ |
| **POST**  | `/v1/banks`                                 | `oan_a2c.api.v1.seller.onboarding.register_bank`         | Authenticated        | Standard   | Registers a new lending organization (`A2C Participating Bank`), creates user permission binding, sets status to `"In Review"`. |
| **GET**   | `/v1/banks/me`                              | `oan_a2c.api.v1.seller.onboarding.get_bank_profile`      | Bank Roles / Admin   | Standard   | Retrieves organization profile, brand info, address, compliance contacts, and KYC status.                                       |
| **PATCH** | `/v1/banks/me`                              | `oan_a2c.api.v1.seller.onboarding.update_bank_profile`   | `Bank Admin`         | Standard   | Updates organization profile, brand name, website, registered address, and logo URL.                                            |
| **POST**  | `/v1/images`                                | `oan_a2c.api.v1.seller.onboarding.upload_image`          | Authenticated        | Standard   | Uploads public PNG/JPEG/WebP images (max 5MB) for bank logos or user avatars.                                                   |
| **POST**  | `/v1/banks/me/kyc-documents`                | `oan_a2c.api.v1.seller.onboarding.upload_kyc_document`   | `Bank Admin`         | Standard   | Uploads private PDF regulatory / banking license compliance document.                                                           |
| **GET**   | `/v1/banks/me/kyc-documents`                | `oan_a2c.api.v1.seller.onboarding.download_kyc_document` | `Bank Admin`         | Standard   | Securely streams/downloads stored private KYC PDF (`?view=1` for inline view).                                                  |
| **PUT**   | `/v1/banks/me/contacts`                     | `oan_a2c.api.v1.seller.onboarding.save_org_contacts`     | `Bank Admin`         | Standard   | Configures mandatory Grievance Redressal Officer (GRO) and Operations (OPS) contacts.                                           |
| **PATCH** | `/v1/banks/me/status`                       | `oan_a2c.api.v1.seller.onboarding.update_bank_status`    | Platform Admin       | Standard   | Governance action to update bank status (`In Review` $\rightarrow$ `Active` / `Suspended`).                                     |
| **POST**  | `/v1/banks/me/team`                         | `oan_a2c.api.v1.seller.onboarding.invite_team_member`    | `Bank Admin`         | Standard   | Invites/provisions a new `Bank Agent` with a temporary password (`oan_must_change_password=1`).                                 |
| **GET**   | `/v1/banks/me/team`                         | `oan_a2c.api.v1.seller.onboarding.list_users`            | `Bank Admin`         | Standard   | Lists all bank staff members with role, active status, and temporary password flag.                                             |
| **PATCH** | `/v1/banks/me/team/{userId}`                | `oan_a2c.api.v1.seller.onboarding.update_user`           | `Bank Admin` / Admin | Standard   | Updates team member details, role assignment, or enables/disables access.                                                       |
| **POST**  | `/v1/banks/me/team/{userId}/password-reset` | `oan_a2c.api.v1.seller.onboarding.reset_member_password` | `Bank Admin`         | 10/5min    | Reissues temporary password, revokes active sessions, sets `oan_must_change_password=1`.                                        |
| **GET**   | `/v1/banks/me/pipeline-stages`              | `oan_a2c.api.v1.seller.loan_stages.get_stages`           | Bank Roles / Admin   | Standard   | Retrieves custom loan pipeline stages configured for the organization.                                                          |
| **POST**  | `/v1/banks/me/pipeline-stages`              | `oan_a2c.api.v1.seller.loan_stages.add_stage`            | `Bank Admin` / Admin | Standard   | Adds an individual custom loan pipeline stage mapped to archetype status.                                                       |
| **PUT**   | `/v1/banks/me/pipeline-stages`              | `oan_a2c.api.v1.seller.loan_stages.sync_stages`          | `Bank Admin` / Admin | Standard   | Bulk synchronizes and reorders bank loan pipeline stages.                                                                       |
| **GET**   | `/v1/banks/me/dashboard/stats`              | `oan_a2c.api.v1.seller.dashboard.get_stats`              | Bank Roles / Admin   | Standard   | Retrieves aggregate performance metrics (products, loan applications, approvals).                                               |

---

## 2. Detailed Identity & Authentication Endpoint Specifications

> **Where the code lives.** Login, register, refresh, logout and the password flows are served by the shared
> `oan_auth_service` app (a required app of `oan_a2c`). The paths below are the A2C gateway paths; the app serves
> them under `/api/v1/...`. The endpoint-level contract is documented in `oan_auth_service/docs/API_DOCUMENTATION.md`.
>
> **Rate limiting.** `oan_auth_service` limits only the password-recovery flows itself. Login, register and refresh
> are limited at the Kong gateway (`public-auth` tier, per IP). A request that reaches the app directly is unlimited.

> **Development Agents sign in only through SSO.** They authenticate with the OpenG2P IAM service (Keycloak behind
> it); the frontend's `/api/auth/sso/*` routes run that login and forward IAM's access token as the Bearer token.
> `oan_auth_service` validates it with IAM, creates the local user on first sign-in (`<hash>@iam.internal`) and sets
> its roles from the IAM client named by the `iam_application` site config. A local (password) account holding
> `A2C Development Agent` is refused on every API call (`require_sso_for_development_agents` in `api/auth.py`), so
> `POST /v1/auth/login` is not a way in for them.

### 2.1 POST /v1/auth/login

| Attribute             | Details                                                                                                                                                                                                      |
| :-------------------- | :----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Endpoint**          | `POST /v1/auth/login` (served as `POST /api/v1/auth/login`)                                                                                                                                                  |
| **Authentication**    | Public / Guest                                                                                                                                                                                               |
| **Request Body**      | `{"usr": "admin@bank.com", "pwd": "Password123!", "remember_me": false, "scope": null}`                                                                                                                      |
| **Field Constraints** | `usr`: email, phone number or user id<br>`pwd`: string<br>`remember_me`: boolean, default `false`<br>`scope`: optional comma-separated subset of the caller's roles (narrows the token's `scope` claim only) |
| **Success Response**  | `200 OK`<br>`{"status": "success", "data": {"access_token": "<jwt>", "refresh_token": "<token>", "token_type": "Bearer", "expires_in": 900, "user": "<user id>", "roles": [...], "scope": null}}`            |
| **User type / bank**  | Not in the login response. Call `GET /v1/auth/me` and read `data.profiles.a2c` (`user_type`, `bank`, `bank_id`, `bank_code`, `bank_name`, `bank_status`).                                                    |
| **Error Handling**    | `401`: invalid credentials.<br>`401 PasswordChangeRequired`: the account holds a temporary password and must call `POST /v1/auth/password/initial`.                                                          |

---

### 2.2 POST /v1/auth/register

| Attribute             | Details                                                                                                                                                                                                                                                                                                                                                   |
| :-------------------- | :-------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Endpoint**          | `POST /v1/auth/register` (served as `POST /api/v1/auth/register`)                                                                                                                                                                                                                                                                                         |
| **Authentication**    | Public / Guest                                                                                                                                                                                                                                                                                                                                            |
| **Request Body**      | `{"full_name": "Abebe Bikila", "email": "abebe@bank.com", "password": "SecurePassword123!", "phone_number": "+251911223344", "role": "A2C Bank Admin"}`                                                                                                                                                                                                   |
| **Field Constraints** | `full_name`: required<br>`email` or `phone_number`: at least one (phone is E.164, or national digits with `country_code`)<br>`password`: 8-128 chars with complexity<br>`role` / `roles`: only roles listed in the site's `jwt_self_registerable_roles` (a2c: `A2C Bank Admin`, `A2C Farmer`); `A2C Development Agent` is provisioned by an administrator |
| **Success Response**  | `200 OK`, same body as login (a token pair). The caller is signed in immediately.                                                                                                                                                                                                                                                                         |
| **Existing Account**  | `400 ValidationError`: "A user with this email address already exists." / "...phone number...". The old `already_exists: true` success response is gone: it let callers test which numbers are registered.                                                                                                                                                |
| **Error Handling**    | `400`: missing fields, password complexity, duplicate email or phone.<br>`403`: role not self-registerable.                                                                                                                                                                                                                                               |

---

### 2.3 POST /v1/auth/refresh

| Attribute              | Details                                                                                                                                                                                           |
| :--------------------- | :------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| **Endpoint**           | `POST /v1/auth/refresh` (renamed from `/v1/auth/token/refresh`)                                                                                                                                   |
| **Authentication**     | Public / Guest                                                                                                                                                                                    |
| **Request Body**       | `{"refresh_token": "<token>"}`                                                                                                                                                                    |
| **Success Response**   | `200 OK`, same body as login (a new token pair)                                                                                                                                                   |
| **Security Mechanism** | Single-use rotation: the submitted token is deleted and a new pair issued. Roles are re-read from the database on every refresh. The user must be enabled and must not hold a temporary password. |
| **Error Handling**     | `401`: invalid, expired or revoked refresh token; user disabled.                                                                                                                                  |

---

### 2.4 POST /v1/auth/logout

| Attribute            | Details                                                                                                          |
| :------------------- | :--------------------------------------------------------------------------------------------------------------- |
| **Endpoint**         | `POST /v1/auth/logout`                                                                                           |
| **Authentication**   | Public / Guest (works with an expired access token)                                                              |
| **Request Body**     | `{"refresh_token": "<token>"}`                                                                                   |
| **Success Response** | `200 OK`<br>`{"status": "success", "data": {"revoked": true}}`. Reports `true` whether or not the token existed. |
| **Business Logic**   | Deletes the matching `OAN User Refresh Token`. Access tokens expire on their own (15 min).                       |

---

### 2.5 POST /v1/auth/password/initial

| Attribute             | Details                                                                                                                                                                                                        |
| :-------------------- | :------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Endpoint**          | `POST /v1/auth/password/initial`                                                                                                                                                                               |
| **Authentication**    | Public / Guest                                                                                                                                                                                                 |
| **Rate Limit**        | 10 requests / 5 minutes per IP                                                                                                                                                                                 |
| **Request Body**      | `{"usr": "invited.agent@bank.com", "current_password": "TempPassword1", "new_password": "PermanentSecurePassword123!"}`                                                                                        |
| **Field Constraints** | `usr`: email, phone or user id<br>`current_password`: the temporary admin-issued password<br>`new_password`: 8-128 chars with complexity, different from the temporary one                                     |
| **Success Response**  | `200 OK`. No token is issued; sign in again with the new password.                                                                                                                                             |
| **Business Logic**    | Authenticates the temporary password, verifies `oan_must_change_password == 1`, updates the password, clears the flag, and purges prior refresh tokens. Every refusal is the same "Invalid login credentials". |

---

### 2.6 POST /v1/auth/password/forgot & /reset

| Attribute            | Details                                                                                                                                                                                                                                                   |
| :------------------- | :-------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Forgot Endpoint**  | `POST /v1/auth/password/forgot` &mdash; `{"usr": "user@example.com"}`<br>Sends a reset link by email, or a one-time code by SMS when the account has a phone number and an SMS gateway is configured. The response never says whether the account exists. |
| **Reset Endpoint**   | `POST /v1/auth/password/reset` &mdash; `{"new_password": "...", "key": "<emailed key>"}` or `{"new_password": "...", "usr": "...", "otp": "123456"}`<br>Rate limited per IP.                                                                              |
| **Success Response** | `200 OK`                                                                                                                                                                                                                                                  |

---

### 2.7 GET /v1/auth/me & /v1/me/profile

| Attribute              | Details                                                                                                                                                                                                                                                                                                                                                                                                   |
| :--------------------- | :-------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **GET /v1/auth/me**    | Served by `oan_auth_service`. Returns `user`, `first_name`, `last_name`, `full_name`, `login_email`, `mobile_no`, `roles`, and `profiles`. a2c contributes `profiles.a2c` = `{"user_type": "bank_admin", "bank": "...", "bank_id": "...", "bank_code": "...", "bank_name": "...", "bank_status": "Active"}` (bank fields only for bank roles).                                                            |
| **GET /v1/me/profile** | Returns extended profile details: personal info (image, name, email, gender, mobile, language) and account info (role label, organization, employee ID, member since).                                                                                                                                                                                                                                    |
| **Authentication**     | Bearer (JWT)                                                                                                                                                                                                                                                                                                                                                                                              |
| **Success Response**   | `200 OK`<br>`{"status": "success", "data": {"personal_information": {"user_image": "/files/pic.png", "full_name": "Abebe Bikila", "email_address": "admin@bank.com", "gender": "Male", "phone_number": "+251911223344", "language": "en"}, "account_information": {"user_role": "Bank Admin", "organization": "Cooperative Bank of Oromia", "employee_id": "EMP-1002", "member_since": "January 2026"}}}` |

---

### 2.8 PATCH /v1/me/profile & PATCH /v1/me/password

| Attribute                 | Details                                                                                                                                                                                                                                                                                                         |
| :------------------------ | :-------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **PATCH /v1/me/profile**  | `{"full_name": "New Name", "phone_number": "+251911223344", "language": "am", "gender": "Male", "user_image": "/files/uploaded_logo.png"}`<br>Updates user profile. Language automatically resolves ISO aliases (`am`, `om`, `ti`, `sw`, `en`). Image URLs are checked for user ownership to prevent tampering. |
| **PATCH /v1/me/password** | `{"current_password": "OldPassword123!", "new_password": "NewPassword123!"}`<br>Rate limit: 5/5min. Validates current password and sets new password.                                                                                                                                                           |

---

## 3. Detailed Organization (Bank) Onboarding & Administration Endpoint Specifications

### 3.1 POST /v1/banks (Register Organization)

| Attribute            | Details                                                                                                                                                                                                                                                                                                                                                                                                                                                                                              |
| :------------------- | :--------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Endpoint**         | `POST /v1/banks` (also available via `POST /api/v1/banks`)                                                                                                                                                                                                                                                                                                                                                                                                                                           |
| **Legacy RPC**       | `POST /api/method/oan_a2c.api.v1.seller.onboarding.register_bank`                                                                                                                                                                                                                                                                                                                                                                                                                                    |
| **Authentication**   | Bearer (JWT)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         |
| **Request Body**     | `{"bank_name": "Cooperative Bank of Oromia", "bank_code": "CBO001", "entity_type": "Commercial Bank", "registered_street": "Bole Road", "registered_kebele_village": "Kebele 03", "registered_woreda_district": "Bole Subcity", "registered_zone": "Addis Ababa", "registered_region": "Addis Ababa", "registered_country": "Ethiopia", "registered_postal_code": "1000", "registered_email": "contact@coopbank.com.et", "registered_phone": "+251115504455", "website": "https://coopbank.com.et"}` |
| **Success Response** | `200 OK`<br>`{"status": "success", "data": {"message": "Bank registered successfully. Currently in review.", "bank_code": "CBO001", "bank_id": "BANK-0001"}}`                                                                                                                                                                                                                                                                                                                                        |
| **Business Logic**   | Normalizes TIN/bank code. Creates `A2C Participating Bank` doc (initial status `"In Review"`), creates default `User Permission` binding for the creator.                                                                                                                                                                                                                                                                                                                                            |
| **Duplicate TIN**    | If TIN is already registered, automatically generates an administrative review `ToDo` task for operators and returns an in-review success response.                                                                                                                                                                                                                                                                                                                                                  |

---

### 3.2 GET & PATCH /v1/banks/me (Organization Profile)

| Attribute              | Details                                                                                                                                                                                                                                                                                                 |
| :--------------------- | :------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| **GET /v1/banks/me**   | Fetches the organization profile, brand details, address, and status. Bank Admins also receive compliance contacts (`gro_name`, `ops_name`) and KYC document upload status.                                                                                                                             |
| **PATCH /v1/banks/me** | `{"brand_name": "Coopbank AgriCredit", "website": "https://coopbank.com.et/agri", "logo": "/files/hash.png", "registered_street": "Updated St 456"}`<br>Permission: `Bank Admin` only.<br>Updates brand details and address. Validates that `logo` belongs to a file uploaded by a member of this bank. |
| **Success Response**   | `200 OK`<br>`{"status": "success", "data": {"message": "Organization profile updated successfully."}}`                                                                                                                                                                                                  |

---

### 3.3 POST /v1/images (Upload Bank Logo / Public Media)

| Attribute             | Details                                                                                                                                                       |
| :-------------------- | :------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| **Endpoint**          | `POST /v1/images` (also available via `POST /api/v1/images`)                                                                                                  |
| **Legacy RPC**        | `POST /api/method/oan_a2c.api.v1.seller.onboarding.upload_image`                                                                                              |
| **Authentication**    | Bearer (JWT)                                                                                                                                                  |
| **Request Body**      | `{"filename": "bank_logo.png", "filedata": "<base64_encoded_image_bytes>"}`                                                                                   |
| **Field Constraints** | `filename`: valid `.png`, `.jpg`, `.jpeg`, or `.webp` extension.<br>`filedata`: valid Base64 string, max decoded size 5MB. Validates file header magic bytes. |
| **Success Response**  | `200 OK`<br>`{"status": "success", "data": {"message": "Image uploaded successfully.", "file_url": "/files/<32_char_random_hash>.png"}}`                      |
| **Security Note**     | Generates an unguessable 32-character hash filename to prevent enumeration of unlaunched bank brands.                                                         |

---

### 3.4 POST & GET /v1/banks/me/kyc-documents

| Attribute              | Details                                                                                                                                                                                                                                                                                                                                                                                                                                     |
| :--------------------- | :------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| **POST (Upload KYC)**  | `POST /v1/banks/me/kyc-documents`<br>Request: `{"filename": "license_2026.pdf", "filedata": "<base64_encoded_pdf_bytes>"}`<br>Constraints: PDF only, magic bytes verified (`%PDF-`), max ~15MB base64.<br>Saves document as a private File attached to the `A2C Participating Bank` record.<br>Response: `{"status": "success", "data": {"message": "KYC document uploaded successfully.", "file_url": "/private/files/license_2026.pdf"}}` |
| **GET (Download KYC)** | `GET /v1/banks/me/kyc-documents?view=1`<br>Permission: `Bank Admin` only.<br>Securely streams the private PDF content. Set query param `view=1` to render inline in browser, or omit to trigger file download attachment.                                                                                                                                                                                                                   |

---

### 3.5 PUT /v1/banks/me/contacts (Grievance & Ops Contacts)

| Attribute             | Details                                                                                                                     |
| :-------------------- | :-------------------------------------------------------------------------------------------------------------------------- |
| **Endpoint**          | `PUT /v1/banks/me/contacts`                                                                                                 |
| **Legacy RPC**        | `POST /api/method/oan_a2c.api.v1.seller.onboarding.save_org_contacts`                                                       |
| **Authentication**    | Bearer (`Bank Admin`)                                                                                                       |
| **Request Body**      | `{"gro_name": "Derartu Tulu", "gro_mobile": "+251911001122", "ops_name": "Kenenisa Bekele", "ops_mobile": "+251911334455"}` |
| **Field Constraints** | `gro_name` & `ops_name`: min 1 char, max 140 chars<br>`gro_mobile` & `ops_mobile`: valid phone format (+251...)             |
| **Success Response**  | `200 OK`<br>`{"status": "success", "data": {"message": "Contacts saved successfully."}}`                                    |

---

### 3.6 PATCH /v1/banks/me/status (Platform Operator Approval)

| Attribute             | Details                                                                                                                              |
| :-------------------- | :----------------------------------------------------------------------------------------------------------------------------------- |
| **Endpoint**          | `PATCH /v1/banks/me/status`                                                                                                          |
| **Legacy RPC**        | `POST /api/method/oan_a2c.api.v1.seller.onboarding.update_bank_status`                                                               |
| **Authentication**    | Platform Admin (`Administrator` or `System Manager` - Level 1)                                                                       |
| **Request Body**      | `{"bank_code": "CBO001", "new_status": "Active"}`                                                                                    |
| **Field Constraints** | `bank_code`: string (required)<br>`new_status`: `"In Review"`, `"Active"`, or `"Suspended"`                                          |
| **State Transitions** | `In Review` $\rightarrow$ `Active` or `Suspended`<br>`Active` $\rightarrow$ `Suspended`<br>`Suspended` $\rightarrow$ `Active`        |
| **Success Response**  | `200 OK`<br>`{"status": "success", "data": {"message": "Bank status updated to Active"}}`                                            |
| **Error Handling**    | `403 PermissionError`: Non-platform admins cannot approve/suspend banks.<br>`400 ValidationError`: Invalid state transition attempt. |

---

### 3.7 POST & GET /v1/banks/me/team (Staff Provisioning)

| Attribute                | Details                                                                                                                                                                                                                                                                                                                                                                                                               |
| :----------------------- | :-------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **POST (Invite Member)** | `POST /v1/banks/me/team`<br>Permission: `Bank Admin`<br>Request: `{"email": "agent@bank.com", "full_name": "Haile Gebrselassie", "password": "TempPassword1", "role": "Bank Agent"}`<br>Creates User record, assigns `Bank Agent` role, sets `oan_must_change_password=1`, and creates `User Permission` bank binding.<br>Response: `{"status": "success", "data": {"message": "Team member invited successfully."}}` |
| **GET (List Team)**      | `GET /v1/banks/me/team`<br>Permission: `Bank Admin`<br>Response: `{"status": "success", "data": {"users": [{"name": "agent@bank.com", "email": "agent@bank.com", "first_name": "Haile Gebrselassie", "enabled": 1, "last_active": "2026-09-24 14:20:00", "role": "Bank Agent", "must_change_password": true}]}}`                                                                                                      |

---

### 3.8 PATCH & POST /v1/banks/me/team/{userId} (Member Updates & Password Reset)

| Attribute                 | Details                                                                                                                                                                                                                                                                                                                                                                                                  |
| :------------------------ | :------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **PATCH (Update User)**   | `PATCH /v1/banks/me/team/{userId}`<br>Request: `{"full_name": "Haile Gebrselassie", "role": "Bank Agent", "enabled": true}`<br>Permission: `Bank Admin` (strictly lower level, within own bank) or Platform Admin.<br>Response: `{"status": "success", "message": "User updated successfully."}`                                                                                                         |
| **POST (Password Reset)** | `POST /v1/banks/me/team/{userId}/password-reset`<br>Request: `{"password": "NewTempPassword2"}`<br>Permission: `Bank Admin` or Platform Admin. Rate Limit: 10/5min.<br>Reissues temporary password, revokes active refresh tokens, sets `oan_must_change_password=1`.<br>Response: `{"status": "success", "message": "Temporary password issued. The agent must set their own password at next login."}` |

---

### 3.9 Pipeline Stages & Dashboard APIs

| Attribute                             | Details                                                                                                                                                                        |
| :------------------------------------ | :----------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **GET /v1/banks/me/pipeline-stages**  | `GET /v1/banks/me/pipeline-stages` &mdash; Lists bank custom pipeline stages.                                                                                                  |
| **POST /v1/banks/me/pipeline-stages** | `POST /v1/banks/me/pipeline-stages`<br>Request: `{"label": "Field Inspection", "archetype_state": "In Transition", "sequence": 2, "description": "Verification of farm site"}` |
| **PUT /v1/banks/me/pipeline-stages**  | `PUT /v1/banks/me/pipeline-stages`<br>Request: `{"stages": [{"stage_id": "...", "label": "Document Review", "archetype_state": "In Transition", "sequence": 1}, ...]}`         |
| **GET /v1/banks/me/dashboard/stats**  | `GET /v1/banks/me/dashboard/stats`<br>Returns aggregated metrics: active loan products, applications in pipeline, total sanctioned amount, and disbursement totals.            |

---

## 4. Standard Response Envelope & Error Codes

### 4.1 Success Envelope

```json
{
  "status": "success",
  "data": { ... },
  "message": "Optional confirmation message"
}
```

### 4.2 Error Envelope

```json
{
  "status": "error",
  "message": "Error description message",
  "code": "VALIDATION_ERROR | UNAUTHORIZED | FORBIDDEN | NOT_FOUND | RATE_LIMITED | INTERNAL_ERROR",
  "details": { ... }
}
```

### 4.3 HTTP Status Code Mapping

- `200 OK`: Request succeeded.
- `400 Bad Request`: Payload validation failed (`VALIDATION_ERROR`).
- `401 Unauthorized`: Missing/invalid Bearer token or temporary password rotation required (`PASSWORD_CHANGE_REQUIRED`).
- `403 Forbidden`: Privilege check failed (`FORBIDDEN`).
- `404 Not Found`: Entity not found (`NOT_FOUND`).
- `429 Too Many Requests`: Rate limit exceeded (`RATE_LIMITED`).
- `500 Internal Server Error`: Server error (`INTERNAL_ERROR`).
