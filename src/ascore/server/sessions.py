"""Stateless signed session tokens (HMAC-SHA256, JWT-shaped) for cookie auth.

Kept dependency-free: a compact ``payload.signature`` token signed with a
server secret, carrying the user id, email, role, tenant and an expiry. Being
stateless means it works across workers with no shared session store; the token
lives in an httponly cookie so XSS can't read it.

The secret comes from ``ASCORE_SESSION_SECRET`` (or ``auth.session_secret``),
falling back to the API admin token so a single-secret deployment still works.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time


def _b64e(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode("ascii")


def _b64d(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def session_secret(cfg: dict) -> str:
    """Resolve the signing secret (env > config > derived from the API token)."""
    from ascore.secrets import get_secret
    env = get_secret("ASCORE_SESSION_SECRET")
    if env:
        return env
    auth = cfg.get("auth", {}) or {}
    if auth.get("session_secret"):
        return str(auth["session_secret"])
    from ascore.server.auth import configured_token
    tok = configured_token(cfg)
    return f"session::{tok}" if tok else "ascore-dev-insecure-session-secret"


def sign_session(payload: dict, secret: str, ttl_seconds: int) -> str:
    body = {**payload, "exp": int(time.time()) + ttl_seconds}
    p = _b64e(json.dumps(body, separators=(",", ":"), sort_keys=True).encode())
    sig = _b64e(hmac.new(secret.encode(), p.encode(), hashlib.sha256).digest())
    return f"{p}.{sig}"


def verify_session(token: str, secret: str) -> dict | None:
    try:
        p, sig = token.split(".", 1)
        expected = _b64e(hmac.new(secret.encode(), p.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(sig, expected):
            return None
        body = json.loads(_b64d(p))
        if int(body.get("exp", 0)) < int(time.time()):
            return None
        return body
    except Exception:  # noqa: BLE001 — any malformed token is just invalid
        return None
