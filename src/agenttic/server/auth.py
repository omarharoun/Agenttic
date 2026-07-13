"""API authentication & authorization — shared tokens mapped to roles.

Auth is config/env driven and simple-but-real. Token resolution:
* ``ASCORE_API_TOKEN`` (env) or ``auth.token`` (config) — the admin token.
* ``auth.tokens`` — a {token: role} map for additional principals.
* personal API tokens (PATs) — self-service ``agt_…`` tokens a logged-in user
  mints in Settings; presenting one authenticates AS that user (their tenant +
  role). Stored hashed; see ``server/pats.py``.

Explicit-token precedence: a configured shared/admin or role token is checked
first (constant-time, no DB hit); otherwise a PAT is resolved from the global
DB. An explicit token (either kind) always wins over a session cookie.

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

# Roles form a capability hierarchy. ``evaluator`` (SPEC-2 M6) sits at the
# operator tier — it can RUN certifications — but is an *independent* principal:
# it certifies agents it does not own, its dossiers are attested "independent"
# (computed from tenancy, never selected), and it is isolated to certified-run
# artifacts (see certification isolation). Adding a role is migration-safe: the
# role is a plain string column on users/PATs; existing rows are unaffected.
ROLES = {"viewer": 0, "evaluator": 1, "operator": 1, "admin": 2}

#: Roles that mark an independent (third-party) evaluator principal.
EVALUATOR_ROLES = {"evaluator"}


def is_evaluator(role: str | None) -> bool:
    return role in EVALUATOR_ROLES

SESSION_COOKIE = "ascore_session"
CSRF_COOKIE = "ascore_csrf"          # readable (double-submit) CSRF token
CSRF_HEADER = "x-csrf-token"
_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def configured_token(cfg: dict) -> str:
    """The admin token (env / *_FILE wins over config), or "" if unset."""
    from agenttic.secrets import get_secret
    env = get_secret("ASCORE_API_TOKEN")
    if env:
        return env
    return str((cfg.get("auth", {}) or {}).get("token", "") or "").strip()


def _role_tokens(cfg: dict) -> dict:
    return (cfg.get("auth", {}) or {}).get("tokens", {}) or {}


def auth_required(cfg: dict) -> bool:
    return bool((cfg.get("auth", {}) or {}).get("required", False))


def auth_enabled(cfg: dict) -> bool:
    """True when the API requires authentication — any token configured, or
    ``auth.required`` (which user-session login also satisfies)."""
    return bool(configured_token(cfg) or _role_tokens(cfg) or auth_required(cfg))


def check_startup(cfg: dict) -> None:
    """Fail closed: if auth is required, there must be *some* way to get in —
    a configured token, an env-bootstrapped admin, or open signup."""
    if not auth_required(cfg):
        return
    has_token = bool(configured_token(cfg) or _role_tokens(cfg))
    from agenttic.secrets import get_secret
    from agenttic._env import get_env
    has_admin_bootstrap = bool(get_env("ASCORE_ADMIN_EMAIL")
                               and get_secret("ASCORE_ADMIN_PASSWORD"))
    allow_signup = bool((cfg.get("auth", {}) or {}).get("allow_signup", False))
    if not (has_token or has_admin_bootstrap or allow_signup):
        raise RuntimeError(
            "auth.required is true but no way to authenticate — set "
            "ASCORE_API_TOKEN, bootstrap an admin (ASCORE_ADMIN_EMAIL/"
            "ASCORE_ADMIN_PASSWORD), or enable auth.allow_signup")


def resolve_principal(cfg: dict, provided: str | None) -> tuple[str, str] | None:
    """(role, tenant) for a presented token, or None if it matches nothing.

    A role-token's value is either a plain role string (tenant = "default") or
    a ``{role, tenant}`` mapping. The admin token's tenant is
    ``auth.admin_tenant`` (default "default")."""
    if not provided:
        return None
    admin = configured_token(cfg)
    if admin and secrets.compare_digest(provided, admin):
        return "admin", str((cfg.get("auth", {}) or {}).get("admin_tenant", "default"))
    for tok, spec in _role_tokens(cfg).items():
        if secrets.compare_digest(provided, str(tok)):
            if isinstance(spec, dict):
                role = spec.get("role", "viewer")
                return (role if role in ROLES else "viewer",
                        str(spec.get("tenant", "default")))
            return (spec if spec in ROLES else "viewer", "default")
    return None


def resolve_role(cfg: dict, provided: str | None) -> str | None:
    p = resolve_principal(cfg, provided)
    return p[0] if p else None


def _provided_token(request: Request) -> str | None:
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    api_key = request.headers.get("x-api-key")
    if api_key:
        return api_key.strip()
    tok = request.query_params.get("token")  # EventSource can't set headers
    return tok.strip() if tok else None


def _resolve_pat(request: Request, provided: str) -> tuple[str, str, str] | None:
    """(role, tenant, email) for a personal API token, or None. Looks the token
    up (hashed) in the GLOBAL engine where users/PATs live."""
    from agenttic.server.pats import PatStore, looks_like_pat
    if not looks_like_pat(provided):
        return None
    try:
        engine = request.app.state.reg.engine
    except AttributeError:
        return None
    try:
        return PatStore(engine).resolve(provided)
    except Exception:  # noqa: BLE001 — a lookup failure must not 500 the request
        return None


def _set_principal(request: Request, role: str, tenant: str,
                   *, email: str | None, method: str) -> None:
    request.state.role = role
    request.state.tenant = tenant
    request.state.user_email = email
    request.state.auth_method = method


def require_auth(request: Request) -> None:
    """Authenticate the request and stash role/tenant/email on request.state.

    Precedence: an explicit **bearer / X-API-Key / ?token** wins — if present it
    must be valid (else 401). Otherwise a **session cookie** (browser login) is
    used; cookie-authenticated *unsafe* methods also require a matching CSRF
    token (double-submit). No-op-open (admin) when auth is disabled."""
    cfg = request.app.state.cfg
    if not auth_enabled(cfg):
        _set_principal(request, "admin", "default", email=None, method="open")
        return

    provided = _provided_token(request)
    if provided is not None:  # explicit token path
        # Precedence within the explicit-token path: a configured shared/admin
        # or role token wins (constant-time compare, no DB hit); otherwise try a
        # personal API token (PAT), which authenticates AS its owning user with
        # that user's tenant + role.
        principal = resolve_principal(cfg, provided)
        if principal is not None:
            _set_principal(request, principal[0], principal[1], email=None,
                           method="token")
            return
        pat = _resolve_pat(request, provided)
        if pat is not None:
            role, tenant, email = pat
            _set_principal(request, role, tenant, email=email, method="pat")
            return
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid API token",
            headers={"WWW-Authenticate": "Bearer"})

    token = request.cookies.get(SESSION_COOKIE)
    if token:
        from agenttic.server.sessions import session_secret, verify_session
        body = verify_session(token, session_secret(cfg))
        if body:
            if request.method not in _SAFE_METHODS:
                c = request.cookies.get(CSRF_COOKIE)
                h = request.headers.get(CSRF_HEADER)
                if not (c and h and secrets.compare_digest(c, h)):
                    raise HTTPException(
                        status_code=status.HTTP_403_FORBIDDEN,
                        detail="CSRF token missing or invalid")
            _set_principal(request, body.get("role", "viewer"),
                           body.get("tenant", "default"),
                           email=body.get("email"), method="session")
            return

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="authentication required (API token or login session)",
        headers={"WWW-Authenticate": "Bearer"})


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
