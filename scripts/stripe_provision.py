"""Provision Agenttic's Stripe products + prices (TEST mode) and a webhook.

Reads STRIPE_SECRET_KEY from the environment (never hardcoded, never printed).
Creates — idempotently, via stable idempotency keys, so re-runs don't duplicate —
the recurring subscription prices (Starter/Pro) and the one-off credit top-up
prices ($10/$50/$100), and optionally the webhook endpoint. Prints ONLY the
resulting non-secret ids (product/price ids, webhook endpoint id/url). The webhook
signing secret is a SECRET: it is returned once by Stripe on creation and written
to .env, never echoed to stdout.

Usage:
  python scripts/stripe_provision.py prices      # create products + prices, print ids
  python scripts/stripe_provision.py webhook URL  # create webhook, append whsec to .env
"""

from __future__ import annotations

import json
import os
import sys

import stripe

CURRENCY = "usd"
# Subscription plans -> recurring price (interval month). id-suffix drives the
# idempotency key so a re-run returns the same objects.
PLANS = [
    {"key": "starter", "name": "Agenttic Starter", "amount": 2900},
    {"key": "pro", "name": "Agenttic Pro", "amount": 9900},
]
# One-off credit top-ups -> one-time price under a single product.
TOPUPS = [
    {"key": "topup_10", "name": "$10 credit top-up", "amount": 1000},
    {"key": "topup_50", "name": "$50 credit top-up", "amount": 5000},
    {"key": "topup_100", "name": "$100 credit top-up", "amount": 10000},
]
WEBHOOK_EVENTS = [
    "checkout.session.completed",
    "invoice.paid",
    "customer.subscription.updated",
    "customer.subscription.deleted",
]


def _key() -> str:
    k = (os.environ.get("STRIPE_SECRET_KEY") or "").strip()
    if not k:
        sys.exit("STRIPE_SECRET_KEY is not set in the environment.")
    if not k.startswith("sk_test_"):
        sys.exit("Refusing to run: STRIPE_SECRET_KEY is not a TEST key (sk_test_…).")
    return k


def _product(name: str, ikey: str) -> str:
    p = stripe.Product.create(name=name, idempotency_key=f"agenttic-prod-{ikey}")
    return p["id"]


def _recurring_price(product: str, amount: int, ikey: str) -> str:
    pr = stripe.Price.create(
        product=product, currency=CURRENCY, unit_amount=amount,
        recurring={"interval": "month"},
        idempotency_key=f"agenttic-price-{ikey}")
    return pr["id"]


def _one_time_price(product: str, amount: int, name: str, ikey: str) -> str:
    pr = stripe.Price.create(
        product=product, currency=CURRENCY, unit_amount=amount,
        nickname=name, idempotency_key=f"agenttic-price-{ikey}")
    return pr["id"]


def provision_prices() -> dict:
    out: dict = {"plans": {}, "topups": {}}
    for plan in PLANS:
        prod = _product(plan["name"], plan["key"])
        price = _recurring_price(prod, plan["amount"], plan["key"])
        out["plans"][plan["key"]] = {"product": prod, "price": price,
                                     "amount_cents": plan["amount"]}
    topup_prod = _product("Agenttic Credit Top-up", "topups")
    for t in TOPUPS:
        price = _one_time_price(topup_prod, t["amount"], t["name"], t["key"])
        out["topups"][t["key"]] = {"product": topup_prod, "price": price,
                                   "amount_cents": t["amount"]}
    return out


def provision_webhook(url: str) -> dict:
    # Clean up any prior endpoint for the same URL (Stripe returns the signing
    # secret only at creation, so an orphan can't be recovered — delete + recreate).
    for ep in stripe.WebhookEndpoint.list(limit=100).auto_paging_iter():
        if ep["url"] == url:
            stripe.WebhookEndpoint.delete(ep["id"])
    ep = stripe.WebhookEndpoint.create(
        url=url, enabled_events=WEBHOOK_EVENTS, description="Agenttic billing")
    # ep["secret"] (whsec_…) is a SECRET — returned to the caller for writing to
    # .env, but NEVER printed to stdout here.
    secret = ep["secret"] if "secret" in ep else ""
    return {"id": ep["id"], "url": ep["url"], "events": WEBHOOK_EVENTS,
            "secret": secret}


if __name__ == "__main__":
    stripe.api_key = _key()
    cmd = sys.argv[1] if len(sys.argv) > 1 else "prices"
    if cmd == "prices":
        print(json.dumps(provision_prices(), indent=2))
    elif cmd == "webhook":
        if len(sys.argv) < 3:
            sys.exit("usage: stripe_provision.py webhook <url>")
        res = provision_webhook(sys.argv[2])
        secret = res.pop("secret", "")
        # write the signing secret to .env (append), never to stdout
        if secret:
            with open(".env", "a") as fh:
                fh.write(f"\n# Stripe webhook signing secret (created "
                         f"{res['id']})\nSTRIPE_WEBHOOK_SECRET={secret}\n")
            res["secret_written_to"] = ".env (STRIPE_WEBHOOK_SECRET)"
            res["secret_prefix"] = secret[:6] + "…"
        print(json.dumps(res, indent=2))
    else:
        sys.exit(f"unknown command: {cmd}")
