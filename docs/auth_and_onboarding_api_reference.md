# OpenAgriNet Access to Credit (A2C) - Authentication & Organization Onboarding API Reference

This document provides a tabular, bordered-ready specification for all APIs covering **Identity & Authentication**, **Password Lifecycle**, **User Profile Management**, and **Organization (Bank) Onboarding & Administration** in `oan_a2c`.

---

## 1. Master Summary Tables

### 1.1 Identity, Authentication & Profile APIs

| Method    | Endpoint                    | Legacy RPC Method                            | Auth Required   | Rate Limit                  | Purpose                                                                                           |
| :-------- | :-------------------------- | :------------------------------------------- | :-------------- | :-------------------------- | :------------------------------------------------------------------------------------------------ |
| **POST**  | `/v1/auth/login`            | `oan_a2c.api.auth.login`                     | Public (Guest)  | 100/min (IP), 10/min (User) | Authenticate user; returns 15-min JWT access token, 30-day/1-day refresh token, and bank context. |
| **POST**  | `/v1/auth/register`         | `oan_a2c.api.v1.auth.register_user`          | Public (Guest)  | 50/min (IP), 5/min (Phone)  | Self-register for eligible roles (`Bank Admin`, `Development Agent`, `Farmer`).                   |
| **POST**  | `/v1/auth/token/refresh`    | `oan_a2c.api.auth.refresh`                   | Public (Guest)  | 30/min (IP)                 | Single-use rotation of refresh token; yields new JWT and fresh refresh token.                     |
| **POST**  | `/v1/auth/logout`           | `oan_a2c.api.auth.logout`                    | Public / Bearer | Standard                    | Revokes and deletes database-backed refresh token record.                                         |
| **POST**  | `/v1/auth/password/initial` | `oan_a2c.api.auth.set_initial_password`      | Public (Guest)  | 5/5min (IP)                 | Rotates temporary admin-issued password for invited staff (`a2c_must_change_password=1`).         |
| **POST**  | `/v1/auth/password/forgot`  | `oan_a2c.api.auth.forgot_password`           | Public (Guest)  | 5/min (IP)                  | Generates a secure 6-digit OTP reset key with 15-minute validity.                                 |
| **POST**  | `/v1/auth/password/reset`   | `oan_a2c.api.auth.reset_password`            | Public (Guest)  | 5/5min (Email)              | Completes password reset using verified 6-digit OTP reset key.                                    |
| **PATCH** | `/v1/me/password`           | `oan_a2c.api.auth.change_password`           | Bearer (JWT)    | 5/5min (User)               | Authenticated user changes password after verifying existing password.                            |
| **GET**   | `/v1/me`                    | `oan_a2c.api.auth.get_me`                    | Bearer (JWT)    | Standard                    | Returns lightweight session identity: roles, classified user type, and linked bank context.       |
| **GET**   | `/v1/me/profile`            | `oan_a2c.api.auth.get_user_profile`          | Bearer (JWT)    | Standard                    | Returns full user profile (personal details, contact, organization, employee ID, member since).   |
| **PATCH** | `/v1/me/profile`            | `oan_a2c.api.auth.update_profile`            | Bearer (JWT)    | Standard                    | Updates personal details (name, phone, language, gender, and verified avatar image).              |
| **POST**  | `/v1/consent/otp`           | `oan_a2c.api.v1.consent.consent.request_otp` | Bearer (JWT)    | 3/min (User)                | Dispatches Fayda National ID OTP for identity authentication.                                     |
| **POST**  | `/v1/consent/otp/verify`    | `oan_a2c.api.v1.consent.consent.verify_otp`  | Bearer (JWT)    | 5/min (User)                | Verifies Fayda National ID OTP code for consent capture.                                          |

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
| **POST**  | `/v1/banks/me/team`                         | `oan_a2c.api.v1.seller.onboarding.invite_team_member`    | `Bank Admin`         | Standard   | Invites/provisions a new `Bank Agent` with a temporary password (`a2c_must_change_password=1`).                                 |
| **GET**   | `/v1/banks/me/team`                         | `oan_a2c.api.v1.seller.onboarding.list_users`            | `Bank Admin`         | Standard   | Lists all bank staff members with role, active status, and temporary password flag.                                             |
| **PATCH** | `/v1/banks/me/team/{userId}`                | `oan_a2c.api.v1.seller.onboarding.update_user`           | `Bank Admin` / Admin | Standard   | Updates team member details, role assignment, or enables/disables access.                                                       |
| **POST**  | `/v1/banks/me/team/{userId}/password-reset` | `oan_a2c.api.v1.seller.onboarding.reset_member_password` | `Bank Admin`         | 10/5min    | Reissues temporary password, revokes active sessions, sets `a2c_must_change_password=1`.                                        |
| **GET**   | `/v1/banks/me/pipeline-stages`              | `oan_a2c.api.v1.seller.loan_stages.get_stages`           | Bank Roles / Admin   | Standard   | Retrieves custom loan pipeline stages configured for the organization.                                                          |
| **POST**  | `/v1/banks/me/pipeline-stages`              | `oan_a2c.api.v1.seller.loan_stages.add_stage`            | `Bank Admin` / Admin | Standard   | Adds an individual custom loan pipeline stage mapped to archetype status.                                                       |
| **PUT**   | `/v1/banks/me/pipeline-stages`              | `oan_a2c.api.v1.seller.loan_stages.sync_stages`          | `Bank Admin` / Admin | Standard   | Bulk synchronizes and reorders bank loan pipeline stages.                                                                       |
| **GET**   | `/v1/banks/me/dashboard/stats`              | `oan_a2c.api.v1.seller.dashboard.get_stats`              | Bank Roles / Admin   | Standard   | Retrieves aggregate performance metrics (products, loan applications, approvals).                                               |

---

## 2. Detailed Identity & Authentication Endpoint Specifications

### 2.1 POST /v1/auth/login

| Attribute             | Details                                                                                                                                                                                                                                                                                |
| :-------------------- | :------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Endpoint**          | `POST /v1/auth/login` (also available via `POST /api/v1/auth/login`)                                                                                                                                                                                                                   |
| **Legacy RPC**        | `POST /api/method/oan_a2c.api.auth.login`                                                                                                                                                                                                                                              |
| **Authentication**    | Public / Guest                                                                                                                                                                                                                                                                         |
| **Rate Limit**        | 100 requests/minute per IP; 10 requests/minute per login ID                                                                                                                                                                                                                            |
| **Request Headers**   | `Content-Type: application/json`                                                                                                                                                                                                                                                       |
| **Request Body**      | `{"usr": "admin@bank.com", "pwd": "Password123!", "remember_me": false}`                                                                                                                                                                                                               |
| **Field Constraints** | `usr`: string (min 1 char, accepts email or phone number)<br>`pwd`: string (min 1 char)<br>`remember_me`: boolean (optional, default: `false`)                                                                                                                                         |
| **Success Response**  | `200 OK`<br>`{"status": "success", "data": {"token": "<jwt>", "refresh_token": "<token>", "user": {"email": "...", "full_name": "...", "roles": [...], "user_type": "bank_admin", "bank": "...", "bank_id": "...", "bank_code": "...", "bank_name": "...", "bank_status": "Active"}}}` |
| **Error Handling**    | `401 AuthenticationError`: Incorrect email or password.<br>`401 PasswordChangeRequired`: User must set initial password before signing in.<br>`429 ValidationError`: Rate limit exceeded.                                                                                              |

---

### 2.2 POST /v1/auth/register

| Attribute             | Details                                                                                                                                                                                                                                              |
| :-------------------- | :--------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Endpoint**          | `POST /v1/auth/register` (also available via `POST /api/v1/auth/register`)                                                                                                                                                                           |
| **Legacy RPC**        | `POST /api/method/oan_a2c.api.v1.auth.register_user`                                                                                                                                                                                                 |
| **Authentication**    | Public / Guest                                                                                                                                                                                                                                       |
| **Rate Limit**        | 50 requests/minute per IP; 5 requests/minute per phone number                                                                                                                                                                                        |
| **Request Headers**   | `Content-Type: application/json`                                                                                                                                                                                                                     |
| **Request Body**      | `{"full_name": "Abebe Bikila", "email": "abebe@bank.com", "password": "SecurePassword123!", "phone_number": "+251911223344", "role": "Bank Admin"}`                                                                                                  |
| **Field Constraints** | `full_name`: min 2 chars<br>`email`: valid email string<br>`password`: min 8 chars, 1 uppercase, 1 lowercase, 1 number, 1 special char<br>`phone_number`: valid phone format (+251...)<br>`role`: one of `Bank Admin`, `Development Agent`, `Farmer` |
| **Success Response**  | `200 OK`<br>`{"status": "success", "data": {"message": "Account created successfully."}}`                                                                                                                                                            |
| **Existing Account**  | `200 OK`<br>`{"status": "success", "data": {"message": "You already have an account. Please log in.", "already_exists": true}}`                                                                                                                      |
| **Error Handling**    | `400 ValidationError`: Missing required fields, invalid role, or password complexity failure.                                                                                                                                                        |

---

### 2.3 POST /v1/auth/token/refresh

| Attribute              | Details                                                                                                                                                             |
| :--------------------- | :------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| **Endpoint**           | `POST /v1/auth/token/refresh`                                                                                                                                       |
| **Legacy RPC**         | `POST /api/method/oan_a2c.api.auth.refresh`                                                                                                                         |
| **Authentication**     | Public / Guest                                                                                                                                                      |
| **Rate Limit**         | 30 requests/minute per IP                                                                                                                                           |
| **Request Body**       | `{"refresh_token": "40_character_hex_hash"}`                                                                                                                        |
| **Field Constraints**  | `refresh_token`: string (required)                                                                                                                                  |
| **Success Response**   | `200 OK`<br>`{"status": "success", "data": {"token": "<new_jwt_access_token>", "refresh_token": "<new_40_char_refresh_token>"}}`                                    |
| **Security Mechanism** | Single-use token rotation: deletes submitted token upon validation and generates a fresh token pair. Checks user enabled state and `a2c_must_change_password` flag. |
| **Error Handling**     | `401 AuthenticationError`: Invalid, expired, or revoked refresh token; user disabled.                                                                               |

---

### 2.4 POST /v1/auth/logout

| Attribute            | Details                                                                     |
| :------------------- | :-------------------------------------------------------------------------- |
| **Endpoint**         | `POST /v1/auth/logout`                                                      |
| **Legacy RPC**       | `POST /api/method/oan_a2c.api.auth.logout`                                  |
| **Authentication**   | Public / Bearer                                                             |
| **Request Body**     | `{"refresh_token": "40_character_hex_hash"}`                                |
| **Success Response** | `200 OK`<br>`{"status": "success", "message": "Logged out successfully."}`  |
| **Business Logic**   | Deletes the corresponding token record in `A2C User Refresh Token` DocType. |

---

### 2.5 POST /v1/auth/password/initial

| Attribute             | Details                                                                                                                                                            |
| :-------------------- | :----------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Endpoint**          | `POST /v1/auth/password/initial`                                                                                                                                   |
| **Legacy RPC**        | `POST /api/method/oan_a2c.api.auth.set_initial_password`                                                                                                           |
| **Authentication**    | Public / Guest                                                                                                                                                     |
| **Rate Limit**        | 5 requests / 5 minutes per IP                                                                                                                                      |
| **Request Body**      | `{"usr": "invited.agent@bank.com", "current_password": "TempPassword1", "new_password": "PermanentSecurePassword123!"}`                                            |
| **Field Constraints** | `usr`: email or mobile number<br>`current_password`: temporary admin-issued password<br>`new_password`: 8-64 chars with standard password complexity               |
| **Success Response**  | `200 OK`<br>`{"status": "success", "message": "Password set successfully. Please sign in with your new password."}`                                                |
| **Business Logic**    | Authenticates temporary password, verifies `a2c_must_change_password == 1`, updates password, clears must-change flag to `0`, and purges any prior refresh tokens. |

---

### 2.6 POST /v1/auth/password/forgot & /reset

| Attribute            | Details                                                                                                                                                                                     |
| :------------------- | :------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| **Forgot Endpoint**  | `POST /v1/auth/password/forgot` &mdash; `{"email": "user@example.com"}`<br>Rate limit: 5/min. Generates 6-digit OTP reset key with 15-min expiry.                                           |
| **Reset Endpoint**   | `POST /v1/auth/password/reset` &mdash; `{"email": "user@example.com", "key": "123456", "new_password": "NewSecurePassword123!"}`<br>Rate limit: 5/5min. Validates OTP and updates password. |
| **Success Response** | `200 OK`<br>`{"status": "success", "message": "Your password has been successfully updated. You may now login."}`                                                                           |

---

### 2.7 GET /v1/me & /v1/me/profile

| Attribute              | Details                                                                                                                                                                                                                                                                                                                                                                                                   |
| :--------------------- | :-------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **GET /v1/me**         | Returns lightweight session identity: `email`, `full_name`, `roles`, `user_type`, and linked `bank` metadata.                                                                                                                                                                                                                                                                                             |
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
| **POST (Invite Member)** | `POST /v1/banks/me/team`<br>Permission: `Bank Admin`<br>Request: `{"email": "agent@bank.com", "full_name": "Haile Gebrselassie", "password": "TempPassword1", "role": "Bank Agent"}`<br>Creates User record, assigns `Bank Agent` role, sets `a2c_must_change_password=1`, and creates `User Permission` bank binding.<br>Response: `{"status": "success", "data": {"message": "Team member invited successfully."}}` |
| **GET (List Team)**      | `GET /v1/banks/me/team`<br>Permission: `Bank Admin`<br>Response: `{"status": "success", "data": {"users": [{"name": "agent@bank.com", "email": "agent@bank.com", "first_name": "Haile Gebrselassie", "enabled": 1, "last_active": "2026-09-24 14:20:00", "role": "Bank Agent", "must_change_password": true}]}}`                                                                                                      |

---

### 3.8 PATCH & POST /v1/banks/me/team/{userId} (Member Updates & Password Reset)

| Attribute                 | Details                                                                                                                                                                                                                                                                                                                                                                                                  |
| :------------------------ | :------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **PATCH (Update User)**   | `PATCH /v1/banks/me/team/{userId}`<br>Request: `{"full_name": "Haile Gebrselassie", "role": "Bank Agent", "enabled": true}`<br>Permission: `Bank Admin` (strictly lower level, within own bank) or Platform Admin.<br>Response: `{"status": "success", "message": "User updated successfully."}`                                                                                                         |
| **POST (Password Reset)** | `POST /v1/banks/me/team/{userId}/password-reset`<br>Request: `{"password": "NewTempPassword2"}`<br>Permission: `Bank Admin` or Platform Admin. Rate Limit: 10/5min.<br>Reissues temporary password, revokes active refresh tokens, sets `a2c_must_change_password=1`.<br>Response: `{"status": "success", "message": "Temporary password issued. The agent must set their own password at next login."}` |

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
