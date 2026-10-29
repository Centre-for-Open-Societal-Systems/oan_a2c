# JWT signing key setup for an existing A2C site

`oan_auth_service` signs access tokens with RS256. It needs an RSA private key in the site config. Sites deployed before the auth migration have no such key, so token issuing fails with `JWTKeyConfigurationError` ("No `jwt_private_keys` in site_config.json") until you add one.

This guide covers a one-time manual setup on an already-running A2C site (develop, staging or main). Repeat it once per environment. Do not share a key between environments.

## What gets configured

| Config key         | Value                       | Purpose                                                                                       |
| ------------------ | --------------------------- | --------------------------------------------------------------------------------------------- |
| `jwt_private_keys` | `{"v1": "keys/jwt_v1.pem"}` | Maps each key id (`kid`) to a PEM file path (relative to the site directory) or the PEM text. |
| `jwt_current_kid`  | `v1`                        | The `kid` used to sign new tokens.                                                            |

Verifiers (Kong, APISIX, other apps) need no secret. They read the public half from `GET /api/v1/auth/keys` (JWKS).

The old `jwt_secrets` (HS256) value is ignored by `oan_auth_service`. Leave it in place until the gateway verifies with JWKS.

## Prerequisites

- SSH access to the backend host.
- `oan_auth_service` is installed on the site (`bench --site <site> list-apps`).
- The `sites/` directory is on a persistent volume. Check this first. If the container is recreated without the volume, the key is lost and every issued token becomes invalid.

## Steps

All commands run in the backend container. The develop site is `a2c-backend-development.oanstaging.com`.

```bash
ssh <user>@<backend-host>
cd <deploy-dir>            # directory holding docker-compose.yml
docker compose exec backend bash
```

1. Generate a 3072-bit RSA key in the site directory.

   ```bash
   cd sites/a2c-backend-development.oanstaging.com
   mkdir -p keys
   openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:3072 -out keys/jwt_v1.pem
   chmod 600 keys/jwt_v1.pem
   ```

2. Set the site config.

   ```bash
   cd /home/frappe/frappe-bench
   bench --site a2c-backend-development.oanstaging.com set-config jwt_private_keys '{"v1": "keys/jwt_v1.pem"}' --parse
   bench --site a2c-backend-development.oanstaging.com set-config jwt_current_kid v1
   ```

3. Restart the backend so every worker reloads the config.

   ```bash
   exit
   docker compose restart backend
   ```

## Verify

```bash
curl -s https://a2c-backend-development.oanstaging.com/api/v1/auth/keys | jq '.keys[].kid'
```

Expected: `"v1"`. A login request should now return an access token.

## Rotating the key later

1. Generate `keys/jwt_v2.pem`.
2. Set `jwt_private_keys` to `{"v1": "keys/jwt_v1.pem", "v2": "keys/jwt_v2.pem"}`. Both keys are now published in the JWKS.
3. Wait for verifiers to refresh their JWKS cache.
4. Set `jwt_current_kid` to `v2`.
5. After the longest access token lifetime has passed (`jwt_access_token_ttl`, default 900 s), remove `v1` from `jwt_private_keys`.

## Backup and loss

- Back up `keys/jwt_v1.pem` somewhere secure, such as a secrets manager.
- If the key is lost, generate a new one. All existing tokens stop validating, so users must log in again.
- Never commit the PEM file to git.
- Do not reuse `encryption_key` for this. That value encrypts data at rest and must stay stable. The signing key must be cheap to rotate.

## Longer term

The Jenkins deploy does not set these values yet. A follow-up change should store the key as a Jenkins credential and write it during deploy, so a rebuilt host does not lose it.
