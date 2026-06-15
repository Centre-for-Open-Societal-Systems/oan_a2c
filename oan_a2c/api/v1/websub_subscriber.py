"""
W3C WebSub subscriber endpoint for receiving OpenG2P consent events from a real
WebSub hub (production).

Unlike scripts/websub_hub.py (which *emulates* a hub for demos), this module is
a strict, spec-compliant SUBSCRIBER. It talks to a real hub and must obey the
W3C WebSub protocol exactly, or the hub will refuse to deliver:

  - GET  (intent verification): the hub sends `hub.challenge`; we MUST reply
         200 with the bare challenge string as plain text — no JSON, no HTML.
  - POST (content distribution): the hub sends the payload with an
         `X-Hub-Signature: sha256=<hmac>` header. We MUST recompute
         HMAC-SHA256 over the RAW request body using the shared subscription
         secret and reject the request unless it matches.

The shared secret is the one supplied to the hub at subscription time and is
read here from site_config.json as `websub_secret`.

Endpoint URL (register this as the hub.callback when subscribing):
  .../api/method/oan_a2c.api.v1.websub_subscriber.callback

Once verified, the payload is handed to the same core logic the authenticated
receiver uses (validate_and_enqueue_consent), so processing stays identical.
"""

import hashlib
import hmac

import frappe
from werkzeug.wrappers import Response

from oan_a2c.api.v1.webhook_consent_data import validate_and_enqueue_consent


def _get_subscription_secret():
    secret = frappe.conf.get("websub_secret")
    if not secret:
        # Without a secret we cannot verify signatures; refuse rather than
        # silently accepting unsigned/forgeable payloads.
        frappe.log_error(
            "websub_secret missing from site_config.json", "WebSub Subscriber Misconfigured"
        )
    return secret


def _verify_signature(raw_body: bytes, signature_header: str, secret: str) -> bool:
    """Constant-time compare of X-Hub-Signature against HMAC-SHA256(raw_body)."""
    if not signature_header or not secret:
        return False
    # Header format: "sha256=<hexdigest>"
    if "=" not in signature_header:
        return False
    algo, _, sent_sig = signature_header.partition("=")
    if algo.lower() != "sha256":
        # The spec also permits sha1, but we require sha256.
        return False
    expected_sig = hmac.new(
        secret.encode("utf-8"), raw_body, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected_sig, sent_sig.strip())


@frappe.whitelist(allow_guest=True)
def callback(**kwargs):
    """W3C WebSub callback: GET = intent verification, POST = content delivery."""
    req = frappe.request
    if not req:
        return Response("bad request", status=400, mimetype="text/plain")

    # --- Intent verification (GET): echo hub.challenge verbatim, plain text ---
    if req.method == "GET":
        challenge = req.args.get("hub.challenge")
        mode = req.args.get("hub.mode")
        topic = req.args.get("hub.topic")
        frappe.logger().info(
            f"🤝 WebSub intent verification: mode={mode!r} topic={topic!r}"
        )
        if not challenge:
            # Not a valid verification request.
            return Response("missing hub.challenge", status=400, mimetype="text/plain")
        # MUST be 200 with the bare challenge string, nothing else.
        return Response(challenge, status=200, mimetype="text/plain")

    # --- Content distribution (POST): verify signature, then process ---
    if req.method == "POST":
        secret = _get_subscription_secret()
        raw_body = req.get_data()  # raw bytes, BEFORE any parsing — required for HMAC
        signature = req.headers.get("X-Hub-Signature", "")

        if not _verify_signature(raw_body, signature, secret):
            frappe.logger().warning("❌ WebSub signature verification failed")
            return Response("invalid signature", status=403, mimetype="text/plain")

        try:
            data = frappe.parse_json(raw_body.decode("utf-8")) if raw_body else {}
        except Exception:
            return Response("invalid json", status=400, mimetype="text/plain")

        frappe.logger().info(f"📨 WebSub delivery. Keys: {list(data.keys())}")
        try:
            # No authenticated user on this leg; the background job persists with
            # ignore_permissions under the doc owner.
            consent_doc_name = validate_and_enqueue_consent(data, enforce_permission=False)
            frappe.logger().info(f"✅ WebSub delivery enqueued for {consent_doc_name}")
        except Exception:
            frappe.log_error(frappe.get_traceback(), "WebSub Subscriber processing failed")
            # 2xx tells the hub we accepted it; processing happens async. Returning
            # 5xx would make the hub retry the same (already-logged) failure.
            return Response("accepted", status=202, mimetype="text/plain")

        return Response("accepted", status=202, mimetype="text/plain")

    return Response("method not allowed", status=405, mimetype="text/plain")