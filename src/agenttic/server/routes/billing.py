"""Billing HTTP surface.

Three routers:
* ``router`` (PROTECTED, tenant-scoped) — the in-app billing dashboard + the
  authenticated checkout-session creation. Every read/write is scoped to the
  caller's tenant (``request.state.tenant`` / ``request.state.reg.engine``), so a
  user only ever sees and acts on their OWN billing.
* ``public_router`` (UNAUTHENTICATED) — the public pricing catalog for the
  landing/pricing page (plans + free-credit offer; no tenant data).
* ``webhook_router`` (UNAUTHENTICATED, SIGNATURE-VERIFIED) — the Stripe + PayPal
  webhooks. Verified against the provider signing secret, resolved to a tenant,
  and applied IDEMPOTENTLY (a replay can't double-credit).

Money is integer cents throughout; secrets (provider keys) never leave the
server and are read only from the environment.
"""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

from agenttic.billing import plans, service
from agenttic.billing.gateways import paypal_gateway, stripe_gateway
from agenttic.billing.invoices import render_invoice_html
from agenttic.billing.store import BillingStore, GlobalBillingStore

log = logging.getLogger("agenttic.billing")

router = APIRouter(tags=["billing"], prefix="/billing")
public_router = APIRouter(tags=["billing-public"])
webhook_router = APIRouter(tags=["billing-webhooks"], prefix="/billing/webhooks")


# --------------------------------------------------------------------------- #
# Helpers.
# --------------------------------------------------------------------------- #

def _tenant(request: Request) -> str:
    return getattr(request.state, "tenant", "default")


def _engine(request: Request):
    return request.state.reg.engine


def _cfg(request: Request) -> dict:
    return getattr(request.state, "cfg", None) or {}


def _store(request: Request) -> BillingStore:
    return BillingStore(_engine(request), _tenant(request))


def _base_url(request: Request) -> str:
    return str(request.base_url).rstrip("/")


# --------------------------------------------------------------------------- #
# In-app billing dashboard (protected).
# --------------------------------------------------------------------------- #

@router.get("")
def billing_overview(request: Request):
    """Current plan, credit balance + usage breakdown, and billing status."""
    return service.account_summary(_engine(request), _tenant(request), _cfg(request))


@router.get("/ledger")
def billing_ledger(request: Request, limit: int = 50):
    """Recent credit ledger entries (grants + debits), newest first."""
    service.ensure_free_trial(_engine(request), _tenant(request), _cfg(request))
    return {"entries": _store(request).ledger(limit=min(max(limit, 1), 500))}


@router.get("/plans")
def billing_plans(request: Request):
    """The plan catalog + top-ups, with which payment providers are live."""
    return _plans_payload(_cfg(request))


@router.get("/invoices")
def billing_invoices(request: Request):
    return {"invoices": _store(request).list_invoices()}


@router.get("/invoices/{invoice_id}")
def billing_invoice(invoice_id: str, request: Request):
    inv = _store(request).get_invoice(invoice_id)
    if inv is None:
        raise HTTPException(404, "invoice not found")
    return inv


@router.get("/invoices/{invoice_id}/download")
def billing_invoice_download(invoice_id: str, request: Request):
    """The invoice as a standalone, printable HTML document (browser → PDF)."""
    inv = _store(request).get_invoice(invoice_id)
    if inv is None:
        raise HTTPException(404, "invoice not found")
    html = render_invoice_html(inv, tenant=_tenant(request),
                               currency=plans.currency(_cfg(request)))
    return HTMLResponse(
        html, headers={"Content-Disposition":
                       f'inline; filename="invoice-{inv["number"]}.html"'})


class CheckoutBody(BaseModel):
    kind: str = "subscription"       # subscription | topup
    plan_id: str | None = None
    topup_id: str | None = None


@router.get("/config")
def billing_config(request: Request):
    """Which providers are configured (so the UI shows only live options)."""
    return {
        "stripe": {"configured": stripe_gateway.is_configured(),
                   "test_mode": stripe_gateway.is_test_mode(),
                   # publishable key is NOT secret — safe for the client
                   "publishable_key": stripe_gateway.publishable_key()},
        "paypal": {"configured": paypal_gateway.is_configured(),
                   "sandbox": paypal_gateway.is_sandbox()},
    }


@router.post("/checkout/stripe")
def checkout_stripe(body: CheckoutBody, request: Request):
    """Create a Stripe Checkout Session for a subscription or a credit top-up and
    return its redirect URL. 503 until STRIPE_SECRET_KEY is set."""
    if not stripe_gateway.is_configured():
        raise HTTPException(503, "Stripe isn't configured on this server "
                                 "(set STRIPE_SECRET_KEY to enable card checkout).")
    cfg, tenant, base = _cfg(request), _tenant(request), _base_url(request)
    success = f"{base}/app/billing?checkout=success"
    cancel = f"{base}/app/billing?checkout=cancel"
    currency = plans.currency(cfg)
    try:
        if body.kind == "topup":
            tp = plans.topup(cfg, body.topup_id or "")
            if not tp:
                raise HTTPException(422, "unknown top-up")
            # Prefer the real Stripe price id when configured; else build the line
            # item inline (dev/first-run before prices are provisioned).
            if tp.get("stripe_price_id"):
                line_items = [{"price": tp["stripe_price_id"], "quantity": 1}]
            else:
                line_items = [{"price_data": {"currency": currency,
                    "product_data": {"name": tp["name"]},
                    "unit_amount": int(tp["price_cents"])}, "quantity": 1}]
            session = stripe_gateway.create_checkout_session(
                tenant=tenant, mode="payment", success_url=success,
                cancel_url=cancel, line_items=line_items,
                metadata={"topup_id": tp["id"], "credits": str(tp["credits"]),
                          "description": tp["name"]})
        else:
            p = plans.plan(cfg, body.plan_id or "")
            if not p or int(p.get("price_cents", 0)) <= 0:
                raise HTTPException(422, "unknown or non-purchasable plan")
            if p.get("stripe_price_id"):
                line_items = [{"price": p["stripe_price_id"], "quantity": 1}]
            else:
                line_items = [{"price_data": {"currency": currency,
                    "product_data": {"name": f"Agenttic {p['name']}"},
                    "unit_amount": int(p["price_cents"]),
                    "recurring": {"interval": p.get("interval", "month")}},
                    "quantity": 1}]
            session = stripe_gateway.create_checkout_session(
                tenant=tenant, mode="subscription", success_url=success,
                cancel_url=cancel, line_items=line_items,
                metadata={"plan_id": body.plan_id})
        return {"url": session["url"], "id": session["id"]}
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 — never leak provider internals
        log.error("stripe checkout failed: %s", type(exc).__name__)
        raise HTTPException(502, "Couldn't start Stripe checkout — please retry.")


@router.post("/checkout/paypal")
def checkout_paypal(body: CheckoutBody, request: Request):
    """Create a PayPal order (top-up) or subscription and return the approval URL.
    503 until PAYPAL_CLIENT_ID/SECRET are set."""
    if not paypal_gateway.is_configured():
        raise HTTPException(503, "PayPal isn't configured on this server "
                                 "(set PAYPAL_CLIENT_ID / PAYPAL_SECRET).")
    cfg, tenant, base = _cfg(request), _tenant(request), _base_url(request)
    ret = f"{base}/app/billing?checkout=success"
    cancel = f"{base}/app/billing?checkout=cancel"
    currency = plans.currency(cfg)
    try:
        if body.kind == "topup":
            tp = plans.topup(cfg, body.topup_id or "")
            if not tp:
                raise HTTPException(422, "unknown top-up")
            order = paypal_gateway.create_order(
                tenant=tenant, amount_cents=int(tp["price_cents"]),
                currency=currency, description=tp["name"], return_url=ret,
                cancel_url=cancel, reference=tp["id"])
            return {"url": order["approve_url"], "id": order["id"]}
        p = plans.plan(cfg, body.plan_id or "")
        if not p or not p.get("paypal_plan_id"):
            raise HTTPException(422, "This plan isn't available via PayPal "
                                     "(no PayPal plan id configured).")
        sub = paypal_gateway.create_subscription(
            tenant=tenant, plan_external_id=p["paypal_plan_id"],
            return_url=ret, cancel_url=cancel)
        return {"url": sub["approve_url"], "id": sub["id"]}
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        log.error("paypal checkout failed: %s", type(exc).__name__)
        raise HTTPException(502, "Couldn't start PayPal checkout — please retry.")


# --------------------------------------------------------------------------- #
# Public pricing (unauthenticated) — powers the landing/pricing page.
# --------------------------------------------------------------------------- #

def _plans_payload(cfg: dict) -> dict:
    plist = []
    for pid, p in plans.plans(cfg).items():
        plist.append({
            "id": pid, "name": p.get("name", pid.title()),
            "price_cents": int(p.get("price_cents", 0)),
            "interval": p.get("interval", "month"),
            "included_credits": int(p.get("included_credits", 0)),
            "features": list(p.get("features", [])),
            "highlight": bool(p.get("highlight", False)),
        })
    return {
        "currency": plans.currency(cfg),
        "free_trial_credits": plans.free_trial_credits(cfg),
        "credit_cent_value": plans.credit_cent_value(cfg),
        "plans": plist,
        "topups": plans.topups(cfg),
        # publishable key is NOT secret — exposed so the client can init Stripe.js
        "stripe_publishable_key": stripe_gateway.publishable_key(),
    }


@public_router.get("/pricing")
def public_pricing(request: Request):
    """Public plan catalog + free-credit offer for the pricing page. No tenant
    data, safe to serve unauthenticated."""
    return _plans_payload(request.app.state.cfg or {})


# --------------------------------------------------------------------------- #
# Webhooks (unauthenticated, signature-verified, idempotent).
# --------------------------------------------------------------------------- #

def _resolve_engine(request: Request):
    """Tenant → engine resolver backed by the app's Workspaces (same mechanism
    the rest of the app uses to reach a tenant's isolated store)."""
    workspaces = request.app.state.workspaces

    def resolve(tenant: str):
        return workspaces.get(tenant).reg.engine
    return resolve


def _global_store(request: Request) -> GlobalBillingStore:
    # GLOBAL billing tables live in the default engine, like users/certifications.
    return GlobalBillingStore(request.app.state.reg.engine)


@webhook_router.post("/stripe")
async def stripe_webhook(request: Request):
    """Stripe webhook: verify signature → apply idempotently. Handles
    checkout.session.completed, invoice.paid, subscription.updated/deleted."""
    payload = await request.body()
    sig = request.headers.get("stripe-signature", "")
    try:
        event = stripe_gateway.construct_event(payload, sig)
    except Exception as exc:  # noqa: BLE001 — bad signature / not configured
        log.warning("stripe webhook rejected: %s", type(exc).__name__)
        raise HTTPException(400, "invalid Stripe signature")
    from agenttic.billing.webhooks import apply_stripe_event
    try:
        result = apply_stripe_event(
            event, resolve_engine=_resolve_engine(request),
            global_store=_global_store(request), cfg=request.app.state.cfg or {})
    except Exception as exc:  # noqa: BLE001
        log.error("stripe webhook apply failed: %s", type(exc).__name__)
        raise HTTPException(500, "webhook processing error")
    return JSONResponse(result)


@webhook_router.post("/paypal")
async def paypal_webhook(request: Request):
    """PayPal webhook: verify signature (unless verification is unconfigured in a
    sandbox/test) → apply idempotently."""
    raw = await request.body()
    try:
        body = json.loads(raw or b"{}")
    except json.JSONDecodeError:
        raise HTTPException(400, "invalid JSON")
    # Verify the signature when a webhook id is configured; refuse otherwise in
    # production. (Verification requires PAYPAL_WEBHOOK_ID.)
    if paypal_gateway.webhook_id():
        try:
            ok = paypal_gateway.verify_webhook(
                headers={k.lower(): v for k, v in request.headers.items()},
                body=body)
        except Exception as exc:  # noqa: BLE001
            log.warning("paypal webhook verify error: %s", type(exc).__name__)
            raise HTTPException(400, "signature verification failed")
        if not ok:
            raise HTTPException(400, "invalid PayPal signature")
    else:
        log.warning("paypal webhook received but PAYPAL_WEBHOOK_ID is unset — "
                    "processing UNVERIFIED (set it before going live)")
    from agenttic.billing.webhooks import apply_paypal_event
    try:
        result = apply_paypal_event(
            body, resolve_engine=_resolve_engine(request),
            global_store=_global_store(request), cfg=request.app.state.cfg or {})
    except Exception as exc:  # noqa: BLE001
        log.error("paypal webhook apply failed: %s", type(exc).__name__)
        raise HTTPException(500, "webhook processing error")
    return JSONResponse(result)
