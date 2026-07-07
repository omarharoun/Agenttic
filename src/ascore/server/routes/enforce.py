"""Enforcement proxy endpoints (SPEC-2 T23.4).

An out-of-process agent can call the gateway over HTTP:

* ``POST /api/enforce/sessions`` — start a session (hash-verified policy load).
* ``POST /api/enforce/tool-call`` — evaluate a tool call → Decision.
* ``POST /api/enforce/tool-result`` — evaluate a tool result → Decision.
* ``GET  /api/enforce/events`` — the append-only enforcement log.

The proxy calls the SAME in-process gateway, so the logged event shape is
identical in both modes. Auth + tenant scoped; rate limits apply via the global
middleware; egress SSRF is enforced inside Lane 1.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from ascore.enforce.gateway import PolicyIntegrityError
from ascore.registry.sqlite_store import NotFoundError
from ascore.server.auth import require_operator

router = APIRouter(tags=["enforce"])


class StartSessionRequest(BaseModel):
    agent_id: str


class ToolCallRequest(BaseModel):
    session_id: str
    tool_name: str
    args: dict = {}


class ToolResultRequest(BaseModel):
    session_id: str
    tool_name: str
    result: dict | str | list | None = None


@router.post("/enforce/sessions", dependencies=[Depends(require_operator)])
def start_session(body: StartSessionRequest, request: Request):
    gw = request.state.enforcer
    try:
        session = gw.start_session(body.agent_id)
    except NotFoundError:
        raise HTTPException(404, f"no policy for agent {body.agent_id}")
    except PolicyIntegrityError as exc:
        raise HTTPException(409, str(exc))
    return {"session_id": session.session_id, "agent_id": session.agent_id,
            "policy_hash": session.policy.content_hash}


@router.post("/enforce/tool-call", dependencies=[Depends(require_operator)])
def enforce_tool_call(body: ToolCallRequest, request: Request):
    gw = request.state.enforcer
    try:
        decision = gw.evaluate_tool_call(body.session_id, body.tool_name, body.args)
    except KeyError:
        raise HTTPException(404, f"unknown session {body.session_id}")
    return decision.model_dump(mode="json")


@router.post("/enforce/tool-result", dependencies=[Depends(require_operator)])
def enforce_tool_result(body: ToolResultRequest, request: Request):
    gw = request.state.enforcer
    try:
        decision = gw.evaluate_tool_result(body.session_id, body.tool_name,
                                           body.result)
    except KeyError:
        raise HTTPException(404, f"unknown session {body.session_id}")
    return decision.model_dump(mode="json")


@router.get("/enforce/events")
def enforce_events(request: Request, session_id: str | None = None,
                   agent_id: str | None = None):
    return request.state.reg.list_enforcement_events(session_id, agent_id)
