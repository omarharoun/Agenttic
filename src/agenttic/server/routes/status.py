"""Public service-status endpoint — Agenttic's OWN health, not agent safety.

``GET /api/status`` — the public rollup consumed by the /status page: overall
state, per-component {operational|degraded|down|unknown} + measured latency +
last-checked, running version and process uptime. Unauthenticated (it's the
public status board) but aggregate-only: no internals, secrets or PII leak
(SPEC hard rule 30). Always returns HTTP 200 with the state in the body — a
degraded/down board is a successful *report*, so the page still renders.
"""

from __future__ import annotations

from fastapi import APIRouter, Request

# public (unauthenticated) — mounted before the auth-protected routers
public_router = APIRouter(tags=["status-public"])


@public_router.get("/status")
def service_status(request: Request):
    checker = request.app.state.health
    return checker.snapshot(request.app)
