"""Live service-health probing for Agenttic's OWN uptime status page.

This is NOT the agent-safety incident/dossier surface — it reports whether
*Agenttic itself* (the API, database, worker, signing keys, ingest receiver,
certification and scanner engines) is up right now. Each component is genuinely
probed; a component that cannot be probed returns ``unknown`` — never a
fabricated ``operational`` (the whole product ethos: no invented numbers).

The public rollup is intentionally coarse: per-component ``status``, measured
``latency_ms`` and a ``last_checked`` timestamp only. No connection strings,
hostnames, secrets, counts or PII cross the boundary (SPEC hard rule 30 — the
public status is aggregate). Results are cached briefly so a burst of page
loads probes the real components at most once per ``cache_ttl`` seconds.
"""

from __future__ import annotations

import os

import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

# Component state vocabulary. Severity order matters for the rollup: a single
# ``down`` dominates, then ``degraded``, then ``unknown`` (we genuinely can't
# tell, so we must not claim green), and only an all-clear board is
# ``operational``.
OPERATIONAL = "operational"
DEGRADED = "degraded"
DOWN = "down"
UNKNOWN = "unknown"

_SEVERITY = {DOWN: 3, DEGRADED: 2, UNKNOWN: 1, OPERATIONAL: 0}


class ProbeError(Exception):
    """Raised by a probe to signal a specific non-operational state.

    ``status`` must be one of DEGRADED / DOWN / UNKNOWN — a probe never raises
    to report health. Any *unexpected* exception from a probe is treated as
    UNKNOWN (we could not determine the state), so a broken probe can never
    masquerade as operational."""

    def __init__(self, status: str, detail: str = ""):
        super().__init__(detail)
        self.status = status
        self.detail = detail


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class ComponentHealth:
    name: str
    status: str
    latency_ms: float | None
    detail: str
    last_checked: str

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "status": self.status,
            "latency_ms": self.latency_ms,
            "detail": self.detail,
            "last_checked": self.last_checked,
        }


# A probe returns a short, non-sensitive detail string on success, or raises
# ProbeError(status, detail) for a known non-operational state. It receives the
# FastAPI ``app`` so it can reach app.state; it must be fast and side-effect free.
Probe = Callable[[object], str]


def rollup(components: list[ComponentHealth]) -> str:
    """Overall status = the most severe component state present. Empty board is
    ``unknown`` (we probed nothing, so we cannot claim operational)."""
    if not components:
        return UNKNOWN
    worst = max(components, key=lambda c: _SEVERITY.get(c.status, 1))
    return worst.status


def run_probe(name: str, probe: Probe, app: object) -> ComponentHealth:
    """Run one probe, timing it and converting outcomes to a ComponentHealth.

    A ProbeError yields its declared status; ANY other exception yields UNKNOWN
    (never operational) — a probe that blows up must not read as healthy."""
    t0 = time.monotonic()
    try:
        detail = probe(app)
        status = OPERATIONAL
    except ProbeError as pe:
        status = pe.status if pe.status in _SEVERITY else UNKNOWN
        detail = pe.detail
    except Exception as exc:  # noqa: BLE001 — unknown, deliberately not green
        status = UNKNOWN
        detail = f"probe error: {type(exc).__name__}"
    latency_ms = round((time.monotonic() - t0) * 1000, 2)
    return ComponentHealth(name=name, status=status, latency_ms=latency_ms,
                           detail=detail, last_checked=_now_iso())


# --------------------------------------------------------------------------- #
# Default probes — each verifies a component that actually exists in this repo.
# --------------------------------------------------------------------------- #

def _probe_api(app) -> str:
    # If this code is running, the API process is serving requests. This is a
    # real (if trivial) liveness fact, not a hardcoded green.
    return "serving requests"


def _probe_database(app) -> str:
    from sqlalchemy import text
    reg = getattr(app.state, "reg", None)
    if reg is None:
        raise ProbeError(UNKNOWN, "registry not initialised")
    try:
        with reg.engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception:
        raise ProbeError(DOWN, "database unreachable")
    return "query ok"


def _probe_worker(app) -> str:
    # The background execution engine's event transport. In-memory transport is
    # up iff the process is; a Redis transport is pinged for real.
    wss = getattr(app.state, "workspaces", None)
    transport = getattr(wss, "_transport", None) if wss is not None else None
    if transport is None:
        raise ProbeError(UNKNOWN, "transport not initialised")
    cls = type(transport).__name__
    if cls == "RedisTransport":
        try:
            transport.sync.ping()
        except Exception:
            raise ProbeError(DOWN, "event broker unreachable")
        return "broker reachable"
    return "in-process queue"


def _probe_certification(app) -> str:
    cfg = getattr(app.state, "cfg", None)
    from agenttic import certification as cert
    keys = cert.published_public_keys(cfg)
    if not keys:
        raise ProbeError(DEGRADED, "no certificate signing keys published")
    return "signing keys published"


def _probe_otel_ingest(app) -> str:
    # SPEC-7 OTLP/HTTP receiver. In-process — verify the mapping module that
    # backs POST /v1/traces is loadable (the capability is wired).
    import importlib
    importlib.import_module("agenttic.ingest.mapping")
    return "receiver wired"


def _probe_passport(app) -> str:
    km = getattr(app.state, "passport_keys", None)
    if km is None:
        raise ProbeError(UNKNOWN, "passport keys not initialised")
    keys = (km.jwks() or {}).get("keys", [])
    if not keys:
        raise ProbeError(DEGRADED, "no passport signing keys published")
    # An ephemeral (unconfigured) key signs passports that won't verify across
    # restarts — real but not operational-grade. Never claim operational for it.
    if getattr(km, "is_ephemeral", lambda: False)():
        raise ProbeError(DEGRADED, "ephemeral signing key (not configured)")
    return "JWKS published"


def _probe_scanner(app) -> str:
    from agenttic.scan import battery_dimensions_public
    dims = battery_dimensions_public()
    if not dims:
        raise ProbeError(DEGRADED, "scan battery empty")
    return "scan battery loaded"


DEFAULT_PROBES: list[tuple[str, Probe]] = [
    ("api", _probe_api),
    ("database", _probe_database),
    ("worker", _probe_worker),
    ("certification_engine", _probe_certification),
    ("otel_ingest", _probe_otel_ingest),
    ("passport_signing", _probe_passport),
    ("scanner", _probe_scanner),
]


class HealthChecker:
    """Runs the probe set, caches the snapshot briefly, and stamps version +
    process uptime. One instance lives on ``app.state.health``."""

    def __init__(self, probes: list[tuple[str, Probe]] | None = None, *,
                 cache_ttl: float = 5.0, version: str | None = None,
                 build: str | None = None, clock: Callable[[], float] = time.monotonic):
        self.probes = probes if probes is not None else DEFAULT_PROBES
        self.cache_ttl = cache_ttl
        self._clock = clock
        self._version = version if version is not None else _discover_version()
        self._build = build if build is not None else _discover_build()
        self._started_monotonic = clock()
        self._started_at = _now_iso()
        self._cache: dict | None = None
        self._cache_at: float = 0.0

    def _uptime_seconds(self) -> float:
        return round(self._clock() - self._started_monotonic, 3)

    def snapshot(self, app, *, force: bool = False) -> dict:
        now = self._clock()
        if (not force and self._cache is not None
                and (now - self._cache_at) < self.cache_ttl):
            return self._cache
        components = [run_probe(name, probe, app) for name, probe in self.probes]
        payload = {
            "status": rollup(components),
            "version": self._version,
            "build": self._build,
            "started_at": self._started_at,
            "uptime_seconds": self._uptime_seconds(),
            "checked_at": _now_iso(),
            "components": [c.as_dict() for c in components],
        }
        self._cache = payload
        self._cache_at = now
        return payload


def _discover_version() -> str | None:
    try:
        from importlib.metadata import PackageNotFoundError, version
        for dist in ("agenttic",):
            try:
                return version(dist)
            except PackageNotFoundError:
                continue
        return None
    except Exception:  # noqa: BLE001
        return None


def _discover_build() -> str | None:
    # Optional build/commit stamp, set by the deploy pipeline. Never fabricated.
    return os.environ.get("AGENTTIC_BUILD") or None
