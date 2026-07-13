"""The "Connect your agent" HTTP surface — the safe webhook way for a normal
user to point Agenttic at their live agent so the Safety Battery can test it.

    GET    /api/connect          masked status of the saved connection
    PUT    /api/connect          save/update the connection (url, auth, mapping, consent)
    DELETE /api/connect          remove the saved connection
    POST   /api/connect/test     send ONE harmless probe; return the agent's reply
    POST   /api/connect/consent  record/clear the authorization confirmation

Safety lives in the layers this route reuses: SSRF validation at save
(``ConnectionStore.save``) and at request time (the black-box adapter transport),
the auth-header secret encrypted at rest + masked on read, the
``X-Agenttic-Safety-Test`` header on every request, and a one-shot, non-storing
test probe. The scan route (``routes/scan.py``) enforces the consent gate before
running. We only ever send text and read text — never execute tools.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from agenttic import connect
from agenttic.security import UnsafeURLError
from agenttic.server.auth import require_operator
from agenttic.server.connections import ConnectionStore

router = APIRouter(tags=["connect"])


def _store(request: Request) -> ConnectionStore:
    return ConnectionStore(request.state.reg.engine, request.state.cfg)


def _tenant(request: Request) -> str:
    return getattr(request.state, "tenant", "default")


class ConnectionBody(BaseModel):
    endpoint_url: str = ""
    agent_name: str = ""
    preset: str = "generic"             # openai | generic | custom
    request_field: str = ""             # generic/custom: the prompt field
    response_path: str = ""             # dotted path to the reply (e.g. choices[0].message.content)
    model: str = ""                     # openai preset model
    auth_header_name: str = ""          # e.g. "Authorization" (not secret)
    auth_header_value: str = ""         # the SECRET — encrypted at rest, never returned
    consent: bool = False               # "I own / am authorized to test this agent"


class ConsentBody(BaseModel):
    consent: bool = False


@router.get("/connect")
def get_connection(request: Request):
    """Masked status of the tenant's saved connection (never the auth secret)."""
    return _store(request).status(_tenant(request))


@router.put("/connect", dependencies=[Depends(require_operator)])
def save_connection(body: ConnectionBody, request: Request):
    """Save/update the connection. Validates the URL for SSRF at save time and
    encrypts the auth header value at rest."""
    try:
        return _store(request).save(
            _tenant(request),
            endpoint_url=body.endpoint_url, agent_name=body.agent_name,
            preset=body.preset, request_field=body.request_field,
            response_path=body.response_path, model=body.model,
            auth_header_name=body.auth_header_name,
            auth_header_value=body.auth_header_value, consent=body.consent)
    except UnsafeURLError as exc:
        raise HTTPException(400, f"That endpoint isn't allowed: {exc}")
    except ValueError as exc:
        raise HTTPException(422, str(exc))


@router.delete("/connect", dependencies=[Depends(require_operator)])
def delete_connection(request: Request):
    _store(request).delete(_tenant(request))
    return _store(request).status(_tenant(request))


@router.post("/connect/consent", dependencies=[Depends(require_operator)])
def set_consent(body: ConsentBody, request: Request):
    try:
        return _store(request).set_consent(_tenant(request), body.consent)
    except ValueError as exc:
        raise HTTPException(422, str(exc))


@router.post("/connect/test", dependencies=[Depends(require_operator)])
def test_connection(body: ConnectionBody, request: Request):
    """Send ONE harmless probe through the configured mapping and return the
    agent's actual reply (so the user can confirm it's wired) or a clear, fix-it
    error. Validates SSRF at request time; stores nothing.

    The auth secret may come from the request body (testing a not-yet-saved
    config) or, if blank, from the saved connection (re-test without re-typing)."""
    cfg = request.state.cfg
    if not body.endpoint_url.strip():
        raise HTTPException(422, "Paste your agent's endpoint URL to test it.")

    # SSRF gate before we build/dial anything (the adapter re-checks too).
    try:
        from agenttic.security import validate_blackbox_url
        validate_blackbox_url(body.endpoint_url.strip(), cfg=cfg, allow_unresolved=True)
    except UnsafeURLError as exc:
        return {"ok": False, "error": connect._trace_error_msg(f"UnsafeURLError: {exc}")}

    auth_value = body.auth_header_value.strip()
    if not auth_value and body.auth_header_name.strip():
        saved = _store(request).get(_tenant(request))
        if saved and saved.auth_header_name == body.auth_header_name.strip():
            auth_value = saved.auth_header_value

    conn = connect.ConnectionConfig(
        endpoint_url=body.endpoint_url.strip(),
        agent_name=body.agent_name.strip() or "your-agent",
        preset=body.preset, request_field=body.request_field,
        response_path=body.response_path, model=body.model,
        auth_header_name=body.auth_header_name.strip(), auth_header_value=auth_value)
    adapter = connect.build_connection_adapter(cfg, conn)
    result = connect.probe(adapter)
    return {"ok": result.ok, "reply": result.reply, "error": result.error or None,
            "mapping": conn.mapping().public()}
