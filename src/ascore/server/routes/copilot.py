"""HTTP/SSE surface for the Agenttic Copilot — the in-app guide assistant.

Endpoints (auth + tenant scoped like the rest of ``/api``):
* ``GET  /api/copilot/status``  — is the Copilot available on this server?
  (Does NOT require the user's Anthropic key — the Copilot runs on Agenttic's own
  server-side key.) Powers the panel's honest "unavailable" state.
* ``POST /api/copilot/chat``    — stream an answer as Server-Sent Events. Body:
  ``{"messages": [{"role": "user"|"assistant", "content": str}, ...]}``. The
  server injects the system prompt (skill) + curated platform knowledge, calls
  Claude Sonnet 4.6 with Agenttic's own key, and streams tokens back.

Guardrails layered here (see also :mod:`ascore.copilot.service`):
* **Credits gate** — :func:`check_credits` runs BEFORE the model (stub: always
  allowed; the billing seam).  A refusal becomes HTTP 402.
* **Rate limit** — a dedicated per-session/per-IP sliding window, independent of
  the global middleware, so the Copilot is always bounded.
* **Untrusted input** — user messages are passed as data; the system prompt
  forbids following instructions embedded in them, and output is secret-scrubbed.
* **Usage logging** — token counts recorded for future billing (no message
  content is persisted).
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from ascore.copilot.credits import check_credits, record_usage
from ascore.copilot.service import (
    CopilotConfig, CopilotNotConfigured, CopilotService, is_configured,
    resolve_client,
)
from ascore.secrets import known_secret_values
from ascore.server.ratelimit import InMemoryRateLimiter

router = APIRouter(tags=["copilot"], prefix="/copilot")

# A dedicated limiter for the Copilot chat, separate from the global middleware
# so the assistant is always bounded even when the app-wide limit is off. Per
# session (cookie/token) or IP; sliding 60s window.
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


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatBody(BaseModel):
    messages: list[ChatMessage] = Field(default_factory=list)


@router.get("/status")
def copilot_status(request: Request):
    """Whether the Copilot is usable on this server (server-side key present or a
    dev/test client injected). Read-only; no secrets."""
    cfg = _copilot_cfg(request)
    return {"available": is_configured(_injected(request)), "model": cfg.model}


def _sse(event: str, data: str) -> str:
    """Format one SSE frame. ``data`` is sent as a single ``data:`` line with
    newlines escaped so the framing can't be broken by model output."""
    safe = data.replace("\\", "\\\\").replace("\n", "\\n")
    return f"event: {event}\ndata: {safe}\n\n"


@router.post("/chat")
def copilot_chat(body: ChatBody, request: Request):
    """Stream a Copilot answer as SSE. Ordered guardrails: rate limit → credits
    gate → configured? → stream (secret-scrubbed) → record usage."""
    tenant = getattr(request.state, "tenant", "default")

    # 1) rate limit (per session/IP), independent of the global middleware
    if not _RL.allow(_rl_key(request), _rl_limit(request), _RL_WINDOW):
        raise HTTPException(
            429, "You're sending messages a little fast — give it a few seconds.")

    # 2) credits gate (stub today; the billing seam)
    decision = check_credits(tenant)
    if not decision.allowed:
        raise HTTPException(
            402, decision.reason or "Out of Copilot credits.")

    # 3) configured? (server-side key OR injected dev/test client)
    injected = _injected(request)
    if not is_configured(injected):
        raise HTTPException(
            503, "The Copilot assistant isn't configured on this server yet.")

    cfg = _copilot_cfg(request)
    try:
        client = resolve_client(injected)
    except CopilotNotConfigured as exc:
        raise HTTPException(503, str(exc))

    service = CopilotService(
        client, cfg,
        extra_secrets=known_secret_values(getattr(request.state, "cfg", None) or {}))
    messages = [m.model_dump() for m in body.messages]

    def gen():
        answered = False
        for event, data in service.stream(messages):
            if event == "token":
                answered = True
                yield _sse("token", str(data))
            elif event == "usage":
                record_usage(tenant, cfg.model,
                             getattr(data, "input_tokens", 0),
                             getattr(data, "output_tokens", 0))
            elif event == "error":
                yield _sse("error", str(data))
            elif event == "done":
                yield _sse("done", "ok" if answered else "empty")

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no",
                 "Connection": "keep-alive"})
