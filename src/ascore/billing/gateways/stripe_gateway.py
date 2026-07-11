"""Stripe integration — Checkout for subscriptions + one-off credit top-ups, and
a signature-verified webhook.

Keys come ONLY from the environment (Stripe TEST mode):
* ``STRIPE_SECRET_KEY``    — secret API key (``sk_test_...`` to stay in test mode)
* ``STRIPE_WEBHOOK_SECRET`` — the webhook signing secret (``whsec_...``)

Both MUST be set to go live; until then :func:`is_configured` is False and the
checkout endpoints return a clear 503 while the free-credits/ledger path keeps
working. We put the Agenttic ``tenant`` in the session + subscription metadata AND
``client_reference_id`` so the unauthenticated webhook can resolve the tenant
without a lookup.
"""

from __future__ import annotations

import os

STRIPE_SECRET_ENV = "STRIPE_SECRET_KEY"
STRIPE_PUBLISHABLE_ENV = "STRIPE_PUBLISHABLE_KEY"   # NOT secret — exposed to the UI
STRIPE_WEBHOOK_SECRET_ENV = "STRIPE_WEBHOOK_SECRET"


def secret_key() -> str:
    return (os.environ.get(STRIPE_SECRET_ENV) or "").strip()


def publishable_key() -> str:
    """The Stripe publishable key (``pk_…``). NOT secret — safe to expose to the
    frontend (it identifies the account for client-side Stripe.js, and can't move
    money). Read only from the environment; never hardcoded."""
    return (os.environ.get(STRIPE_PUBLISHABLE_ENV) or "").strip()


def webhook_secret() -> str:
    return (os.environ.get(STRIPE_WEBHOOK_SECRET_ENV) or "").strip()


def is_configured() -> bool:
    return bool(secret_key())


def is_test_mode() -> bool:
    """True when the configured key is a Stripe TEST key (sk_test_…)."""
    return secret_key().startswith("sk_test_")


def _client():
    """The stripe SDK, keyed. Raises if the SDK isn't installed or no key set."""
    key = secret_key()
    if not key:
        raise RuntimeError("Stripe is not configured (set STRIPE_SECRET_KEY).")
    import stripe  # noqa: PLC0415 — optional dependency, imported on demand
    stripe.api_key = key
    return stripe


def create_checkout_session(*, tenant: str, mode: str, success_url: str,
                            cancel_url: str, line_items: list[dict],
                            customer_email: str | None = None,
                            metadata: dict | None = None) -> dict:
    """Create a Stripe Checkout Session. ``mode`` is ``"subscription"`` (a plan)
    or ``"payment"`` (a one-off top-up). ``line_items`` are Stripe line-item
    dicts. Returns ``{"id", "url"}``. The tenant is stamped into metadata and
    ``client_reference_id`` for webhook resolution."""
    stripe = _client()
    md = {"tenant": tenant, **(metadata or {})}
    kwargs = dict(
        mode=mode,
        success_url=success_url,
        cancel_url=cancel_url,
        line_items=line_items,
        client_reference_id=tenant,
        metadata=md,
    )
    if customer_email:
        kwargs["customer_email"] = customer_email
    if mode == "subscription":
        kwargs["subscription_data"] = {"metadata": md}
    session = stripe.checkout.Session.create(**kwargs)
    return {"id": session["id"], "url": session["url"]}


def construct_event(payload: bytes, sig_header: str) -> dict:
    """Verify a webhook signature and return the parsed event dict. Raises on a
    bad/missing signature (the caller maps that to 400). Requires
    ``STRIPE_WEBHOOK_SECRET``."""
    secret = webhook_secret()
    if not secret:
        raise RuntimeError("Stripe webhook secret not set (STRIPE_WEBHOOK_SECRET).")
    import json  # noqa: PLC0415
    import stripe  # noqa: PLC0415
    # construct_event VERIFIES the signature (raising on a bad/missing one). Its
    # return is a StripeObject whose `.get`/`dict()` are unreliable across SDK
    # versions, so — signature now verified — we parse the raw payload ourselves
    # into a plain nested dict the apply logic can use with `.get()`.
    stripe.Webhook.construct_event(payload, sig_header, secret)
    raw = payload.decode("utf-8") if isinstance(payload, (bytes, bytearray)) else payload
    return json.loads(raw)
