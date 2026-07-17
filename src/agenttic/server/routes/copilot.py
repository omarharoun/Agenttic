"""HTTP/SSE surface for the Agenttic Copilot — an AGENTIC in-app assistant.

The Copilot is a Claude Sonnet 4.6 agent whose tools are the platform's own API,
scoped to the signed-in user (see :mod:`agenttic.copilot.tools`,
:mod:`agenttic.copilot.agent`). It reads freely and PROPOSES write/cost actions,
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

from agenttic.copilot.agent import CopilotAgent, new_session
from agenttic.copilot.credits import check_credits, check_daily_cap, record_usage
from agenttic.copilot.errors import (
    DAILY_LIMIT, NOT_CONFIGURED, OUT_OF_CREDITS, RATE_LIMITED, with_message,
)
from agenttic.copilot.service import (
    CopilotConfig, CopilotNotConfigured, is_configured, resolve_client,
    server_side_key,
)
from agenttic.copilot.skill import build_public_system_prompt
from agenttic.copilot.store import CopilotStore
from agenttic.copilot.tools import (
    ToolContext, is_public_safe, public_tool_schemas,
)
from agenttic.registry.sqlite_store import NotFoundError
from agenttic.secrets import known_secret_values
from agenttic.server.ratelimit import InMemoryRateLimiter

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


def _stream(request: Request, session: dict, events, *,
            store: CopilotStore | None = None, tenant: str | None = None,
            model: str | None = None):
    """Turn agent events into an SSE response, recording usage and persisting the
    session (incl. any pending approval) when the stream ends.

    The authed route resolves ``store``/``tenant``/``model`` from the tenant-scoped
    ``request.state``; the PUBLIC route passes them explicitly (public-demo tenant
    + ``app.state`` store) since a signed-out request carries no ``request.state``
    binding. The SSE event protocol + escaping are IDENTICAL on both surfaces."""
    if store is None:
        store = _store(request)
    if tenant is None:
        tenant = getattr(request.state, "tenant", "default")
    if model is None:
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


# --------------------------------------------------------------------------- #
# PUBLIC, UNAUTHENTICATED intake bot — the scan-intake persona on the landing
# page ("Is your AI agent safe to ship?").
#
# It reuses the EXACT same CopilotAgent loop + SSE machinery above (never a
# second loop), parameterized for the anonymous surface:
#   * client   — the server-side key (never a visitor key), like the demo scan;
#   * model    — the copilot (Haiku) model;
#   * tenant   — the public-demo tenant (job-isolated from real workspaces);
#   * prompt   — the PUBLIC intake persona (build_public_system_prompt);
#   * tools    — the STRICT public allowlist (public_tool_schemas), and dispatch
#                is hard-gated by is_public_safe so a model can NEVER reach a
#                tenant/platform/certification tool here;
#   * ctx      — ToolContext(request, public=True): demo tools force the demo
#                tenant + read app.state, and start_scan refuses.
#
# Chat turns are rate-limited generously (the Copilot per-minute limiter, default
# 20/min) + daily-capped; the tight demo-scan ceiling (abuse.DEMO_DEFAULTS: per-IP
# /min + global/day) lives on the start_demo_scan action itself. All refusals fail
# closed with the SAME 429/402 error-card shape the authed route uses. Sessions are
# anonymous, keyed by a returned session_id in the public-demo tenant's copilot
# session store; no auth, no cookies.
# --------------------------------------------------------------------------- #

from agenttic.server.routes.scan import PUBLIC_DEMO_TENANT  # noqa: E402

public_router = APIRouter(tags=["copilot-public"], prefix="/public/copilot")


def _public_cfg(request: Request) -> CopilotConfig:
    """Copilot config from app.state (no per-request tenant binding on the public
    surface)."""
    return CopilotConfig.from_cfg(getattr(request.app.state, "cfg", None) or {})


def _public_injected(request: Request) -> dict:
    """Injected test/dev clients live on app.state for the signed-out surface
    (the authed route copies them onto request.state; the public route can't)."""
    return getattr(request.app.state, "clients", None) or {}


def _public_store(request: Request) -> CopilotStore:
    """Anonymous session store, isolated to the public-demo tenant."""
    return CopilotStore(request.app.state.reg.engine, PUBLIC_DEMO_TENANT)


def _public_demo_key(request: Request) -> str | None:
    """The key the public intake bot runs on — resolved EXACTLY like the open
    demo scan it drives (``scan._server_demo_key``: env ANTHROPIC_API_KEY, else
    the default workspace's stored key), so the bot is available precisely when
    the demo is. Never a visitor key. Unlike the authed Copilot (env-only), the
    public surface accepts the stored default key so single-owner installs get a
    working bot without setting an extra env var."""
    from agenttic.server.routes.scan import _server_demo_key
    return _server_demo_key(request.app.state.cfg, request.app.state.reg)


def _public_available(request: Request) -> bool:
    """The public bot runs on the SERVER-SIDE key (or an injected client) — never
    a visitor key. Available when either is present."""
    if is_configured(_public_injected(request)):
        return True
    return bool(_public_demo_key(request))


def _public_client(request: Request):
    """Anthropic client for the public bot: an injected test/dev client if
    present, else one built from the demo key (env or stored default)."""
    injected = _public_injected(request)
    if injected and (injected.get("copilot") or injected.get("anthropic")):
        return resolve_client(injected)
    key = _public_demo_key(request)
    if not key:
        raise CopilotNotConfigured(
            "The assistant isn't configured on this server yet.")
    import anthropic
    return anthropic.Anthropic(api_key=key)


def _public_guards(request: Request):
    """Pre-flight for the public chat/approve: rate-limit + daily cap (fail closed
    with the authed 429/402 card shape) → server-key-required (503) → build the
    PUBLIC agent + a public ToolContext. Returns (agent, ctx, cfg)."""
    # CHAT-appropriate rate limit — a chat turn is one cheap Haiku call, so it
    # uses the SAME per-minute limiter as the authed Copilot (default 20/min),
    # NOT the tight 2/min demo-scan ceiling. The expensive demo *scan* keeps its
    # own per-IP + daily ceiling, applied where the scan actually starts (the
    # start_demo_scan tool), so chatting stays fluid while scan spend stays
    # bounded. Keyed per-IP for the anonymous surface.
    _ip = request.client.host if request.client else "unknown"
    # Read the chat limit from app.state.cfg — the public surface has no auth /
    # workspace middleware, so request.state.cfg (what _rl_limit reads) is unset.
    _app_cfg = (getattr(request.app.state, "cfg", None) or {}).get("copilot", {}) or {}
    _rlpm = int(_app_cfg.get("rate_limit_per_minute", 20))
    if not _RL.allow(f"pubcop:ip:{_ip}", _rlpm, _RL_WINDOW):
        raise _refuse(429, with_message(
            RATE_LIMITED, "You're sending messages a bit fast — give it a few "
            "seconds and try again."))
    cfg = _public_cfg(request)
    cap = check_daily_cap(PUBLIC_DEMO_TENANT, cfg.daily_cap_per_user,
                          cfg.daily_cap_global)
    if not cap.allowed:
        raise _refuse(402, with_message(DAILY_LIMIT, cap.reason or None))
    try:
        client = _public_client(request)
    except CopilotNotConfigured as exc:
        raise _refuse(503, with_message(NOT_CONFIGURED, str(exc)))
    agent = CopilotAgent(
        client, cfg.model, max_tokens=cfg.max_output_tokens,
        extra_secrets=known_secret_values(
            getattr(request.app.state, "cfg", None) or {}),
        system_prompt=build_public_system_prompt(),
        tools=public_tool_schemas(),
        tool_filter=is_public_safe)
    return agent, ToolContext(request, public=True), cfg


def _public_stream(request: Request, session: dict, events):
    return _stream(request, session, events,
                   store=_public_store(request), tenant=PUBLIC_DEMO_TENANT,
                   model=_public_cfg(request).model)


@public_router.get("/status")
def public_copilot_status(request: Request):
    """Is the public intake bot available on this server? Available when a
    server-side Copilot key (or an injected client) is present. No auth."""
    cfg = _public_cfg(request)
    return {"available": _public_available(request), "model": cfg.model}


@public_router.post("/chat")
def public_copilot_chat(body: ChatBody, request: Request):
    """Anonymous intake chat turn → SSE stream. Same event protocol/escaping as
    the authed /api/copilot/chat. Runs on the server key + the public tool
    allowlist, forced onto the public-demo tenant. No auth, no cookies — pass the
    returned session_id back to continue the conversation."""
    agent, ctx, _cfg = _public_guards(request)
    store = _public_store(request)
    if body.session_id:
        try:
            session = store.get(body.session_id)
        except NotFoundError:
            raise HTTPException(404, "copilot session not found")
    else:
        session = new_session()
    events = agent.start_turn(session, ctx, body.message)
    return _public_stream(request, session, events)


@public_router.post("/approve")
def public_copilot_approve(body: ApproveBody, request: Request):
    """Resume an anonymous turn after the visitor confirms/declines a proposed
    action (the free demo scan). Same SSE protocol as the authed approve route."""
    agent, ctx, _cfg = _public_guards(request)
    store = _public_store(request)
    try:
        session = store.get(body.session_id)
    except NotFoundError:
        raise HTTPException(404, "copilot session not found")
    events = agent.resume(session, ctx, approved=body.approved)
    return _public_stream(request, session, events)
