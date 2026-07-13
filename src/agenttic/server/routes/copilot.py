"""HTTP/SSE surface for the Agenttic Copilot — an AGENTIC in-app assistant.

The Copilot is a Claude Sonnet 4.6 agent whose tools are the platform's own API,
scoped to the signed-in user (see :mod:`ascore.copilot.tools`,
:mod:`ascore.copilot.agent`). It reads freely and PROPOSES write/cost actions,
which the user must confirm before they run.

Endpoints (auth + tenant scoped like the rest of ``/api``):
* ``GET  /api/copilot/status``  — is the Copilot available on this server?
  (Runs on Agenttic's OWN server-side key — not the user's.)
* ``POST /api/copilot/chat``    — ``{session_id?, message}``. Creates/loads a
  tenant-scoped session, runs the agent loop, and streams events as SSE. If the
  agent wants to run a write/cost tool it emits ``approval_required`` and the
  stream ends awaiting the user's decision.
* ``POST /api/copilot/approve`` — ``{session_id, approved}``. Resolves the pending
  write action (run it / decline it) and resumes the agent, streaming as SSE.

SSE events: ``session`` (id/status), ``token`` (answer text delta), ``tool``
(tool activity: start/done + summary), ``approval_required`` (a write action
awaiting confirmation, with its confirmation card), ``error``, ``done``
(id/status). Guardrails: per-session/IP rate limit, credits gate (coarse here;
per-write inside the agent), server-key-required (503), secret scrubbing, tenant
isolation. Token usage is recorded for billing (no message content persisted
beyond the tenant-scoped session transcript needed to resume).
"""

from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from ascore.copilot.agent import CopilotAgent, new_session
from ascore.copilot.credits import check_credits, check_daily_cap, record_usage
from ascore.copilot.errors import (
    DAILY_LIMIT, NOT_CONFIGURED, OUT_OF_CREDITS, RATE_LIMITED, with_message,
)
from ascore.copilot.service import (
    CopilotConfig, CopilotNotConfigured, is_configured, resolve_client,
)
from ascore.copilot.store import CopilotStore
from ascore.copilot.tools import ToolContext
from ascore.registry.sqlite_store import NotFoundError
from ascore.secrets import known_secret_values
from ascore.server.ratelimit import InMemoryRateLimiter

router = APIRouter(tags=["copilot"], prefix="/copilot")

# Dedicated per-session/IP limiter, independent of the global middleware, so the
# Copilot is always bounded. Shared across chat + approve.
_RL = InMemoryRateLimiter()
_RL_WINDOW = 60.0


def _copilot_cfg(request: Request) -> CopilotConfig:
    return CopilotConfig.from_cfg(getattr(request.state, "cfg", None) or {})


def _rl_limit(request: Request) -> int:
    cfg = (getattr(request.state, "cfg", None) or {}).get("copilot", {}) or {}
    return int(cfg.get("rate_limit_per_minute", 20))


def _rl_key(request: Request) -> str:
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return f"copilot:tok:{auth[7:].strip()}"
    sid = request.cookies.get("ascore_session")
    if sid:
        return f"copilot:sess:{sid}"
    client = request.client
    return f"copilot:ip:{client.host if client else 'unknown'}"


def _injected(request: Request) -> dict:
    return getattr(request.state, "clients", None) or {}


def _store(request: Request) -> CopilotStore:
    return CopilotStore(request.state.reg.engine,
                        getattr(request.state, "tenant", "default"))


class ChatBody(BaseModel):
    message: str
    session_id: str | None = None


class ApproveBody(BaseModel):
    session_id: str
    approved: bool


@router.get("/status")
def copilot_status(request: Request):
    cfg = _copilot_cfg(request)
    return {"available": is_configured(_injected(request)), "model": cfg.model,
            "agentic": True}


def _sse(event: str, data: str) -> str:
    safe = data.replace("\\", "\\\\").replace("\n", "\\n")
    return f"event: {event}\ndata: {safe}\n\n"


def _refuse(status: int, err) -> HTTPException:
    """A pre-flight refusal whose ``detail`` mirrors the SSE ``error`` shape
    (``code`` / ``message`` / ``action``) so the frontend renders ONE styled
    error card whether the failure arrives as an HTTP 4xx or an in-stream SSE
    event. FastAPI serializes the dict under ``detail``."""
    return HTTPException(status, detail=err.payload())


def _guards(request: Request):
    """Shared pre-flight for chat/approve: rate limit → credits → configured →
    (agent, ctx). Raises HTTPException on refusal; returns (agent, ctx, cfg)."""
    if not _RL.allow(_rl_key(request), _rl_limit(request), _RL_WINDOW):
        raise _refuse(429, with_message(
            RATE_LIMITED,
            "You're sending messages too fast — give it a moment and try again."))
    tenant = getattr(request.state, "tenant", "default")
    decision = check_credits(tenant)
    if not decision.allowed:
        raise _refuse(402, with_message(OUT_OF_CREDITS, decision.reason or None))
    injected = _injected(request)
    if not is_configured(injected):
        raise _refuse(503, with_message(
            NOT_CONFIGURED,
            "The Copilot assistant isn't configured on this server yet."))
    cfg = _copilot_cfg(request)
    try:
        client = resolve_client(injected)
    except CopilotNotConfigured as exc:
        raise _refuse(503, with_message(NOT_CONFIGURED, str(exc)))
    agent = CopilotAgent(
        client, cfg.model, max_tokens=cfg.max_output_tokens,
        extra_secrets=known_secret_values(getattr(request.state, "cfg", None) or {}))
    return agent, ToolContext(request), cfg


def _stream(request: Request, session: dict, events):
    """Turn agent events into an SSE response, recording usage and persisting the
    session (incl. any pending approval) when the stream ends."""
    store = _store(request)
    tenant = getattr(request.state, "tenant", "default")
    model = _copilot_cfg(request).model

    def gen():
        yield _sse("session", json.dumps(
            {"session_id": session["session_id"], "status": session["status"]}))
        try:
            for ev in events:
                kind = ev.get("type")
                if kind == "token":
                    yield _sse("token", ev.get("text", ""))
                elif kind == "tool":
                    yield _sse("tool", json.dumps({
                        "tool": ev.get("tool"), "phase": ev.get("phase"),
                        "kind": ev.get("kind"), "ok": ev.get("ok"),
                        "summary": ev.get("summary")}))
                elif kind == "approval_required":
                    yield _sse("approval_required", json.dumps({
                        "tool": ev.get("tool"), "input": ev.get("input", {}),
                        "card": ev.get("card", {})}))
                elif kind == "usage":
                    record_usage(tenant, model, ev.get("input_tokens", 0),
                                 ev.get("output_tokens", 0))
                elif kind == "final":
                    # the answer text already streamed as `token` events during
                    # the model turn; `final` is just the end-of-answer marker.
                    pass
                elif kind == "error":
                    yield _sse("error", json.dumps({
                        "code": ev.get("code", "generic"),
                        "message": ev.get("text", ""),
                        "action": ev.get("action", "retry")}))
        finally:
            store.save(session)
            yield _sse("done", json.dumps(
                {"session_id": session["session_id"],
                 "status": session.get("status", "ready")}))

    return StreamingResponse(
        gen(), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no",
                 "Connection": "keep-alive"})


@router.post("/chat")
def copilot_chat(body: ChatBody, request: Request):
    agent, ctx, cfg = _guards(request)
    # Stopgap spend cap: count this user message against the per-tenant/day and
    # global/day limits before running the model. Reuses the 402 credits path;
    # remove when real billing replaces the credits seam. Only new chat messages
    # are counted — an /approve resume is bounded by the chat that preceded it.
    tenant = getattr(request.state, "tenant", "default")
    cap = check_daily_cap(tenant, cfg.daily_cap_per_user, cfg.daily_cap_global)
    if not cap.allowed:
        raise _refuse(402, with_message(DAILY_LIMIT, cap.reason or None))
    store = _store(request)
    if body.session_id:
        try:
            session = store.get(body.session_id)
        except NotFoundError:
            raise HTTPException(404, "copilot session not found")
    else:
        session = new_session()
    events = agent.start_turn(session, ctx, body.message)
    return _stream(request, session, events)


@router.post("/approve")
def copilot_approve(body: ApproveBody, request: Request):
    agent, ctx, _cfg = _guards(request)
    store = _store(request)
    try:
        session = store.get(body.session_id)
    except NotFoundError:
        raise HTTPException(404, "copilot session not found")
    events = agent.resume(session, ctx, approved=body.approved)
    return _stream(request, session, events)
