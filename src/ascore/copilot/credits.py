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
from dataclasses import dataclass
from datetime import datetime, timezone

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
    """What we persist for billing — counts only, never message content."""
    tenant: str
    model: str
    input_tokens: int
    output_tokens: int
    at: datetime


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
        """Called AFTER a turn with the token counts. The stub logs them for
        future billing; a real provider debits credits / writes a meter row."""
        log.info(
            "copilot_usage",
            extra={"extra_fields": {
                "tenant": record.tenant,
                "model": record.model,
                "input_tokens": record.input_tokens,
                "output_tokens": record.output_tokens,
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
