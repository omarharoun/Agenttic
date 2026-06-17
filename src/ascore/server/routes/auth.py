"""User authentication endpoints (public — no require_auth): signup, login,
logout. Issues an httponly session cookie (+ a readable CSRF cookie for
double-submit protection). Brute-force protection via per-email lockout.

Users live in the default/shared engine (global by email); each carries a role
+ tenant that drive the same RBAC and tenant scoping as the bearer token.
"""

from __future__ import annotations

import re
import secrets
import time

from fastapi import APIRouter, HTTPException, Request, Response, status
from pydantic import BaseModel

from ascore.server.auth import CSRF_COOKIE, SESSION_COOKIE
from ascore.server.users import DuplicateUserError, UserStore
from ascore.server.verification import VerificationStore, send_verification

router = APIRouter(tags=["auth"])


def _email_cfg(cfg: dict) -> dict:
    return cfg.get("email", {}) or {}


def _require_verification(cfg: dict) -> bool:
    return bool(_email_cfg(cfg).get("require_verification", False))

# per-email failed-login tracker (in-process; multi-worker uses per-worker
# counts — acceptable alongside the global rate limiter).
_attempts: dict[str, tuple[int, float]] = {}


class Credentials(BaseModel):
    email: str  # validated in UserStore (no email-validator dep)
    password: str


def _store(request: Request) -> UserStore:
    # users are global → the default/shared engine (Postgres shared DB, or the
    # default-tenant SQLite file)
    return UserStore(request.app.state.reg.engine)


def _cfg(request: Request) -> dict:
    return request.app.state.cfg


def _lockout(email: str, cfg: dict) -> None:
    sec = cfg.get("security", {}) or {}
    max_attempts = int(sec.get("login_max_attempts", 5))
    fails, until = _attempts.get(email, (0, 0.0))
    if fails >= max_attempts and time.time() < until:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                            detail="too many failed logins; try again later")


def _record_fail(email: str, cfg: dict) -> None:
    sec = cfg.get("security", {}) or {}
    window = int(sec.get("login_lockout_seconds", 900))
    fails, _ = _attempts.get(email, (0, 0.0))
    _attempts[email] = (fails + 1, time.time() + window)


def _issue_session(response: Response, cfg: dict, user) -> str:
    """Set the session + CSRF cookies; return the CSRF token (also echoed in the
    body so the SPA can use it immediately)."""
    from ascore.server.sessions import session_secret, sign_session
    ttl = int((cfg.get("auth", {}) or {}).get("session_ttl_hours", 168)) * 3600
    token = sign_session(
        {"uid": user.id, "email": user.email, "role": user.role,
         "tenant": user.tenant_id}, session_secret(cfg), ttl)
    secure = bool((cfg.get("auth", {}) or {}).get("cookie_secure", False))
    csrf = secrets.token_urlsafe(32)
    common = dict(max_age=ttl, secure=secure, samesite="lax", path="/")
    response.set_cookie(SESSION_COOKIE, token, httponly=True, **common)
    response.set_cookie(CSRF_COOKIE, csrf, httponly=False, **common)
    return csrf


def _new_tenant_slug(email: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", email.split("@")[0].lower()).strip("-")[:24]
    return f"{base or 'workspace'}-{secrets.token_hex(3)}"


@router.post("/auth/signup")
def signup(creds: Credentials, request: Request, response: Response):
    cfg = _cfg(request)
    if not (cfg.get("auth", {}) or {}).get("allow_signup", False):
        raise HTTPException(403, "signup is disabled")
    role = str((cfg.get("auth", {}) or {}).get("signup_role", "admin"))
    tenant = _new_tenant_slug(creds.email)  # each signup gets its own workspace
    require_verify = _require_verification(cfg)
    try:
        user = _store(request).create_user(
            str(creds.email), creds.password, role=role, tenant=tenant,
            verified=not require_verify)  # unverified only when we'll gate on it
    except DuplicateUserError:
        raise HTTPException(409, "an account with that email already exists")
    except ValueError as exc:
        raise HTTPException(422, str(exc))

    # always send a verification email; when required, we DON'T start a session
    # until the address is confirmed.
    if _email_cfg(cfg).get("enabled", False) or require_verify:
        try:
            send_verification(cfg, request.app.state.reg.engine, user.email)
        except Exception:  # noqa: BLE001 — mail issues must not fail signup
            pass

    if require_verify:
        return {"email": user.email, "needs_verification": True}
    csrf = _issue_session(response, cfg, user)
    return {"email": user.email, "role": user.role, "tenant": user.tenant_id,
            "verified": user.verified, "csrf_token": csrf}


class VerifyToken(BaseModel):
    token: str


@router.post("/auth/verify")
def verify_email(body: VerifyToken, request: Request, response: Response):
    cfg = _cfg(request)
    status_, email = VerificationStore(
        request.app.state.reg.engine).consume(body.token)
    if status_ != "ok":
        msg = {"invalid": "this verification link is not valid",
               "expired": "this verification link has expired — request a new one",
               "used": "this email is already verified — please log in"}[status_]
        code = 410 if status_ in ("expired", "used") else 400
        raise HTTPException(code, msg)
    # convenience: start a session straight away on successful verification
    user = _store(request).get_by_email(email or "")
    if user is not None:
        csrf = _issue_session(response, cfg, user)
        return {"verified": True, "email": user.email, "role": user.role,
                "tenant": user.tenant_id, "csrf_token": csrf}
    return {"verified": True, "email": email}


class ResendBody(BaseModel):
    email: str


@router.post("/auth/resend-verification")
def resend_verification(body: ResendBody, request: Request):
    cfg = _cfg(request)
    user = _store(request).get_by_email(body.email)
    # only send for a real, still-unverified account; always 200 (no enumeration)
    if user is not None and not user.verified:
        try:
            send_verification(cfg, request.app.state.reg.engine, user.email)
        except Exception:  # noqa: BLE001
            pass
    return {"ok": True}


@router.post("/auth/login")
def login(creds: Credentials, request: Request, response: Response):
    cfg = _cfg(request)
    email = str(creds.email).lower()
    _lockout(email, cfg)
    user = _store(request).authenticate(email, creds.password)
    if not user:
        _record_fail(email, cfg)
        raise HTTPException(401, "invalid email or password")
    _attempts.pop(email, None)  # reset on success
    if _require_verification(cfg) and not user.verified:
        # credentials are correct but the address isn't confirmed yet
        raise HTTPException(403, detail={
            "error": "email not verified",
            "email": user.email,
            "needs_verification": True})
    csrf = _issue_session(response, cfg, user)
    return {"email": user.email, "role": user.role, "tenant": user.tenant_id,
            "verified": user.verified, "csrf_token": csrf}


@router.post("/auth/logout")
def logout(response: Response):
    response.delete_cookie(SESSION_COOKIE, path="/")
    response.delete_cookie(CSRF_COOKIE, path="/")
    return {"ok": True}
