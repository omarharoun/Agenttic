"""API authentication — a single shared bearer token, config/env driven.

Deliberately simple but real: every `/api` route (including the SSE stream and
the human-approval gate) requires the token once one is configured. The token
resolves from the ``ASCORE_API_TOKEN`` environment variable first (so the secret
never has to live in config.yaml), falling back to ``auth.token`` in config.

If no token is configured the dependency is a no-op (open) — this keeps local
dev and the mocked test suite frictionless. Set ``auth.required: true`` to make
the server refuse to start without a token, which is what a real deployment does.

EventSource (SSE) cannot send headers, so the token is also accepted as a
``?token=`` query parameter; `Authorization: Bearer <t>` and `X-API-Key: <t>`
headers are accepted everywhere else.
"""

from __future__ import annotations

import os
import secrets

from fastapi import HTTPException, Request, status


def configured_token(cfg: dict) -> str:
    """The active API token, or "" when auth is disabled."""
    env = os.environ.get("ASCORE_API_TOKEN", "").strip()
    if env:
        return env
    return str((cfg.get("auth", {}) or {}).get("token", "") or "").strip()


def auth_required(cfg: dict) -> bool:
    return bool((cfg.get("auth", {}) or {}).get("required", False))


def check_startup(cfg: dict) -> None:
    """Fail closed: if auth is marked required, a token must be resolvable."""
    if auth_required(cfg) and not configured_token(cfg):
        raise RuntimeError(
            "auth.required is true but no token is set — export ASCORE_API_TOKEN "
            "or set auth.token in config.yaml")


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
    """FastAPI dependency. No-op when no token is configured; otherwise enforces
    a constant-time token match on every request."""
    token = configured_token(request.app.state.cfg)
    if not token:
        return
    provided = _provided_token(request)
    if not provided or not secrets.compare_digest(provided, token):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="missing or invalid API token",
            headers={"WWW-Authenticate": "Bearer"})
