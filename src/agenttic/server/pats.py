"""Personal API tokens (PATs) — self-service programmatic REST access.

A user mints a named token in Settings; it is shown once, then only its
SHA-256 hash is persisted (never plaintext, never logged). Presenting it as
``Authorization: Bearer agt_…`` authenticates the request AS that user — the
same tenant + role as their login — so every existing /api endpoint works under
a PAT. Revocation (``revoked_at``) takes effect immediately.

Tokens are high-entropy random strings, so a fast deterministic hash (SHA-256)
is the right primitive for O(1) lookup — bcrypt (for low-entropy passwords)
would be both unnecessary and too slow to run on every API request.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timezone

from sqlmodel import Session, select

from ascore.registry.sqlite_store import PersonalApiTokenRow

TOKEN_PREFIX = "agt_"          # agenttic token (distinct from sk-ant-… Anthropic)
_TOKEN_BYTES = 32             # 256 bits of entropy


def _now() -> datetime:
    return datetime.now(timezone.utc)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.strip().encode("utf-8")).hexdigest()


def mask(last4: str) -> str:
    return f"{TOKEN_PREFIX}…{last4}"


def looks_like_pat(token: str | None) -> bool:
    return bool(token) and token.strip().startswith(TOKEN_PREFIX)


class PatStore:
    """CRUD + resolution for personal API tokens. Bound to the GLOBAL engine
    (the default-tenant DB), where users/PATs live."""

    def __init__(self, engine):
        self.engine = engine

    def create(self, *, user_email: str, tenant: str, role: str,
               name: str) -> dict:
        """Mint a token. Returns the PLAINTEXT token exactly once (caller must
        show it then forget it) plus the stored masked metadata."""
        name = (name or "").strip() or "api token"
        token = TOKEN_PREFIX + secrets.token_urlsafe(_TOKEN_BYTES)
        row = PersonalApiTokenRow(
            token_hash=hash_token(token), name=name,
            user_email=user_email.strip().lower(), tenant_id=tenant, role=role,
            last4=token[-4:], created_at=_now())
        with Session(self.engine) as s:
            s.add(row)
            s.commit()
            s.refresh(row)
        return {"id": row.id, "name": row.name, "token": token,
                "masked": mask(row.last4), "created_at": row.created_at.isoformat()}

    def list(self, user_email: str) -> list[dict]:
        """Active (non-revoked) tokens for a user — masked only, never the
        plaintext (which no longer exists in any form)."""
        email = user_email.strip().lower()
        with Session(self.engine) as s:
            rows = s.exec(select(PersonalApiTokenRow).where(
                PersonalApiTokenRow.user_email == email,
                PersonalApiTokenRow.revoked_at.is_(None))  # type: ignore[union-attr]
            ).all()
        rows.sort(key=lambda r: r.created_at, reverse=True)
        return [{"id": r.id, "name": r.name, "masked": mask(r.last4),
                 "created_at": r.created_at.isoformat(),
                 "last_used_at": r.last_used_at.isoformat() if r.last_used_at else None}
                for r in rows]

    def revoke(self, *, user_email: str, token_id: int) -> bool:
        """Revoke one of the user's tokens (scoped to owner). Effective
        immediately. Returns False if not found / already revoked / not theirs."""
        email = user_email.strip().lower()
        with Session(self.engine) as s:
            row = s.get(PersonalApiTokenRow, token_id)
            if row is None or row.user_email != email or row.revoked_at is not None:
                return False
            row.revoked_at = _now()
            s.add(row)
            s.commit()
            return True

    def resolve(self, token: str) -> tuple[str, str, str] | None:
        """(role, tenant, user_email) for a presented token, or None if it is
        unknown or revoked. Bumps ``last_used_at`` best-effort."""
        if not looks_like_pat(token):
            return None
        h = hash_token(token)
        with Session(self.engine) as s:
            row = s.exec(select(PersonalApiTokenRow).where(
                PersonalApiTokenRow.token_hash == h)).first()
            if row is None or row.revoked_at is not None:
                return None
            row.last_used_at = _now()
            s.add(row)
            s.commit()
            return row.role, row.tenant_id, row.user_email
