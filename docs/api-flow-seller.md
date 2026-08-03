
### 6.12 `POST /api/method/oan_a2c.api.v1.seller.onboarding.upload_image`

Uploads a Base64-encoded image (e.g., bank logo or product image) and returns its URL.

**Authentication & Permissions:** Requires JWT Bearer token.
**Parameters (JSON Body):**

| Param                  | Type   | Required | Default | Notes                                                                            |
| :--------------------- | :----- | :------- | :------ | :------------------------------------------------------------------------------- |
| **`filename`** | string | Yes      | —      | Min 4, max 100 chars. Must match regex pattern for images (png, jpg, jpeg, webp) |
| **`filedata`** | string | Yes      | —      | Base64-encoded image string. Min 10, max 7,000,000 chars (~5MB limit)            |

**Success Response (HTTP 200):**

```json
{
  "status": "success",
  "message": "Image uploaded successfully.",
  "data": {
    "message": "Image uploaded successfully.",
    "file_url": "/files/bank-logo.png"
  }
}
```

**Error Cases:**

- **400 `VALIDATION_ERROR`**: Filename does not match allowed image extensions or string length out of bounds.
- **400 `VALIDATION_ERROR`**: Base64 decoding fails (`Invalid file: content is not valid Base64.`).
- **401 `AUTHENTICATION_ERROR`**: Called by unauthenticated user.
- **500 `INTERNAL_ERROR`**: File creation or database save failure (`Failed to save uploaded image.`).

---

## 7. Endpoint Reference: Authentication & Identity Gateway (`api/auth.py`)

### 7.1 `POST /api/method/oan_a2c.api.auth.login`

Authenticates seller credentials and returns a short-lived access JWT (15-min expiry) along with a database-backed refresh token.

**Authentication & Permissions:** Guest accessible (`allow_guest=True`).
**Parameters (JSON Body):**

| Param             | Type    | Required | Default | Notes                                                      |
| :---------------- | :------ | :------- | :------ | :--------------------------------------------------------- |
| **`usr`** | string  | Yes      | —      | User email address. Min length 1                           |
| **`pwd`** | string  | Yes      | —      | User password. Min length 1                                |
| `remember_me`   | boolean | No       | false   | If true, refresh token expires in 30 days instead of 1 day |

**Success Response (HTTP 200):**

```json
{
  "status": "success",
  "message": "Success",
  "data": {
    "token": "eyJhbGciOiJIUzI1NiIsIn...",
    "refresh_token": "a1b2c3d4e5f6...",
    "user": {
      "email": "admin@bank.com",
      "full_name": "Abebe Kebede",
      "roles": ["A2C Bank Admin", "System Manager"],
      "bank": "A2C-BANK-0001"
    }
  }
}
```

**Error Cases:**

- **400 `VALIDATION_ERROR`**: Missing `usr` or `pwd`.
- **401 `AUTHENTICATION_ERROR`**: Incorrect email/password (`Incorrect email or password.`), or account disabled/locked.
- **500 `INTERNAL_ERROR`**: System configuration error (missing `encryption_key`) or database error.

---

### 7.2 `POST /api/method/oan_a2c.api.auth.forgot_password`

Generates a 6-digit OTP for password recovery and sends it via SMS (if mobile number exists) or email.

**Authentication & Permissions:** Guest accessible (`allow_guest=True`).
**Parameters (JSON Body):**

| Param               | Type   | Required | Default | Notes                      |
| :------------------ | :----- | :------- | :------ | :------------------------- |
| **`email`** | string | Yes      | —      | Valid email address format |

**Success Response (HTTP 200):**

```json
{
  "status": "success",
  "message": "If your email is registered, a password reset OTP has been sent via email or SMS.",
  "data": null
}
```

*(Note: Unknown email addresses return success silently without sending an email/SMS to prevent account enumeration).*

**Error Cases:**

- **400 `VALIDATION_ERROR`**: Invalid email format.
- **500 `INTERNAL_ERROR`**: Mail or SMS transport failure.

---

### 7.3 `POST /api/method/oan_a2c.api.auth.reset_password`

Verifies the 6-digit OTP key and sets a new password for the account.

**Authentication & Permissions:** Guest accessible (`allow_guest=True`).
**Parameters (JSON Body):**

| Param                      | Type   | Required | Default | Notes                                          |
| :------------------------- | :----- | :------- | :------ | :--------------------------------------------- |
| **`email`**        | string | Yes      | —      | Valid email address format                     |
| **`key`**          | string | Yes      | —      | The 6-digit OTP key sent to user. Min length 1 |
| **`new_password`** | string | Yes      | —      | New password string. Min length 1              |

**Success Response (HTTP 200):**

```json
{
  "status": "success",
  "message": "Your password has been successfully updated. You may now login.",
  "data": null
}
```

**Error Cases:**

- **400 `VALIDATION_ERROR`**: Missing parameters or invalid email format.
- **401 `AUTHENTICATION_ERROR`**: Invalid or expired OTP key (`Invalid or expired reset OTP.`).
- **500 `INTERNAL_ERROR`**: Database update failure.

---

### 7.4 `POST /api/method/oan_a2c.api.auth.refresh`

Rotates a valid refresh token, issuing a new access JWT and a new refresh token while deleting the old token.

**Authentication & Permissions:** Guest accessible (`allow_guest=True`).
**Parameters (JSON Body):**

| Param                       | Type   | Required | Default | Notes                                              |
| :-------------------------- | :----- | :------- | :------ | :------------------------------------------------- |
| **`refresh_token`** | string | Yes      | —      | Currently valid refresh token string. Min length 1 |

**Success Response (HTTP 200):**

```json
{
  "status": "success",
  "message": "Success",
  "data": {
    "token": "eyJhbGciOiJIUzI1NiIsIn...",
    "refresh_token": "f6e5d4c3b2a1..."
  }
}
```

**Error Cases:**

- **400 `VALIDATION_ERROR`**: Missing `refresh_token` parameter.
- **401 `AUTHENTICATION_ERROR`**: Token hash not found (`Invalid or expired refresh token.`), token past expiry date (`Refresh token has expired.`), or target user account disabled (`User is disabled or does not exist.`).
- **500 `INTERNAL_ERROR`**: Database transaction failure.

---

### 7.5 `POST /api/method/oan_a2c.api.auth.logout`

Revokes a refresh token by deleting it from the database.

**Authentication & Permissions:** Guest accessible (`allow_guest=True`).
**Parameters (JSON Body):**

| Param                       | Type   | Required | Default | Notes                                 |
| :-------------------------- | :----- | :------- | :------ | :------------------------------------ |
| **`refresh_token`** | string | Yes      | —      | Refresh token to revoke. Min length 1 |

**Success Response (HTTP 200):**

```json
{
  "status": "success",
  "message": "Logged out successfully.",
  "data": null
}
```

**Error Cases:**

- **400 `VALIDATION_ERROR`**: Missing `refresh_token` parameter.
- **500 `INTERNAL_ERROR`**: Database deletion failure.

---

### 7.6 `GET /api/method/oan_a2c.api.auth.get_me`

Returns the authenticated caller's profile details including roles and associated bank binding.

**Authentication & Permissions:** Requires JWT Bearer token.
**Parameters:** None.
**Success Response (HTTP 200):**

```json
{
  "status": "success",
  "message": "Success",
  "data": {
    "email": "admin@bank.com",
    "full_name": "Abebe Kebede",
    "roles": ["A2C Bank Admin", "System Manager"],
    "bank": "A2C-BANK-0001"
  }
}
```

**Error Cases:**

- **401 `AUTHENTICATION_ERROR`**: Called by unauthenticated user (`Guest`).
- **500 `INTERNAL_ERROR`**: Database lookup failure.

---

### 7.7 `GET /api/method/oan_a2c.api.auth.get_user_profile`

Returns detailed profile information for the authenticated user, designed specifically for the "My Profile" screen.

**Authentication & Permissions:** Requires JWT Bearer token.
**Parameters:** None.
**Success Response (HTTP 200):**

```json
{
  "status": "success",
  "message": "Success",
  "data": {
    "personal_information": {
      "user_image": "/private/files/avatar.png",
      "full_name": "Abebe Kebede",
      "email_address": "admin@bank.com",
      "phone_number": "+251911111111",
      "language": "English"
    },
    "account_information": {
      "user_role": "Bank Admin",
      "organization": "Example Bank",
      "employee_id": "EMP-001",
      "member_since": "July 2026"
    }
  }
}
```

**Error Cases:**

- **401 `AUTHENTICATION_ERROR`**: Called by unauthenticated user.

---

### 7.8 `POST /api/method/oan_a2c.api.auth.update_profile`

Updates the personal profile details of the authenticated user.

**Authentication & Permissions:** Requires JWT Bearer token.
**Parameters (JSON Body):**

| Param            | Type   | Required | Default | Notes                                     |
| :--------------- | :----- | :------- | :------ | :---------------------------------------- |
| `full_name`    | string | No       | null    | The user's full name                      |
| `phone_number` | string | No       | null    | The user's mobile number                  |
| `language`     | string | No       | null    | Preferred language code (e.g., "English") |
| `user_image`   | string | No       | null    | URL from the`upload_image` endpoint     |

**Success Response (HTTP 200):**
*Returns the fully updated profile object identical to `7.7 get_user_profile`.*

**Error Cases:**

- **401 `AUTHENTICATION_ERROR`**: Called by unauthenticated user.
- **500 `INTERNAL_ERROR`**: Database save failure.
