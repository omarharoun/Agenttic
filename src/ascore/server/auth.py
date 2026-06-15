"""API authentication & authorization — shared tokens mapped to roles.

Auth is config/env driven and simple-but-real. Token resolution:
* ``ASCORE_API_TOKEN`` (env) or ``auth.token`` (config) — the admin token.
* ``auth.tokens`` — a {token: role} map for additional principals.

Roles form a hierarchy: **viewer** < **operator** < **admin**.
* viewer  — read-only (all GET endpoints).
* operator — also trigger runs, approve the human gate, manage the agent
  catalog, write workflows, ingest live traffic.
* admin   — everything (reserved for future tenant/user management).

If no token is configured at all the API is open and every request is treated
as ``admin`` — this keeps local dev and the mocked test suite frictionless. Set
``auth.required: true`` to refuse to start without a token (fail closed).

EventSource (SSE) can't send headers, so the token is also accepted as a
``?token=`` query parameter; ``Authorization: Bearer`` / ``X-API-Key`` headers
work everywhere else.
"""

from __future__ import annotations

import os
import secrets

from fastapi import HTTPException, Request, status

ROLES = {"viewer": 0, "operator": 1, "admin": 2}


def configured_token(cfg: dict) -> str:
    """The admin token (env wins over config), or "" if unset."""
    env = os.environ.get("ASCORE_API_TOKEN", "").strip()
    if env:
        return env
    return str((cfg.get("auth", {}) or {}).get("token", "") or "").strip()


def _role_tokens(cfg: dict) -> dict:
    return (cfg.get("auth", {}) or {}).get("tokens", {}) or {}


def auth_enabled(cfg: dict) -> bool:
    """True when any token (admin or role-mapped) is configured."""
    return bool(configured_token(cfg) or _role_tokens(cfg))


def auth_required(cfg: dict) -> bool:
    return bool((cfg.get("auth", {}) or {}).get("required", False))


def check_startup(cfg: dict) -> None:
    """Fail closed: if auth is marked required, a token must be resolvable."""
    if auth_required(cfg) and not auth_enabled(cfg):
        raise RuntimeError(
            "auth.required is true but no token is set — export ASCORE_API_TOKEN, "
            "or set auth.token / auth.tokens in config.yaml")


def resolve_role(cfg: dict, provided: str | None) -> str | None:
    """Role for a presented token, or None if it matches nothing."""
    if not provided:
        return None
    admin = configured_token(cfg)
    if admin and secrets.compare_digest(provided, admin):
        return "admin"
    for tok, role in _role_tokens(cfg).items():
        if secrets.compare_digest(provided, str(tok)):
            return role if role in ROLES else "viewer"
    return None


def _provided_token(request: Request) -> str | None:
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    api_key = request.headers.get("x-api-key")
    if api_key:
        return api_key.strip()
    tok = request.query_params.get("token")  # EventSource can't set headers
    return tok.strip() if tok else None


def require_auth(request: Request) -> None:
    """Authenticate the request and stash its role on ``request.state.role``.
    No-op-open (role=admin) when no token is configured."""
    cfg = request.app.state.cfg
    if not auth_enabled(cfg):
        request.state.role = "admin"
        return
    role = resolve_role(cfg, _provided_token(request))
    if role is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="missing or invalid API token",
            headers={"WWW-Authenticate": "Bearer"})
    request.state.role = role


def require_role(min_role: str):
    """Dependency factory: require at least ``min_role``. Relies on require_auth
    (router-level) having set request.state.role first."""
    threshold = ROLES[min_role]

    def dep(request: Request) -> None:
        role = getattr(request.state, "role", None)
        if role is None or ROLES.get(role, -1) < threshold:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"this action requires the '{min_role}' role")
    return dep


require_operator = require_role("operator")
require_admin = require_role("admin")
