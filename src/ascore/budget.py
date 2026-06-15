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


def _budget(cfg: dict) -> dict:
    return cfg.get("budget", {}) or {}


def budget_context(cfg: dict, reg: Registry, projected_usd: float) -> dict:
    """Read-only view for the UI/estimate: caps, today's spend, and whether the
    projected run would breach either cap."""
    b = _budget(cfg)
    max_run = float(b.get("max_run_cost_usd", 0) or 0)
    max_daily = float(b.get("max_daily_cost_usd", 0) or 0)
    spent_today = reg.spend_today()
    return {
        "max_run_cost_usd": max_run,
        "max_daily_cost_usd": max_daily,
        "spent_today_usd": round(spent_today, 6),
        "projected_usd": round(projected_usd, 6),
        "would_exceed_run": bool(max_run and projected_usd > max_run),
        "would_exceed_daily": bool(max_daily and spent_today + projected_usd > max_daily),
        "warn_only": bool(b.get("warn_only", False)),
    }


def check_pre_run(cfg: dict, reg: Registry, projected_usd: float) -> list[str]:
    """Raise BudgetExceededError if the estimate breaches a cap (unless
    warn_only). Returns warning strings (empty when clear)."""
    ctx = budget_context(cfg, reg, projected_usd)
    warnings: list[str] = []
    if ctx["would_exceed_run"]:
        warnings.append(
            f"projected ${projected_usd:.4f} exceeds per-run cap "
            f"${ctx['max_run_cost_usd']:.4f}")
    if ctx["would_exceed_daily"]:
        warnings.append(
            f"projected ${projected_usd:.4f} + today's ${ctx['spent_today_usd']:.4f} "
            f"exceeds daily cap ${ctx['max_daily_cost_usd']:.4f}")
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
