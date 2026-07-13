"""Abuse controls for the cost-bearing endpoints — a small, config-driven layer
that bounds how fast a single IP, a single tenant, and the whole server can hit
the endpoints that spend money or start background work.

Why this exists (the threat model):

* The Copilot chat runs on **Agenttic's own** Anthropic key. It already has a
  per-session/IP per-minute limiter and per-tenant + global daily message caps
  (see :mod:`agenttic.copilot.credits`). This module adds the SAME shape of
  protection to the other cost/expense-bearing surfaces — **demo scans** and
  **certification starts** — plus a **signup throttle** so accounts (and thus
  fresh per-tenant budgets) can't be farmed by hammering ``/auth/signup``.
* Every knob is read from the ``abuse:`` config block at request time and is
  **0 = off** by default, exactly like ``security.rate_limit_per_minute``. Dev
  ships with them off; ``config.prod.yaml`` turns them on. Legitimate use stays
  unaffected; abuse fails closed with an honest 429.

Layers, checked in order (cheapest / most-specific first, so a blocked request
never consumes the scarce global budget):

    per-IP / minute   → 429   (one network address hammering)
    per-tenant / minute → 429 (one workspace hammering)
    global / day      → 429   (server-wide ceiling across ALL tenants — the
                               backstop against IP/tenant rotation)

State is per-process and in-memory (like the existing limiters): fine for a
single worker; a multi-worker deployment should front this with the shared
Redis limiter. Counters reset at UTC midnight and on restart — acceptable for a
coarse abuse ceiling.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from datetime import date, datetime, timezone

from fastapi import HTTPException, Request

from agenttic.server.ratelimit import InMemoryRateLimiter

log = logging.getLogger("agenttic.server.abuse")

_MINUTE = 60.0
_HOUR = 3600.0


@dataclass(frozen=True)
class ActionLimits:
    """Resolved thresholds for one cost-bearing action (0 = that layer off)."""
    per_ip_per_minute: int = 0
    per_tenant_per_minute: int = 0
    global_per_day: int = 0


def _cfg(request: Request) -> dict:
    """The active config. Middleware sets ``request.state.cfg`` for ``/api``
    routes; the auth routes (outside ``/api``) only have ``app.state.cfg`` — try
    both so the guards work on either surface."""
    cfg = getattr(getattr(request, "state", None), "cfg", None)
    if not cfg:
        app_state = getattr(getattr(request, "app", None), "state", None)
        cfg = getattr(app_state, "cfg", None)
    return cfg or {}


def _abuse_cfg(cfg: dict | None, action: str) -> dict:
    return (((cfg or {}).get("abuse", {}) or {}).get(action, {}) or {})


def limits_for(cfg: dict | None, action: str) -> ActionLimits:
    a = _abuse_cfg(cfg, action)
    return ActionLimits(
        per_ip_per_minute=int(a.get("per_ip_per_minute", 0) or 0),
        per_tenant_per_minute=int(a.get("per_tenant_per_minute", 0) or 0),
        global_per_day=int(a.get("global_per_day", 0) or 0),
    )


class _DailyCeiling:
    """Process-wide, thread-safe per-UTC-day counter keyed by action name. The
    server-wide backstop: it bounds aggregate spend across ALL tenants/IPs, so
    rotating IPs or farming tenants can't push total spend past the ceiling."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._day: date | None = None
        self._counts: dict[str, int] = {}

    def _roll(self, today: date) -> None:
        if self._day != today:
            self._day, self._counts = today, {}

    def allow(self, action: str, limit: int, today: date | None = None) -> bool:
        """Count one hit against ``action`` and return whether it's under
        ``limit``. Only call this AFTER the per-IP/per-tenant checks pass, so a
        request that's already refused doesn't burn the global budget."""
        if limit <= 0:
            return True
        today = today or datetime.now(timezone.utc).date()
        with self._lock:
            self._roll(today)
            used = self._counts.get(action, 0)
            if used >= limit:
                return False
            self._counts[action] = used + 1
            return True

    def reset(self) -> None:
        with self._lock:
            self._day, self._counts = None, {}


# Per-minute sliding windows (per-IP + per-tenant) share one limiter; the key
# namespaces action+scope. The daily ceiling is separate.
_RL = InMemoryRateLimiter()
_DAY = _DailyCeiling()


def _client_ip(request: Request) -> str:
    client = getattr(request, "client", None)
    return client.host if client else "unknown"


def _tenant(request: Request) -> str:
    return getattr(getattr(request, "state", None), "tenant", "default") or "default"


def _too_fast(retry_after: int, message: str) -> HTTPException:
    return HTTPException(status_code=429, detail={"code": "rate_limited",
                        "message": message, "action": "retry"},
                        headers={"Retry-After": str(retry_after)})


def guard_cost_endpoint(request: Request, action: str) -> None:
    """Enforce the per-IP / per-tenant / global-daily limits for ``action``
    (``scan`` | ``certify``). Raises HTTP 429 (fail closed) with an honest
    message when a layer is exceeded; a no-op when every knob is 0/off.

    Layers are checked specific→global so a blocked request never consumes the
    scarce global daily budget."""
    lim = limits_for(_cfg(request), action)
    if lim == ActionLimits():  # all off — nothing to do
        return
    ip, tenant = _client_ip(request), _tenant(request)

    if lim.per_ip_per_minute and not _RL.allow(
            f"{action}:ip:{ip}", lim.per_ip_per_minute, _MINUTE):
        log.warning("abuse_block", extra={"extra_fields": {
            "action": action, "layer": "per_ip", "tenant": tenant}})
        raise _too_fast(60, "You're doing that too fast — give it a minute and "
                            "try again.")

    if lim.per_tenant_per_minute and not _RL.allow(
            f"{action}:ten:{tenant}", lim.per_tenant_per_minute, _MINUTE):
        log.warning("abuse_block", extra={"extra_fields": {
            "action": action, "layer": "per_tenant", "tenant": tenant}})
        raise _too_fast(60, "This workspace is doing that too fast — give it a "
                            "minute and try again.")

    # Global daily ceiling last: only count a request that cleared the layers
    # above, so rotation can't drain the server-wide budget on refused calls.
    if lim.global_per_day and not _DAY.allow(action, lim.global_per_day):
        log.warning("abuse_block", extra={"extra_fields": {
            "action": action, "layer": "global_day", "tenant": tenant}})
        raise _too_fast(3600, "This feature has hit its daily limit for everyone "
                             "— please try again tomorrow.")


def guard_signup(request: Request) -> None:
    """Throttle account creation per IP so tenants (and their fresh per-tenant
    budgets / verification emails) can't be farmed by hammering ``/auth/signup``.
    Config: ``abuse.signup.per_ip_per_hour`` (0 = off). Raises 429 when tripped.

    Note: the free-account grant itself is idempotent per email/tenant (signup
    409s a duplicate), so credits can't be farmed by re-signing-up the same
    address; this throttle stops the complementary vector of spinning up many
    fresh addresses from one network."""
    per_ip_hour = int(_abuse_cfg(_cfg(request), "signup").get(
        "per_ip_per_hour", 0) or 0)
    if per_ip_hour <= 0:
        return
    ip = _client_ip(request)
    if not _RL.allow(f"signup:ip:{ip}", per_ip_hour, _HOUR):
        log.warning("abuse_block", extra={"extra_fields": {
            "action": "signup", "layer": "per_ip", "ip": ip}})
        raise HTTPException(
            status_code=429,
            detail="Too many sign-ups from your network — please try again later.",
            headers={"Retry-After": "3600"})


def reset_abuse() -> None:
    """Clear all in-memory abuse counters (used by tests)."""
    _RL._hits.clear()
    _DAY.reset()
