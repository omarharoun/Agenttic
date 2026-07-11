"""Credits / usage seam for the Copilot — the integration point for the upcoming
billing system.

v1 is a **stub**: :func:`check_credits` always returns ``allowed`` so the Copilot
is free to use today, and :func:`record_usage` logs token counts (never message
content) for future accounting. But the *shape* is the real one: a per-tenant
gate called BEFORE the model runs, and a usage record written AFTER, keyed by
tenant. When billing lands (platform fee + free credits to try tests & chat,
Stripe + PayPal, subscriptions & invoices — see docs/COPILOT.md), the real
free-credit accounting drops in behind :class:`CreditsProvider` with no change to
the endpoint or the frontend.

Nothing here stores conversation content. We keep only what billing needs:
tenant, timestamp, model, and token counts.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from datetime import date, datetime, timezone

log = logging.getLogger("ascore.copilot.usage")


@dataclass(frozen=True)
class CreditDecision:
    """Outcome of a pre-flight credit check."""
    allowed: bool
    reason: str = ""
    #: Remaining free credits (None while billing is not yet wired).
    remaining: int | None = None


@dataclass(frozen=True)
class UsageRecord:
    """What we persist for billing — counts + executed-action metadata only,
    never message content. ``action`` names an executed write/cost tool (e.g.
    ``start_certification``) when this record is for an action rather than a chat
    turn; ``cost_usd`` is its (optional) estimated/known spend."""
    tenant: str
    model: str
    input_tokens: int
    output_tokens: int
    at: datetime
    action: str | None = None
    cost_usd: float = 0.0


class CreditsProvider:
    """The billing seam. The default implementation is a permissive stub.

    Swap this out (or subclass) when the billing system is ready: implement real
    free-credit accounting in :meth:`check` and durable metering in
    :meth:`record`. The endpoint depends only on this interface.
    """

    def check(self, tenant: str) -> CreditDecision:
        """Called BEFORE the model runs. Return ``allowed=False`` to refuse (the
        endpoint turns that into an HTTP 402 with the reason). The stub always
        allows."""
        return CreditDecision(allowed=True, reason="free-preview", remaining=None)

    def record(self, record: UsageRecord) -> None:
        """Called AFTER a turn (token counts) or an executed write action
        (``action`` set). The stub logs it for future billing; a real provider
        debits credits / writes a meter row."""
        log.info(
            "copilot_usage",
            extra={"extra_fields": {
                "tenant": record.tenant,
                "model": record.model,
                "input_tokens": record.input_tokens,
                "output_tokens": record.output_tokens,
                "action": record.action,
                "cost_usd": record.cost_usd,
                "at": record.at.isoformat(),
            }},
        )


#: Process-wide default provider. Replace by assigning a new instance (or wire a
#: per-request provider) once billing exists.
_PROVIDER = CreditsProvider()


def get_provider() -> CreditsProvider:
    return _PROVIDER


def check_credits(tenant: str) -> CreditDecision:
    return get_provider().check(tenant)


def record_usage(tenant: str, model: str, input_tokens: int,
                 output_tokens: int) -> None:
    get_provider().record(UsageRecord(
        tenant=tenant, model=model,
        input_tokens=int(input_tokens or 0), output_tokens=int(output_tokens or 0),
        at=datetime.now(timezone.utc)))


def record_action(tenant: str, model: str, action: str,
                  cost_usd: float = 0.0) -> None:
    """Record an executed write/cost tool for billing/audit (no message content).
    This is the hook where a real biller debits a per-action credit."""
    get_provider().record(UsageRecord(
        tenant=tenant, model=model, input_tokens=0, output_tokens=0,
        at=datetime.now(timezone.utc), action=action, cost_usd=cost_usd))


# --------------------------------------------------------------------------- #
# STOPGAP daily message cap (delete when real billing lands).
#
# The CreditsProvider above is the permissive free-preview stub — it never
# refuses. Until real free-credit accounting exists, that leaves a live Copilot
# able to run up an unbounded Anthropic bill. This is a minimal, in-memory safety
# net: a per-tenant/day and a global/day *message* counter, consulted BEFORE the
# model runs. It is deliberately crude — counts reset at UTC midnight and on
# process restart, and are not shared across worker processes (each worker gets
# its own budget). That is acceptable for a coarse spend cap and keeps it free of
# storage/coupling. When billing replaces CreditsProvider, delete this whole
# block and the check_daily_cap() call in the chat route.
# --------------------------------------------------------------------------- #


class _DailyMessageCap:
    """Process-wide, in-memory per-day message counter (thread-safe)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._day: date | None = None
        self._per_tenant: dict[str, int] = {}
        self._global = 0

    def _roll(self, today: date) -> None:
        if self._day != today:
            self._day, self._per_tenant, self._global = today, {}, 0

    def check(self, tenant: str, per_tenant_daily: int | None,
              global_daily: int | None, today: date | None = None) -> CreditDecision:
        today = today or datetime.now(timezone.utc).date()
        with self._lock:
            self._roll(today)
            if global_daily and self._global >= global_daily:
                return CreditDecision(
                    allowed=False,
                    reason="The Copilot has hit its daily limit for everyone — "
                           "please try again tomorrow.",
                    remaining=0)
            used = self._per_tenant.get(tenant, 0)
            if per_tenant_daily and used >= per_tenant_daily:
                return CreditDecision(
                    allowed=False,
                    reason="You've reached today's Copilot message limit — "
                           "please try again tomorrow.",
                    remaining=0)
            # Under the cap: count this message and allow.
            self._per_tenant[tenant] = used + 1
            self._global += 1
            remaining = (max(0, per_tenant_daily - self._per_tenant[tenant])
                         if per_tenant_daily else None)
            return CreditDecision(allowed=True, reason="free-preview",
                                  remaining=remaining)

    def reset(self) -> None:
        with self._lock:
            self._day, self._per_tenant, self._global = None, {}, 0


_CAP = _DailyMessageCap()


def check_daily_cap(tenant: str, per_tenant_daily: int | None = None,
                    global_daily: int | None = None,
                    today: date | None = None) -> CreditDecision:
    """Consult (and, when allowed, increment) the stopgap daily message cap.

    Returns a :class:`CreditDecision` so the endpoint reuses its existing 402
    path. When both caps are falsy the cap is disabled and this always allows
    (without counting)."""
    if not per_tenant_daily and not global_daily:
        return CreditDecision(allowed=True, reason="free-preview")
    return _CAP.check(tenant, per_tenant_daily, global_daily, today)


def reset_daily_cap() -> None:
    """Clear the in-memory daily counters (used by tests)."""
    _CAP.reset()
