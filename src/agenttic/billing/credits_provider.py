"""Wire the real billing provider into the Copilot credits seam.

:mod:`ascore.copilot.credits` defines a :class:`CreditsProvider` interface with a
permissive stub as the default. This module supplies the REAL implementation —
:class:`BillingCreditsProvider` — and installs it at app startup, so:

* ``check_credits(tenant)`` (called BEFORE the Copilot model runs) consults the
  tenant's real credit balance and returns ``allowed=False`` when out of credits,
  which the endpoint turns into the existing HTTP 402.
* ``record_usage(...)`` / ``record_action(...)`` (called AFTER a turn / executed
  action) DEBIT real credits from the tenant's ledger.

The provider is bound to a ``resolve_engine(tenant) -> engine`` callback (backed
by the app's :class:`Workspaces`) plus the config, so it can reach each tenant's
ledger without threading a store through the fixed seam signature.

Installation is guarded: we only replace the process-wide provider if it is still
the default stub, so a test that has monkeypatched a custom provider is never
clobbered (see :func:`install_if_default`).
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from ascore.billing import plans, service
from ascore.copilot import credits as copilot_credits
from ascore.copilot.credits import CreditDecision, CreditsProvider, UsageRecord

log = logging.getLogger("ascore.billing")


class BillingCreditsProvider(CreditsProvider):
    """Real free-credit accounting behind the Copilot seam."""

    def __init__(self, resolve_engine: Callable[[str], object], cfg: dict):
        self._resolve = resolve_engine
        self._cfg = cfg

    def check(self, tenant: str) -> CreditDecision:
        """Grant the free trial on first sight, then allow iff the balance is
        positive (or billing is disabled). Fails OPEN on an internal error — a
        billing hiccup must not lock a paying user out of the assistant."""
        try:
            engine = self._resolve(tenant)
            ent = service.entitlement(engine, tenant, self._cfg)
            if ent["allowed"]:
                return CreditDecision(allowed=True, reason="ok",
                                      remaining=ent["balance"])
            return CreditDecision(allowed=False,
                                  reason=service.OUT_OF_CREDITS_MESSAGE,
                                  remaining=0)
        except Exception as exc:  # noqa: BLE001 — fail open on billing errors
            log.warning("billing check failed for %s: %s", tenant,
                        type(exc).__name__)
            return CreditDecision(allowed=True, reason="billing-unavailable")

    def record(self, record: UsageRecord) -> None:
        """DEBIT credits for a completed Copilot turn (token cost) or an executed
        write/cost action (``action`` set → its known/estimated ``cost_usd``).
        Best-effort — never raises."""
        try:
            engine = self._resolve(record.tenant)
            if record.action:
                # an executed write/cost tool: debit its (optional) known cost,
                # flooring at min_action_credits so a "free" action still costs 1.
                service.meter_cost(
                    engine, record.tenant,
                    reason="copilot", cost_usd=record.cost_usd,
                    model=record.model, cfg=self._cfg,
                    ref=record.action,
                    meta={"action": record.action})
            else:
                service.meter_tokens(
                    engine, record.tenant, "copilot", record.model,
                    record.input_tokens, record.output_tokens, cfg=self._cfg)
        except Exception as exc:  # noqa: BLE001 — metering must not break the stream
            log.warning("billing record failed for %s: %s", record.tenant,
                        type(exc).__name__)


def _is_default_stub() -> bool:
    """True when the current provider is the un-swapped default stub (exact base
    type), i.e. no test/other code has installed its own provider."""
    return type(copilot_credits.get_provider()) is CreditsProvider


def install_if_default(workspaces, cfg: dict):
    """Install the billing provider bound to ``workspaces`` — but ONLY if the
    current provider is still the default stub. Returns a restore token
    (the previous provider) when installed, else ``None``.

    Skips installation entirely when billing is disabled in config (the stub's
    permissive free-preview behaviour is then the intended one)."""
    if not plans.is_enabled(cfg):
        return None
    if not _is_default_stub():
        return None

    def resolve_engine(tenant: str):
        return workspaces.get(tenant).reg.engine

    provider = BillingCreditsProvider(resolve_engine, cfg)
    return copilot_credits.set_provider(provider)


def restore(token) -> None:
    """Restore a previous provider captured by :func:`install_if_default`."""
    if token is not None:
        copilot_credits.set_provider(token)
