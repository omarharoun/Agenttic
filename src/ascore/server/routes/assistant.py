"""HTTP surface for the Safe Reference Assistant.

Endpoints (all tenant-scoped; the assistant runs on the tenant's OWN Anthropic
key — BYO-key, surfaced as a clear 400 when missing):

* ``POST   /api/assistant/sessions``                 create a session
* ``POST   /api/assistant/sessions/{id}/message``    send a user message; runs the
  hardened loop and returns the steps + state (answer, or a pending sensitive
  action awaiting approval). Poll ``GET`` for state.
* ``POST   /api/assistant/sessions/{id}/approve``    approve/deny the pending
  sensitive action, then resume the loop.
* ``GET    /api/assistant/sessions/{id}``            full session state/history.
* ``GET    /api/assistant/sessions``                 list this tenant's sessions.
* ``GET    /api/assistant/posture``                  the assistant's safety posture
  (tools, sandbox, approval gate) for the UI + certificate.

Writes/runs require the operator role; reads are open to any authenticated
principal.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from ascore.assistant.agent import SafeAssistant, new_session
from ascore.assistant.posture import safety_posture
from ascore.assistant.store import AssistantStore
from ascore.registry.sqlite_store import NotFoundError
from ascore.secrets import known_secret_values
from ascore.server.auth import require_operator
from ascore.server.keys import NO_KEY_MSG
from ascore.server.keys import tenant_run_clients as _run_clients

router = APIRouter(tags=["assistant"], prefix="/assistant")


def _store(request: Request) -> AssistantStore:
    return AssistantStore(request.state.reg.engine,
                          getattr(request.state, "tenant", "default"))


def _assistant(request: Request) -> SafeAssistant:
    """Build a SafeAssistant on THIS tenant's Anthropic key (or the injected
    test client). 400 with a clear "add your key" message when no key is set."""
    clients = _run_clients(request)  # None => use injected dev/test clients
    if clients is None:
        injected = getattr(request.state, "clients", None) or {}
        client = injected.get("agent") or injected.get("anthropic")
        if client is None:
            raise HTTPException(400, NO_KEY_MSG)
    else:
        client = clients["agent"]
    model = request.state.cfg["models"]["agent_default"]
    return SafeAssistant(client, model,
                         extra_secrets=known_secret_values(request.state.cfg))


def _public(session: dict) -> dict:
    """Client-facing view: status, step log, answer, and a summary of any
    pending sensitive action — never the raw internal transcript."""
    pending = session.get("pending")
    pending_view = None
    if pending:
        pending_view = [{"tool": c["name"], "input": c.get("input", {})}
                        for c in pending.get("calls", [])
                        if _is_sensitive(c["name"])]
    return {
        "session_id": session["session_id"],
        "status": session.get("status", "ready"),
        "steps": session.get("steps", []),
        "answer": session.get("answer"),
        "notes": sorted(session.get("notes", {})),
        "pending_approval": pending_view,
        "created_at": session.get("created_at"),
        "updated_at": session.get("updated_at"),
    }


def _is_sensitive(name: str) -> bool:
    from ascore.assistant.tools import is_sensitive
    return is_sensitive(name)


class MessageBody(BaseModel):
    message: str


class ApproveBody(BaseModel):
    approved: bool


@router.get("/posture")
def get_posture(request: Request):
    """The assistant's declared safety posture (read-only)."""
    return safety_posture()


@router.post("/sessions", dependencies=[Depends(require_operator)])
def create_session(request: Request):
    session = new_session()
    _store(request).save(session)
    return _public(session)


@router.get("/sessions")
def list_sessions(request: Request):
    return {"sessions": _store(request).list()}


@router.get("/sessions/{session_id}")
def get_session(session_id: str, request: Request):
    try:
        return _public(_store(request).get(session_id))
    except NotFoundError:
        raise HTTPException(404, f"assistant session {session_id} not found")


@router.post("/sessions/{session_id}/message",
             dependencies=[Depends(require_operator)])
def send_message(session_id: str, body: MessageBody, request: Request):
    store = _store(request)
    try:
        session = store.get(session_id)
    except NotFoundError:
        raise HTTPException(404, f"assistant session {session_id} not found")
    assistant = _assistant(request)  # 400 here if no tenant key
    try:
        session = assistant.send_message(session, body.message)
    except ValueError as exc:
        raise HTTPException(409, str(exc))
    store.save(session)
    return _public(session)


@router.post("/sessions/{session_id}/approve",
             dependencies=[Depends(require_operator)])
def approve_action(session_id: str, body: ApproveBody, request: Request):
    store = _store(request)
    try:
        session = store.get(session_id)
    except NotFoundError:
        raise HTTPException(404, f"assistant session {session_id} not found")
    assistant = _assistant(request)
    try:
        session = assistant.approve(session, approved=body.approved)
    except ValueError as exc:
        raise HTTPException(409, str(exc))
    store.save(session)
    return _public(session)
