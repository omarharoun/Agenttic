"""High-level billing operations — the API the rest of the app calls.

Everything here takes a tenant ENGINE (+ tenant id) and the config, builds a
:class:`ascore.billing.store.BillingStore`, and applies the business rules:

* :func:`ensure_free_trial` — grant the one-time free-credit trial (idempotent);
  the config-driven "free credits on signup" that lets a user try tests + chat.
* :func:`meter_cost` — DEBIT credits for a billable action (copilot / cert / scan),
  converting the model's USD spend into credits with the platform markup.
* :func:`ensure_credits` / :func:`entitlement` — the pre-flight 402 gate: is this
  tenant allowed to spend right now? (balance > 0, or an active paid entitlement).
* :func:`account_summary` — the in-app dashboard payload (plan, balance, usage).

An out-of-credits error is surfaced as :class:`OutOfCreditsError`, which the
routes/seam turn into the existing HTTP 402 with an honest upgrade message.
"""

from __future__ import annotations

import logging

from ascore.billing import plans
from ascore.billing.store import BillingStore

log = logging.getLogger("ascore.billing")

OUT_OF_CREDITS_MESSAGE = (
    "You're out of credits — upgrade your plan or add a credit top-up to keep "
    "using the Copilot, scans, and certification.")


class OutOfCreditsError(RuntimeError):
    """Raised when a tenant has no credits/entitlement left. Callers map this to
    HTTP 402 with :data:`OUT_OF_CREDITS_MESSAGE`."""


def store_for(engine, tenant: str) -> BillingStore:
    return BillingStore(engine, tenant)


def ensure_free_trial(engine, tenant: str, cfg: dict) -> int:
    """Grant the one-time free-credit trial if this tenant has never had a ledger
    entry. Idempotent: the first ledger row is the trial, so we never re-grant.
    Returns the credits granted (0 if already granted or trial is 0)."""
    st = store_for(engine, tenant)
    if st.has_any_ledger():
        return 0
    amount = plans.free_trial_credits(cfg)
    if amount <= 0:
        return 0
    # ensure the tenant is on the free plan by default
    if st.get_subscription() is None:
        st.upsert_subscription(plan_id="free", status="trialing", provider="none")
    granted = st.grant(amount, "signup", dedup_key=f"free-trial:{tenant}",
                       meta={"note": "free trial credits"})
    return amount if granted else 0


def entitlement(engine, tenant: str, cfg: dict) -> dict:
    """Resolve whether the tenant may spend, granting the free trial on first
    sight. Returns ``{allowed, balance, reason, plan_id, status}``."""
    if not plans.is_enabled(cfg):
        # billing disabled → permissive free preview (still records usage).
        return {"allowed": True, "balance": None, "reason": "billing-disabled",
                "plan_id": "free", "status": "disabled"}
    ensure_free_trial(engine, tenant, cfg)
    st = store_for(engine, tenant)
    bal = st.balance()
    sub = st.get_subscription()
    plan_id = sub.plan_id if sub else "free"
    status = sub.status if sub else "trialing"
    allowed = bal > 0
    reason = "" if allowed else "out-of-credits"
    return {"allowed": allowed, "balance": bal, "reason": reason,
            "plan_id": plan_id, "status": status}


def ensure_credits(engine, tenant: str, cfg: dict) -> None:
    """Pre-flight gate. Raise :class:`OutOfCreditsError` when the tenant can't
    spend. No-op when billing is disabled or the tenant has credits."""
    ent = entitlement(engine, tenant, cfg)
    if not ent["allowed"]:
        raise OutOfCreditsError(OUT_OF_CREDITS_MESSAGE)


def meter_cost(engine, tenant: str, reason: str, cost_usd: float, *,
               model: str = "", cfg: dict | None = None,
               ref: str = "", meta: dict | None = None) -> int:
    """DEBIT credits for a billable action that spent ``cost_usd`` of model
    budget. Converts USD → credits with the platform markup and floors at
    ``min_action_credits``. Best-effort: never raises (a metering failure must not
    break the action). Returns the credits debited (0 on failure/disabled)."""
    if not plans.is_enabled(cfg):
        return 0
    try:
        credits = plans.usd_to_credits(cfg, cost_usd)
        if credits <= 0:
            return 0
        st = store_for(engine, tenant)
        entry_meta = {"cost_usd": round(float(cost_usd or 0.0), 6), "ref": ref}
        if meta:
            entry_meta.update(meta)
        return st.debit(credits, reason, model=model, meta=entry_meta)
    except Exception as exc:  # noqa: BLE001 — metering must never break the action
        log.warning("billing meter_cost failed (%s): %s", reason,
                    type(exc).__name__)
        return 0


def meter_tokens(engine, tenant: str, reason: str, model: str,
                 input_tokens: int, output_tokens: int, *,
                 cfg: dict | None = None) -> int:
    """DEBIT credits for a token-metered action (the Copilot chat). Prices the
    tokens via the pricing config, applies the markup, and debits. Best-effort."""
    from ascore.pricing import token_cost
    usd = token_cost(cfg or {}, model, input_tokens, output_tokens)
    return meter_cost(engine, tenant, reason, usd, model=model, cfg=cfg,
                      meta={"input_tokens": int(input_tokens or 0),
                            "output_tokens": int(output_tokens or 0)})


def account_summary(engine, tenant: str, cfg: dict) -> dict:
    """The in-app billing dashboard payload: plan, credit balance, usage
    breakdown, subscription status, and the current-period end. Grants the free
    trial on first view so a brand-new account already shows its free credits."""
    ensure_free_trial(engine, tenant, cfg)
    st = store_for(engine, tenant)
    sub = st.get_subscription()
    plan_id = sub.plan_id if sub else "free"
    plan_def = plans.plan(cfg, plan_id) or {}
    bal = st.balance()
    ccv = plans.credit_cent_value(cfg)
    return {
        "billing_enabled": plans.is_enabled(cfg),
        "currency": plans.currency(cfg),
        "credit_cent_value": ccv,
        "balance_credits": bal,
        "balance_cents": bal * ccv,
        "balance_display": plans.credits_to_usd_str(cfg, bal),
        "plan": {
            "id": plan_id,
            "name": plan_def.get("name", plan_id.title()),
            "price_cents": int(plan_def.get("price_cents", 0)),
            "interval": plan_def.get("interval", "month"),
            "included_credits": int(plan_def.get("included_credits", 0)),
        },
        "status": sub.status if sub else "trialing",
        "provider": sub.provider if sub else "none",
        "current_period_end": (sub.current_period_end.isoformat()
                               if sub and sub.current_period_end else None),
        "usage_by_reason": st.usage_by_reason(),
    }
