"""Risk feed endpoints (SPEC-2 T34.1/T34.2). Authenticated, tenant-scoped."""

from __future__ import annotations

from fastapi import APIRouter, Request

router = APIRouter(tags=["feeds"])


@router.get("/feeds/risk/{agent_id}")
def risk_feed(agent_id: str, request: Request):
    """Aggregate risk feed for one agent (no traces/payloads/PII)."""
    from ascore.feeds.risk_api import risk_feed as _feed
    return _feed(request.state.reg, request.state.cfg, agent_id)
