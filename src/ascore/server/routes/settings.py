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

router = APIRouter(tags=["settings"])


def _store(request: Request) -> KeyStore:
    return KeyStore(request.state.reg.engine, request.state.cfg)


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
