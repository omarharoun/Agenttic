"""Spend ceilings — turn the cost estimate and live spend into a go/no-go.

Two caps, both from ``config.yaml`` ``budget`` (0 = unlimited):
* ``max_run_cost_usd`` — a single run may not exceed this.
* ``max_daily_cost_usd`` — today's recorded spend + this run may not exceed this.

``check_pre_run`` is the gate called before a run starts: it compares the
estimate against both caps and raises ``BudgetExceededError`` (unless
``budget.warn_only``). ``RunBudget`` is the runtime accumulator the harness
charges as each case completes, so an under-estimated run still aborts cleanly
once actual spend crosses the per-run cap.
"""

from __future__ import annotations

from dataclasses import dataclass

from ascore.registry.sqlite_store import Registry


class BudgetExceededError(RuntimeError):
    """A run would exceed (or has exceeded) a configured spend cap."""


_MONTH_WINDOW_DAYS = 29  # rolling 30-day window (today + previous 29)


def _budget(cfg: dict) -> dict:
    return cfg.get("budget", {}) or {}


def tenant_quota(cfg: dict, tenant: str) -> dict:
    """Per-tenant spend quota (daily/monthly USD; 0 = unlimited). Resolved from
    ``quotas.tiers[<tenant>]`` falling back to ``quotas.default``."""
    q = cfg.get("quotas", {}) or {}
    default = q.get("default", {}) or {}
    tier = (q.get("tiers", {}) or {}).get(tenant, default)
    return {
        "daily_usd": float(tier.get("daily_usd", default.get("daily_usd", 0)) or 0),
        "monthly_usd": float(tier.get("monthly_usd",
                                      default.get("monthly_usd", 0)) or 0),
    }


def budget_context(cfg: dict, reg: Registry, projected_usd: float) -> dict:
    """Read-only view for the UI/estimate: per-run cap, global daily cap, the
    tenant's daily/monthly quota, current spend, and which caps the projected
    run would breach."""
    b = _budget(cfg)
    max_run = float(b.get("max_run_cost_usd", 0) or 0)
    max_daily = float(b.get("max_daily_cost_usd", 0) or 0)
    quota = tenant_quota(cfg, getattr(reg, "tenant", "default"))
    spent_today = reg.spend_today()
    spent_month = reg.spend_since_days(_MONTH_WINDOW_DAYS)
    return {
        "tenant": getattr(reg, "tenant", "default"),
        "max_run_cost_usd": max_run,
        "max_daily_cost_usd": max_daily,
        "quota_daily_usd": quota["daily_usd"],
        "quota_monthly_usd": quota["monthly_usd"],
        "spent_today_usd": round(spent_today, 6),
        "spent_month_usd": round(spent_month, 6),
        "projected_usd": round(projected_usd, 6),
        "would_exceed_run": bool(max_run and projected_usd > max_run),
        "would_exceed_daily": bool(max_daily and spent_today + projected_usd > max_daily),
        "would_exceed_quota_daily": bool(
            quota["daily_usd"] and spent_today + projected_usd > quota["daily_usd"]),
        "would_exceed_quota_monthly": bool(
            quota["monthly_usd"] and spent_month + projected_usd > quota["monthly_usd"]),
        "warn_only": bool(b.get("warn_only", False)),
    }


def check_pre_run(cfg: dict, reg: Registry, projected_usd: float) -> list[str]:
    """Raise BudgetExceededError if the estimate breaches the per-run cap, the
    global daily cap, or the tenant's daily/monthly quota (unless warn_only).
    Returns warning strings (empty when clear)."""
    ctx = budget_context(cfg, reg, projected_usd)
    p = f"${projected_usd:.4f}"
    warnings: list[str] = []
    if ctx["would_exceed_run"]:
        warnings.append(f"projected {p} exceeds per-run cap "
                        f"${ctx['max_run_cost_usd']:.4f}")
    if ctx["would_exceed_daily"]:
        warnings.append(f"projected {p} + today's ${ctx['spent_today_usd']:.4f} "
                        f"exceeds daily cap ${ctx['max_daily_cost_usd']:.4f}")
    if ctx["would_exceed_quota_daily"]:
        warnings.append(f"projected {p} + today's ${ctx['spent_today_usd']:.4f} "
                        f"exceeds tenant '{ctx['tenant']}' daily quota "
                        f"${ctx['quota_daily_usd']:.4f}")
    if ctx["would_exceed_quota_monthly"]:
        warnings.append(f"projected {p} + 30d ${ctx['spent_month_usd']:.4f} "
                        f"exceeds tenant '{ctx['tenant']}' monthly quota "
                        f"${ctx['quota_monthly_usd']:.4f}")
    if warnings and not ctx["warn_only"]:
        raise BudgetExceededError("; ".join(warnings))
    return warnings


@dataclass
class RunBudget:
    """Runtime spend accumulator for one run. Thread-safety isn't needed: the
    harness charges from the event loop, not worker threads."""
    max_run_usd: float = 0.0
    spent_usd: float = 0.0

    def charge(self, amount: float | None) -> None:
        self.spent_usd += amount or 0.0

    @property
    def exhausted(self) -> bool:
        return bool(self.max_run_usd) and self.spent_usd >= self.max_run_usd
