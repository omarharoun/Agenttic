"""Passport endpoints (SPEC-2 M16/M17).

* Public JWKS at ``/.well-known/agenttic-jwks.json`` — the verification keys.
* ``POST /api/passport/issue`` / ``renew`` — issue/renew bound to latest evidence.
* ``POST /api/passport/{id}/revoke`` — append-only revocation.
* Public ``GET /passport/{id}/status`` — the status URL (truth for revocation).
* ``GET /api/passport/{id}/verify`` — signature verification (split from status).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from agenttic.registry.sqlite_store import NotFoundError
from agenttic.server.auth import require_operator

# public (unauthenticated) — JWKS + status URL
public_router = APIRouter(tags=["passport-public"])
# authenticated
router = APIRouter(tags=["passport"])


@public_router.get("/.well-known/agenttic-jwks.json")
def jwks(request: Request):
    km = request.app.state.passport_keys
    return km.jwks()


@public_router.get("/passport/{passport_id}/status")
def passport_status(passport_id: str, request: Request):
    """The status URL — the authority on revocation (checked separately from the
    signature, Hard Rule 28). Public."""
    from agenttic.passport.issuer import passport_status_view
    reg = request.app.state.reg
    try:
        return passport_status_view(reg, passport_id)
    except NotFoundError:
        raise HTTPException(404, f"passport {passport_id} not found")


class IssueRequest(BaseModel):
    agent_id: str


@router.post("/passport/issue", dependencies=[Depends(require_operator)])
def issue_passport(body: IssueRequest, request: Request):
    from agenttic.passport.issuer import PassportIssuer
    issuer = PassportIssuer(request.state.reg, request.state.cfg,
                            request.app.state.passport_keys)
    try:
        p = issuer.issue(body.agent_id)
    except NotFoundError:
        raise HTTPException(404, f"no certification for agent {body.agent_id}")
    except ValueError as exc:
        raise HTTPException(409, str(exc))
    return p.model_dump(mode="json")


@router.post("/passport/renew", dependencies=[Depends(require_operator)])
def renew_passport(body: IssueRequest, request: Request):
    from agenttic.passport.issuer import PassportIssuer
    issuer = PassportIssuer(request.state.reg, request.state.cfg,
                            request.app.state.passport_keys)
    try:
        p = issuer.issue(body.agent_id)  # renew = re-issue bound to latest evidence
    except (NotFoundError, ValueError) as exc:
        raise HTTPException(409, str(exc))
    return p.model_dump(mode="json")


@router.post("/passport/{passport_id}/revoke",
             dependencies=[Depends(require_operator)])
def revoke_passport(passport_id: str, request: Request):
    from agenttic.passport.issuer import PassportIssuer
    issuer = PassportIssuer(request.state.reg, request.state.cfg,
                            request.app.state.passport_keys)
    try:
        issuer.revoke(passport_id)
    except NotFoundError:
        raise HTTPException(404, f"passport {passport_id} not found")
    return {"passport_id": passport_id, "status": "revoked"}


@router.get("/passport/{passport_id}/verify")
def verify_passport(passport_id: str, request: Request):
    from agenttic.passport.issuer import PassportIssuer
    issuer = PassportIssuer(request.state.reg, request.state.cfg,
                            request.app.state.passport_keys)
    try:
        return issuer.verify(passport_id)
    except NotFoundError:
        raise HTTPException(404, f"passport {passport_id} not found")
