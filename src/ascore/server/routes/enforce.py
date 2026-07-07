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


@router.get("/enforce/export")
def enforce_export(request: Request, fmt: str = "json",
                   session_id: str | None = None, agent_id: str | None = None):
    from ascore.enforce.export import export_json, export_otel
    if fmt == "otel":
        return export_otel(request.state.reg, session_id, agent_id)
    import json
    return json.loads(export_json(request.state.reg, session_id, agent_id))


@router.get("/enforce/dashboard")
def enforce_dashboard(request: Request, agent_id: str | None = None,
                      session_id: str | None = None):
    from ascore.enforce.dashboard import dashboard_metrics
    return dashboard_metrics(request.state.reg, agent_id, session_id)


class FalsePositiveRequest(BaseModel):
    session_id: str
    agent_id: str
    decision_ref: str
    note: str = ""


@router.post("/enforce/false-positive", dependencies=[Depends(require_operator)])
def enforce_false_positive(body: FalsePositiveRequest, request: Request):
    """The dashboard FP button: mark a flagged decision benign → checker-eval case."""
    from ascore.enforce.feedback import mark_false_positive
    reviewer = getattr(request.state, "user_email", None) or "reviewer"
    case_id = mark_false_positive(request.state.reg, body.session_id,
                                  body.agent_id, body.decision_ref, reviewer,
                                  note=body.note)
    return {"checker_eval_case": case_id}


class ApprovalResolveRequest(BaseModel):
    approve: bool
    note: str = ""


@router.get("/oversight/analytics")
def oversight_analytics(request: Request, agent_id: str | None = None):
    """Approval-quality process-health metrics (renders from the log alone)."""
    from ascore.oversight.analytics import approval_analytics
    return approval_analytics(request.state.reg, request.state.cfg, agent_id)


@router.get("/oversight/pending")
def oversight_pending(request: Request, agent_id: str | None = None):
    """Pending oversight reviews + loosening proposals (the SSE/UI feed source)."""
    from ascore.enforce.interactive_oversight import (
        pending_loosen_proposals,
        pending_reviews,
    )
    reg = request.state.reg
    cfg = request.state.cfg
    enabled = (cfg.get("oversight", {}).get("interactive_loop", {}) or {}).get(
        "enabled", False)
    return {
        "enabled": enabled,
        "reviews": pending_reviews(reg, agent_id),
        "loosen_proposals": pending_loosen_proposals(reg, agent_id),
    }


class ConfirmLooseningRequest(BaseModel):
    agent_id: str


@router.post("/oversight/proposals/{proposal_id}/confirm",
             dependencies=[Depends(require_operator)])
def oversight_confirm(proposal_id: str, body: ConfirmLooseningRequest,
                      request: Request):
    """Explicitly confirm a loosening proposal (the only path that ever applies
    a loosening — never automatic)."""
    from ascore.enforce.interactive_oversight import InteractiveOversightLoop
    identity = getattr(request.state, "user_email", None) or "operator"
    loop = InteractiveOversightLoop(request.state.reg, request.state.cfg)
    try:
        result = loop.confirm_loosening(body.agent_id, proposal_id, f"pat:{identity}")
    except ValueError as exc:
        raise HTTPException(404, str(exc))
    return result


@router.get("/enforce/approvals")
def enforce_approvals(request: Request, session_id: str | None = None,
                      state: str | None = None):
    return request.state.reg.list_approvals(session_id, state)


@router.post("/enforce/approvals/{approval_id}/resolve",
             dependencies=[Depends(require_operator)])
def resolve_approval(approval_id: str, body: ApprovalResolveRequest,
                     request: Request):
    from ascore.enforce.approvals import ApprovalManager
    from ascore.registry.sqlite_store import NotFoundError
    identity = getattr(request.state, "user_email", None) or "operator"
    am = ApprovalManager(request.state.reg, request.state.cfg)
    try:
        ar = am.resolve(approval_id, body.approve, f"pat:{identity}")
    except NotFoundError:
        raise HTTPException(404, f"approval {approval_id} not found")
    return {"approval_id": ar.approval_id, "state": ar.state,
            "resolver_identity": ar.resolver_identity}
