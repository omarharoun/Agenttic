"""Provider-agnostic, IDEMPOTENT webhook application.

The route layer verifies the signature and hands a normalised event dict here.
These functions:

1. Skip an already-processed event (global idempotency log) — a replay is a no-op.
2. Resolve the Agenttic tenant (from event metadata, else the global customer map).
3. Apply the effect against that tenant's ledger/subscription/invoices, with a
   per-event ``dedup_key`` on the grant so even a concurrent double-delivery can't
   double-credit.
4. Mark the event processed.

Split out from the HTTP layer so the whole apply path is unit-testable with plain
dicts (no Stripe/PayPal SDK, no HTTP) — see ``tests/test_billing*.py``.
"""

from __future__ import annotations

import logging

from agenttic.billing import plans
from agenttic.billing.store import BillingStore, GlobalBillingStore

log = logging.getLogger("agenttic.billing")


def _grant_and_invoice(store: BillingStore, cfg: dict, *, tenant: str,
                       credits: int, amount_cents: int, description: str,
                       provider: str, external_id: str, dedup_key: str,
                       plan_id: str | None = None) -> dict:
    """Grant credits (idempotent by ``dedup_key``) and, when a payment actually
    moved, write the matching invoice. Returns a small result dict."""
    granted = store.grant(credits, plan_id and "subscription" or "topup",
                          dedup_key=dedup_key,
                          meta={"provider": provider, "external_id": external_id})
    invoice = None
    if amount_cents > 0:
        invoice = store.create_invoice(
            description=description,
            line_items=[{"description": description, "quantity": 1,
                         "unit_cents": amount_cents}],
            provider=provider, external_id=external_id, status="paid",
            currency=plans.currency(cfg), credits_granted=credits,
            dedup_key=dedup_key)
    return {"granted": granted, "credits": credits,
            "invoice": invoice.get("number") if invoice else None}


def apply_stripe_event(event: dict, *, resolve_engine, global_store: GlobalBillingStore,
                       cfg: dict) -> dict:
    """Apply a Stripe webhook event. Handled types:
    ``checkout.session.completed`` (subscription start OR one-off top-up),
    ``invoice.paid`` (recurring renewal),
    ``customer.subscription.updated`` / ``customer.subscription.deleted``."""
    etype = event.get("type", "")
    event_id = event.get("id", "")
    obj = (event.get("data", {}) or {}).get("object", {}) or {}

    if event_id and global_store.already_processed("stripe", event_id):
        return {"status": "duplicate", "event_id": event_id}

    result: dict = {"status": "ignored", "type": etype, "event_id": event_id}

    if etype == "checkout.session.completed":
        md = obj.get("metadata", {}) or {}
        tenant = md.get("tenant") or obj.get("client_reference_id")
        if tenant:
            engine = resolve_engine(tenant)
            store = BillingStore(engine, tenant)
            # remember the customer + subscription → tenant for future events
            for ext in (obj.get("customer"), obj.get("subscription")):
                if ext:
                    global_store.map_customer("stripe", str(ext), tenant)
            amount = int(obj.get("amount_total") or 0)
            if obj.get("mode") == "subscription":
                plan_id = md.get("plan_id", "starter")
                credits = _plan_credits(cfg, plan_id)
                store.upsert_subscription(
                    plan_id=plan_id, status="active", provider="stripe",
                    external_id=str(obj.get("subscription") or ""))
                res = _grant_and_invoice(
                    store, cfg, tenant=tenant, credits=credits, amount_cents=amount,
                    description=f"{_plan_name(cfg, plan_id)} subscription",
                    provider="stripe", external_id=str(obj.get("id") or ""),
                    dedup_key=f"stripe:{event_id}", plan_id=plan_id)
            else:  # one-off top-up
                credits = int(md.get("credits") or 0)
                res = _grant_and_invoice(
                    store, cfg, tenant=tenant, credits=credits, amount_cents=amount,
                    description=md.get("description", "Credit top-up"),
                    provider="stripe", external_id=str(obj.get("id") or ""),
                    dedup_key=f"stripe:{event_id}")
            result = {"status": "applied", "type": etype, "tenant": tenant, **res}

    elif etype == "invoice.paid":
        sub_id = str(obj.get("subscription") or "")
        cust_id = str(obj.get("customer") or "")
        tenant = (global_store.tenant_for_customer("stripe", sub_id)
                  or global_store.tenant_for_customer("stripe", cust_id))
        if tenant:
            engine = resolve_engine(tenant)
            store = BillingStore(engine, tenant)
            sub = store.get_subscription()
            plan_id = sub.plan_id if sub else "starter"
            credits = _plan_credits(cfg, plan_id)
            amount = int(obj.get("amount_paid") or obj.get("amount_due") or 0)
            res = _grant_and_invoice(
                store, cfg, tenant=tenant, credits=credits, amount_cents=amount,
                description=f"{_plan_name(cfg, plan_id)} renewal",
                provider="stripe", external_id=str(obj.get("id") or ""),
                dedup_key=f"stripe:{event_id}", plan_id=plan_id)
            result = {"status": "applied", "type": etype, "tenant": tenant, **res}

    elif etype in ("customer.subscription.updated", "customer.subscription.deleted"):
        sub_id = str(obj.get("id") or "")
        tenant = (global_store.tenant_for_customer("stripe", sub_id)
                  or global_store.tenant_for_customer(
                      "stripe", str(obj.get("customer") or "")))
        if tenant:
            store = BillingStore(resolve_engine(tenant), tenant)
            if etype.endswith("deleted") or obj.get("status") == "canceled":
                store.upsert_subscription(plan_id="free", status="canceled",
                                          provider="stripe", external_id=sub_id)
            else:
                md = obj.get("metadata", {}) or {}
                plan_id = md.get("plan_id") or (
                    store.get_subscription().plan_id if store.get_subscription()
                    else "starter")
                store.upsert_subscription(
                    plan_id=plan_id, status=str(obj.get("status") or "active"),
                    provider="stripe", external_id=sub_id)
            result = {"status": "applied", "type": etype, "tenant": tenant}

    if event_id:
        global_store.mark_processed("stripe", event_id, etype,
                                    result.get("tenant", ""))
    return result


def apply_paypal_event(event: dict, *, resolve_engine, global_store: GlobalBillingStore,
                       cfg: dict) -> dict:
    """Apply a PayPal webhook event. Handled types:
    ``PAYMENT.CAPTURE.COMPLETED`` / ``CHECKOUT.ORDER.APPROVED`` (top-up),
    ``BILLING.SUBSCRIPTION.ACTIVATED`` / ``BILLING.SUBSCRIPTION.CANCELLED``."""
    etype = event.get("event_type", "")
    event_id = event.get("id", "")
    res_obj = (event.get("resource", {}) or {})

    if event_id and global_store.already_processed("paypal", event_id):
        return {"status": "duplicate", "event_id": event_id}

    result: dict = {"status": "ignored", "type": etype, "event_id": event_id}

    if etype in ("PAYMENT.CAPTURE.COMPLETED", "CHECKOUT.ORDER.APPROVED"):
        tenant, reference = _paypal_custom(res_obj)
        if tenant:
            store = BillingStore(resolve_engine(tenant), tenant)
            amount_cents = _paypal_amount_cents(res_obj)
            # credits: from the top-up config keyed by reference, else 1:1 with cents
            tp = plans.topup(cfg, reference) if reference else None
            credits = int(tp["credits"]) if tp else amount_cents
            res = _grant_and_invoice(
                store, cfg, tenant=tenant, credits=credits, amount_cents=amount_cents,
                description=(tp or {}).get("name", "Credit top-up"),
                provider="paypal", external_id=str(res_obj.get("id") or ""),
                dedup_key=f"paypal:{event_id}")
            result = {"status": "applied", "type": etype, "tenant": tenant, **res}

    elif etype == "BILLING.SUBSCRIPTION.ACTIVATED":
        tenant = res_obj.get("custom_id")
        if tenant:
            store = BillingStore(resolve_engine(tenant), tenant)
            plan_id = _paypal_plan_id(cfg, res_obj)
            credits = _plan_credits(cfg, plan_id)
            store.upsert_subscription(
                plan_id=plan_id, status="active", provider="paypal",
                external_id=str(res_obj.get("id") or ""))
            global_store.map_customer("paypal", str(res_obj.get("id") or ""), tenant)
            res = _grant_and_invoice(
                store, cfg, tenant=tenant, credits=credits, amount_cents=0,
                description=f"{_plan_name(cfg, plan_id)} subscription",
                provider="paypal", external_id=str(res_obj.get("id") or ""),
                dedup_key=f"paypal:{event_id}", plan_id=plan_id)
            result = {"status": "applied", "type": etype, "tenant": tenant, **res}

    elif etype in ("BILLING.SUBSCRIPTION.CANCELLED", "BILLING.SUBSCRIPTION.EXPIRED"):
        sub_id = str(res_obj.get("id") or "")
        tenant = (res_obj.get("custom_id")
                  or global_store.tenant_for_customer("paypal", sub_id))
        if tenant:
            store = BillingStore(resolve_engine(tenant), tenant)
            store.upsert_subscription(plan_id="free", status="canceled",
                                      provider="paypal", external_id=sub_id)
            result = {"status": "applied", "type": etype, "tenant": tenant}

    if event_id:
        global_store.mark_processed("paypal", event_id, etype,
                                    result.get("tenant", ""))
    return result


# -- helpers ---------------------------------------------------------------- #

def _plan_credits(cfg: dict, plan_id: str) -> int:
    p = plans.plan(cfg, plan_id) or {}
    return int(p.get("included_credits", 0))


def _plan_name(cfg: dict, plan_id: str) -> str:
    p = plans.plan(cfg, plan_id) or {}
    return str(p.get("name", plan_id.title()))


def _paypal_custom(res_obj: dict) -> tuple[str | None, str]:
    """Extract (tenant, reference) from a PayPal capture/order resource. We stamp
    ``custom_id = "tenant|reference"`` on the purchase unit at creation."""
    custom = res_obj.get("custom_id")
    if not custom:
        pus = res_obj.get("purchase_units") or []
        if pus:
            custom = pus[0].get("custom_id")
    if not custom:
        return None, ""
    parts = str(custom).split("|", 1)
    return parts[0], (parts[1] if len(parts) > 1 else "")


def _paypal_amount_cents(res_obj: dict) -> int:
    amt = res_obj.get("amount") or {}
    if not amt:
        pus = res_obj.get("purchase_units") or []
        if pus:
            amt = pus[0].get("amount", {})
    value = amt.get("value") if isinstance(amt, dict) else None
    try:
        return round(float(value) * 100) if value is not None else 0
    except (TypeError, ValueError):
        return 0


def _paypal_plan_id(cfg: dict, res_obj: dict) -> str:
    """Map a PayPal plan external id back to our plan id via config, else the
    first paid plan."""
    ext = res_obj.get("plan_id")
    for pid, p in plans.plans(cfg).items():
        if p.get("paypal_plan_id") and p.get("paypal_plan_id") == ext:
            return pid
    for pid, p in plans.plans(cfg).items():
        if int(p.get("price_cents", 0)) > 0:
            return pid
    return "starter"
