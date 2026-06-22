"""Tenant settings — the BYO Anthropic API key.

The key is encrypted at rest and never returned (only a masked status). Setting
it validates against Anthropic first. Every Anthropic call for this tenant's
runs uses this key; there is no fallback to the platform key.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from ascore.server import keys as keys_mod
from ascore.server.auth import require_operator
from ascore.server.keys import KeyStore
from ascore.server.pats import PatStore

router = APIRouter(tags=["settings"])


def _store(request: Request) -> KeyStore:
    return KeyStore(request.state.reg.engine, request.state.cfg)


def _pat_store(request: Request) -> PatStore:
    # PATs are global (like users) — use the default/global engine, not the
    # per-tenant workspace engine.
    return PatStore(request.app.state.reg.engine)


def _require_user(request: Request) -> str:
    """The owning user's email, or 401 — PATs belong to a logged-in account
    (a shared/config token has no user identity to attach tokens to)."""
    email = getattr(request.state, "user_email", None)
    if not email:
        raise HTTPException(
            401, "Personal API tokens require a logged-in user account "
                 "(log in, then create tokens in Settings).")
    return email


class KeyBody(BaseModel):
    key: str


@router.get("/settings/anthropic-key")
def get_key_status(request: Request):
    return _store(request).status(request.state.tenant)


@router.post("/settings/anthropic-key/test",
             dependencies=[Depends(require_operator)])
def test_key(body: KeyBody, request: Request):
    ok, msg = keys_mod.validate_anthropic_key(body.key.strip())
    return {"valid": ok, "error": msg or None}


@router.put("/settings/anthropic-key",
            dependencies=[Depends(require_operator)])
def set_key(body: KeyBody, request: Request):
    key = body.key.strip()
    ok, msg = keys_mod.validate_anthropic_key(key)
    if not ok:
        raise HTTPException(422, msg or "key validation failed")
    try:
        _store(request).set_key(request.state.tenant, key)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    return _store(request).status(request.state.tenant)


@router.delete("/settings/anthropic-key",
               dependencies=[Depends(require_operator)])
def delete_key(request: Request):
    _store(request).delete(request.state.tenant)
    return _store(request).status(request.state.tenant)


# -- Personal API tokens (PATs) --------------------------------------------

class TokenBody(BaseModel):
    name: str = ""


@router.get("/settings/tokens")
def list_tokens(request: Request):
    """List the caller's active personal API tokens (masked — the plaintext is
    only ever shown once, at creation)."""
    email = _require_user(request)
    return {"tokens": _pat_store(request).list(email)}


@router.post("/settings/tokens")
def create_token(body: TokenBody, request: Request):
    """Mint a new personal API token for the caller. The ``token`` value is
    returned exactly once — store it now; only its hash is kept server-side."""
    email = _require_user(request)
    created = _pat_store(request).create(
        user_email=email,
        tenant=getattr(request.state, "tenant", "default"),
        role=getattr(request.state, "role", "viewer"),
        name=body.name)
    return created


@router.delete("/settings/tokens/{token_id}")
def revoke_token(token_id: int, request: Request):
    """Revoke one of the caller's personal API tokens. Effective immediately."""
    email = _require_user(request)
    if not _pat_store(request).revoke(user_email=email, token_id=token_id):
        raise HTTPException(404, "token not found")
    return {"revoked": token_id}
