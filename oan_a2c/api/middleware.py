import frappe
import jwt
from jwt import PyJWKClient

import logging

logger = logging.getLogger(__name__)

# Module-level cache for the JWKS client.
# Re-created only when the configured URL changes (e.g., after a site config reload).
_jwks_client = None
_jwks_url_cache = None

# Frappe system roles that must NEVER be removed by Keycloak role sync.
# These are internal Frappe concepts that Keycloak has no knowledge of.
PROTECTED_ROLES = frozenset({
    "All",
    "Guest",
    "System Manager",
    "Administrator",
    "Script Manager",
    "Desk User",
})


def validate_jwt_request(request=None):
    """
    Middleware bound to Frappe's auth_hooks.
    Intercepts and validates JWTs for the oan_a2c API namespace.

    Supports dual-mode validation:
    - HS256: Legacy tokens signed with the site's local encryption_key.
    - RS256: Keycloak OIDC tokens validated against the JWKS endpoint.

    For RS256 tokens, also performs:
    - Just-In-Time (JIT) user provisioning
    - Role synchronization from realm_access.roles
    """
    # frappe.local.request is the Werkzeug request object set per-thread.
    path = frappe.local.request.path

    # We only care about our own API boundary.
    # Let Frappe handle desk access and standard APIs normally.
    if not path.startswith("/api/method/oan_a2c."):
        return

    # Whitelisted endpoints that don't require JWT validation
    if path in [
        "/api/method/oan_a2c.api.auth.login",
        "/api/method/oan_a2c.api.auth.forgot_password",
        "/api/method/oan_a2c.api.auth.reset_password",
        "/api/method/oan_a2c.api.v1.webhook_consent_data.receive_consent_data",
        "/api/method/oan_a2c.api.v1.webhooks.lead_inbound",
    ]:
        return

    auth_header = frappe.get_request_header("Authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        # Forcing a hard boundary: If you hit our namespace, you need a JWT.
        raise frappe.AuthenticationError("Missing Authorization Header")

    token = auth_header.split(" ")[1]

    try:
        # Peek at the unverified header to determine the signing algorithm.
        # SECURITY: We never pass this value to jwt.decode() — each path
        # hardcodes its expected algorithm to prevent algorithm confusion attacks.
        unverified_header = jwt.get_unverified_header(token)
        alg = unverified_header.get("alg")

        if alg == "HS256":
            payload = _validate_hs256(token)
            _set_frappe_user(payload.get("sub"))

        elif alg == "RS256":
            payload = _validate_rs256(token)
            email = payload.get("email")
            if not email:
                raise frappe.AuthenticationError("Token missing email claim")

            _ensure_user_exists(payload, email)
            _sync_roles(email, payload)
            _set_frappe_user(email)

        else:
            # Reject tokens with 'none' or any other unexpected algorithm.
            raise frappe.AuthenticationError("Unsupported token algorithm")

    except jwt.ExpiredSignatureError:
        raise frappe.AuthenticationError("Token has expired")
    except jwt.InvalidTokenError:
        raise frappe.AuthenticationError("Invalid token")


# ---------------------------------------------------------------------------
# HS256 — Legacy / Native Token Validation
# ---------------------------------------------------------------------------

def _validate_hs256(token):
    """
    Validate a legacy HS256 token signed with the site's local encryption_key.
    This is the original A2C login flow — unchanged behavior.
    """
    secret = frappe.conf.get("encryption_key")
    if not secret:
        raise frappe.AuthenticationError("System encryption key missing")

    # SECURITY: Algorithm is hardcoded — never derived from the token header.
    return jwt.decode(token, secret, algorithms=["HS256"])


# ---------------------------------------------------------------------------
# RS256 — Keycloak OIDC Token Validation
# ---------------------------------------------------------------------------

def _validate_rs256(token):
    """
    Validate an RS256 token issued by Keycloak against its JWKS endpoint.

    Validates:
    - Cryptographic signature via JWKS public key
    - Token expiration (exp claim)
    - Issuer (iss claim) — ensures token comes from the configured Keycloak realm
    - Audience (aud claim) — ensures token was intended for this application
    """
    jwks_url = _get_jwks_url()
    jwks_client = _get_jwks_client(jwks_url)

    try:
        signing_key = jwks_client.get_signing_key_from_jwt(token)
    except jwt.exceptions.PyJWKClientError:
        raise frappe.AuthenticationError("Unable to find signing key for token")

    decode_options = {
        "verify_exp": True,
    }

    # Build decode kwargs — issuer and audience validation are enabled
    # when the corresponding config values are present.
    decode_kwargs = {
        "algorithms": ["RS256"],
        "options": decode_options,
    }

    expected_issuer = _get_expected_issuer()
    if expected_issuer:
        decode_kwargs["issuer"] = expected_issuer

    expected_audience = frappe.conf.get("keycloak_client_id")
    if expected_audience:
        decode_kwargs["audience"] = expected_audience

    # SECURITY: Algorithm is hardcoded — never derived from the token header.
    return jwt.decode(token, signing_key.key, **decode_kwargs)


def _get_jwks_url():
    """
    Construct the JWKS endpoint URL from site_config.json values.
    Returns: <base_url>/realms/<realm>/protocol/openid-connect/certs

    Uses `keycloak_jwks_base_url` if set, otherwise falls back to `keycloak_url`.
    This separation is needed in Docker environments where the Frappe container
    reaches Keycloak via a different hostname (e.g., host.docker.internal)
    than what Keycloak embeds in its tokens (e.g., localhost).
    """
    # Prefer explicit JWKS base URL for network-reachability, fall back to keycloak_url
    jwks_base = frappe.conf.get("keycloak_jwks_base_url") or frappe.conf.get("keycloak_url")
    realm = frappe.conf.get("keycloak_realm")

    if not jwks_base or not realm:
        raise frappe.AuthenticationError(
            "Keycloak configuration missing (keycloak_url and keycloak_realm required)"
        )

    # Strip trailing slash to avoid double-slash in URL
    base = jwks_base.rstrip("/")
    return f"{base}/realms/{realm}/protocol/openid-connect/certs"


def _get_expected_issuer():
    """
    Construct the expected issuer claim value from site_config.json.
    Returns: <keycloak_url>/realms/<realm>

    Always uses `keycloak_url` — this must match the `iss` claim that
    Keycloak embeds in its tokens (the URL clients use to reach Keycloak).
    """
    keycloak_url = frappe.conf.get("keycloak_url")
    realm = frappe.conf.get("keycloak_realm")

    if not keycloak_url or not realm:
        return None

    base = keycloak_url.rstrip("/")
    return f"{base}/realms/{realm}"


def _get_jwks_client(jwks_url):
    """
    Return a cached PyJWKClient instance. The client caches JWKS keys internally
    using a configurable TTL (default: 300 seconds / 5 minutes).

    The module-level cache is invalidated if the JWKS URL changes
    (e.g., after a Keycloak URL reconfiguration).
    """
    global _jwks_client, _jwks_url_cache

    if _jwks_client is None or _jwks_url_cache != jwks_url:
        cache_ttl = int(frappe.conf.get("keycloak_jwks_cache_ttl", 300))
        _jwks_client = PyJWKClient(jwks_url, lifespan=cache_ttl)
        _jwks_url_cache = jwks_url

    return _jwks_client


# ---------------------------------------------------------------------------
# JIT (Just-In-Time) User Provisioning
# ---------------------------------------------------------------------------

def _ensure_user_exists(payload, email):
    """
    Create a Frappe User document on-the-fly if the email from the Keycloak
    token does not exist in the system.

    This ensures foreign key constraints (owner, assigned_to, etc.) are
    satisfied without manual user provisioning.
    """
    if frappe.db.exists("User", email):
        return

    first_name = payload.get("given_name") or email.split("@")[0]
    last_name = payload.get("family_name", "")

    user = frappe.new_doc("User")
    user.email = email
    user.first_name = first_name
    user.last_name = last_name
    user.user_type = "System User"
    user.send_welcome_email = 0
    user.append("roles", {"role": "Desk User"})
    user.flags.ignore_permissions = True
    user.flags.no_welcome_mail = True
    user.insert(ignore_permissions=True)
    frappe.db.commit()

    logger.info("JIT provisioned user: %s", email)


# ---------------------------------------------------------------------------
# Role Synchronization
# ---------------------------------------------------------------------------

def _sync_roles(email, payload):
    """
    Synchronize Keycloak realm_access.roles with Frappe's Has Role table.

    Rules:
    - Only roles that exist in Frappe's tabRole are considered valid.
    - Protected system roles (All, Guest, System Manager, etc.) are never
      added or removed by this sync.
    - Keycloak roles that don't exist in Frappe are silently ignored.
    - On each request, Frappe roles are made to match Keycloak roles
      (add missing, remove revoked) for non-protected roles.
    """
    realm_access = payload.get("realm_access", {})
    kc_roles = set(realm_access.get("roles", []))

    if not kc_roles:
        return

    # Filter to only roles that actually exist in Frappe's tabRole table
    existing_frappe_roles = set(
        r.name for r in frappe.get_all(
            "Role",
            filters={"name": ["in", list(kc_roles)]},
            fields=["name"],
        )
    )

    # Exclude protected roles from the set Keycloak can manage
    valid_kc_roles = existing_frappe_roles - PROTECTED_ROLES

    user = frappe.get_doc("User", email)
    current_roles = {r.role for r in user.roles}

    # Roles managed by Keycloak: current non-protected roles
    manageable_current = current_roles - PROTECTED_ROLES

    # Diff: what to add, what to remove
    to_add = valid_kc_roles - current_roles
    to_remove = manageable_current - valid_kc_roles

    if not to_add and not to_remove:
        return

    for role_name in to_add:
        user.append("roles", {"role": role_name})

    if to_remove:
        user.roles = [r for r in user.roles if r.role not in to_remove]

    user.flags.ignore_permissions = True
    user.save(ignore_permissions=True)
    frappe.db.commit()

    if to_add:
        logger.info("Roles added for %s: %s", email, to_add)
    if to_remove:
        logger.info("Roles removed for %s: %s", email, to_remove)


# ---------------------------------------------------------------------------
# User Context
# ---------------------------------------------------------------------------

def _set_frappe_user(user_identifier):
    """
    Set the Frappe user context for the current request thread.
    Preserves form_dict which frappe.set_user() resets.
    """
    temp_form_dict = getattr(frappe.local, "form_dict", None)
    frappe.set_user(user_identifier)
    if temp_form_dict is not None:
        frappe.local.form_dict = temp_form_dict
