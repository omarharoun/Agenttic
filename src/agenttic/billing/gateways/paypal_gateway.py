"""PayPal integration — subscriptions/orders via the PayPal REST API + a
verified webhook. SANDBOX by default.

Keys come ONLY from the environment:
* ``PAYPAL_CLIENT_ID``  — REST app client id
* ``PAYPAL_SECRET``     — REST app secret
* ``PAYPAL_WEBHOOK_ID`` — the webhook id (needed to verify webhook signatures)
* ``PAYPAL_ENV``        — ``sandbox`` (default) or ``live``

All calls go through the REST API with ``requests`` (already a dependency); we
don't pull in a PayPal SDK. :func:`is_configured` is False until the client
id/secret are set, and the checkout endpoint returns 503 until then.
"""

from __future__ import annotations

import os

SANDBOX_BASE = "https://api-m.sandbox.paypal.com"
LIVE_BASE = "https://api-m.paypal.com"


def client_id() -> str:
    return (os.environ.get("PAYPAL_CLIENT_ID") or "").strip()


def client_secret() -> str:
    return (os.environ.get("PAYPAL_SECRET") or "").strip()


def webhook_id() -> str:
    return (os.environ.get("PAYPAL_WEBHOOK_ID") or "").strip()


def env() -> str:
    return (os.environ.get("PAYPAL_ENV") or "sandbox").strip().lower()


def is_sandbox() -> bool:
    return env() != "live"


def api_base() -> str:
    return SANDBOX_BASE if is_sandbox() else LIVE_BASE


def is_configured() -> bool:
    return bool(client_id() and client_secret())


def _access_token(timeout: float = 20.0) -> str:
    """OAuth2 client-credentials token for the REST API."""
    import requests  # noqa: PLC0415
    resp = requests.post(
        f"{api_base()}/v1/oauth2/token",
        auth=(client_id(), client_secret()),
        data={"grant_type": "client_credentials"},
        headers={"Accept": "application/json"}, timeout=timeout)
    resp.raise_for_status()
    return resp.json()["access_token"]


def create_order(*, tenant: str, amount_cents: int, currency: str,
                 description: str, return_url: str, cancel_url: str,
                 reference: str = "") -> dict:
    """Create a one-off PayPal ORDER (for a credit top-up). Returns
    ``{"id", "approve_url"}``. The tenant is carried in ``custom_id`` so the
    webhook can resolve it."""
    import requests  # noqa: PLC0415
    token = _access_token()
    value = f"{amount_cents / 100:.2f}"
    body = {
        "intent": "CAPTURE",
        "purchase_units": [{
            "custom_id": f"{tenant}|{reference}",
            "description": description[:127],
            "amount": {"currency_code": currency.upper(), "value": value},
        }],
        "application_context": {"return_url": return_url, "cancel_url": cancel_url,
                                "user_action": "PAY_NOW"},
    }
    resp = requests.post(f"{api_base()}/v2/checkout/orders", json=body,
                         headers={"Authorization": f"Bearer {token}",
                                  "Content-Type": "application/json"}, timeout=20.0)
    resp.raise_for_status()
    data = resp.json()
    approve = next((ln["href"] for ln in data.get("links", [])
                    if ln.get("rel") == "approve"), "")
    return {"id": data["id"], "approve_url": approve}


def create_subscription(*, tenant: str, plan_external_id: str, return_url: str,
                        cancel_url: str) -> dict:
    """Create a PayPal SUBSCRIPTION for a pre-created PayPal plan id. Returns
    ``{"id", "approve_url"}``. ``custom_id`` carries the tenant."""
    import requests  # noqa: PLC0415
    token = _access_token()
    body = {
        "plan_id": plan_external_id,
        "custom_id": tenant,
        "application_context": {"return_url": return_url, "cancel_url": cancel_url,
                                "user_action": "SUBSCRIBE_NOW"},
    }
    resp = requests.post(f"{api_base()}/v1/billing/subscriptions", json=body,
                         headers={"Authorization": f"Bearer {token}",
                                  "Content-Type": "application/json"}, timeout=20.0)
    resp.raise_for_status()
    data = resp.json()
    approve = next((ln["href"] for ln in data.get("links", [])
                    if ln.get("rel") == "approve"), "")
    return {"id": data["id"], "approve_url": approve}


def verify_webhook(*, headers: dict, body: dict) -> bool:
    """Verify a PayPal webhook via the REST verify-signature endpoint. Requires
    ``PAYPAL_WEBHOOK_ID``. Returns True iff PayPal reports ``SUCCESS``."""
    wid = webhook_id()
    if not wid:
        raise RuntimeError("PayPal webhook id not set (PAYPAL_WEBHOOK_ID).")
    import requests  # noqa: PLC0415
    token = _access_token()
    verify_body = {
        "auth_algo": headers.get("paypal-auth-algo"),
        "cert_url": headers.get("paypal-cert-url"),
        "transmission_id": headers.get("paypal-transmission-id"),
        "transmission_sig": headers.get("paypal-transmission-sig"),
        "transmission_time": headers.get("paypal-transmission-time"),
        "webhook_id": wid,
        "webhook_event": body,
    }
    resp = requests.post(
        f"{api_base()}/v1/notifications/verify-webhook-signature",
        json=verify_body, headers={"Authorization": f"Bearer {token}",
                                   "Content-Type": "application/json"}, timeout=20.0)
    resp.raise_for_status()
    return resp.json().get("verification_status") == "SUCCESS"
