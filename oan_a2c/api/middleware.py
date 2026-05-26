import frappe
import jwt

def validate_jwt_request(request=None):
    """
    Middleware bound to Frappe's auth_hooks.
    Intercepts and validates JWTs for the oan_a2c API namespace.
    """
    # frappe.local.request is the Werkzeug request object set per-thread.
    # Using frappe.request here would be ambiguous — frappe.local.request is explicit
    # and matches what test stubs patch directly.
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
        "/api/method/oan_a2c.api.webhook_api.receive_consent_data"
    ]:
        return

    auth_header = frappe.get_request_header("Authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        # Forcing a hard boundary: If you hit our namespace, you need a JWT.
        raise frappe.AuthenticationError("Missing Authorization Header")

    token = auth_header.split(" ")[1]
    secret = frappe.conf.get("encryption_key")
    
    if not secret:
        # A server configuration error. The NSPF mindset demands we fail securely.
        raise frappe.AuthenticationError("System encryption key missing")

    try:
        # Decode and validate cryptographically
        payload = jwt.decode(token, secret, algorithms=["HS256"])
        
        # Log the user context into the Python thread memory for Frappe's ORM RBAC
        frappe.set_user(payload.get("sub"))
        
    except jwt.ExpiredSignatureError:
        raise frappe.AuthenticationError("Token has expired")
    except jwt.InvalidTokenError:
        raise frappe.AuthenticationError("Invalid token")
