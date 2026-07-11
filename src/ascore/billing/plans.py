"""Plan / tier + credit configuration, read from ``config.yaml`` ``billing``
(Hard Rule 7: no hardcoded prices/plans in code). Everything here is a pure read
over the config dict with sane built-in defaults, so the module is safe to import
in the public bundle-free backend without side effects.

Credit unit: **1 credit == 1 US cent.** ``credit_cent_value`` is the cents-per-
credit and is 1 by default; it exists only so the money math has a single, named
conversion point. All amounts crossing a money boundary are integer cents.
"""

from __future__ import annotations

import math

# Built-in defaults so billing is coherent even if the config omits the block.
_DEFAULT_FREE_TRIAL_CREDITS = 500          # $5.00 of free usage on signup
_DEFAULT_MARKUP = 1.5                       # platform fee over metered model cost
_DEFAULT_MIN_ACTION_CREDITS = 1             # floor any billable action at 1 credit
_DEFAULT_CREDIT_CENT_VALUE = 1              # 1 credit == 1 cent

_DEFAULT_PLANS: dict[str, dict] = {
    "free": {
        "name": "Free trial",
        "price_cents": 0,
        "interval": "once",
        "included_credits": _DEFAULT_FREE_TRIAL_CREDITS,
        "features": [
            "$5.00 in free credits",
            "Copilot chat + agent tools",
            "Scan & certify your agent",
            "Community support",
        ],
    },
    "starter": {
        "name": "Starter",
        "price_cents": 2900,
        "interval": "month",
        "included_credits": 5000,          # $50 of usage / mo
        "features": [
            "$50 in monthly credits",
            "Everything in Free",
            "Custom invoices",
            "Email support",
        ],
    },
    "pro": {
        "name": "Pro",
        "price_cents": 9900,
        "interval": "month",
        "included_credits": 20000,         # $200 of usage / mo
        "highlight": True,
        "features": [
            "$200 in monthly credits",
            "Everything in Starter",
            "Priority scans & certification",
            "Priority support",
        ],
    },
}

_DEFAULT_TOPUPS: list[dict] = [
    {"id": "topup_10", "name": "$10 credit top-up", "price_cents": 1000, "credits": 1000},
    {"id": "topup_50", "name": "$50 credit top-up", "price_cents": 5000, "credits": 5000},
    {"id": "topup_100", "name": "$100 credit top-up", "price_cents": 10000, "credits": 10000},
]


def billing_cfg(cfg: dict | None) -> dict:
    return (cfg or {}).get("billing", {}) or {}


def is_enabled(cfg: dict | None) -> bool:
    """Billing is ON unless explicitly disabled. When OFF the credits gate stays
    permissive (free preview) — the seam still records usage but never refuses."""
    return bool(billing_cfg(cfg).get("enabled", True))


def credit_cent_value(cfg: dict | None) -> int:
    return int(billing_cfg(cfg).get("credit_cent_value", _DEFAULT_CREDIT_CENT_VALUE) or 1)


def free_trial_credits(cfg: dict | None) -> int:
    return int(billing_cfg(cfg).get("free_trial_credits", _DEFAULT_FREE_TRIAL_CREDITS))


def markup_multiplier(cfg: dict | None) -> float:
    return float(billing_cfg(cfg).get("markup_multiplier", _DEFAULT_MARKUP) or 1.0)


def min_action_credits(cfg: dict | None) -> int:
    return int(billing_cfg(cfg).get("min_action_credits", _DEFAULT_MIN_ACTION_CREDITS))


def currency(cfg: dict | None) -> str:
    return str(billing_cfg(cfg).get("currency", "usd") or "usd")


def plans(cfg: dict | None) -> dict[str, dict]:
    """The configured plan catalog (id → plan dict), or the built-in default."""
    return dict(billing_cfg(cfg).get("plans") or _DEFAULT_PLANS)


def plan(cfg: dict | None, plan_id: str) -> dict | None:
    return plans(cfg).get(plan_id)


def topups(cfg: dict | None) -> list[dict]:
    return list(billing_cfg(cfg).get("topups") or _DEFAULT_TOPUPS)


def topup(cfg: dict | None, topup_id: str) -> dict | None:
    for t in topups(cfg):
        if t.get("id") == topup_id:
            return t
    return None


def usd_to_credits(cfg: dict | None, usd: float, *, markup: bool = True) -> int:
    """Convert a USD spend into credits (== cents), applying the platform markup
    and flooring at ``min_action_credits`` (a metered action always costs ≥1). We
    round UP so a sliver of spend never costs 0 credits."""
    cents = float(usd or 0.0) * 100.0
    if markup:
        cents *= markup_multiplier(cfg)
    credits = math.ceil(cents / credit_cent_value(cfg))
    return max(credits, min_action_credits(cfg))


def credits_to_usd_str(cfg: dict | None, credits: int) -> str:
    """Human dollar string for a credit amount (for UI/invoices)."""
    cents = int(credits) * credit_cent_value(cfg)
    return f"${cents / 100:.2f}"
