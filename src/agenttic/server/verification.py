"""Email verification: single-use, expiring tokens + the verification email.

A token is a random url-safe string stored in ``email_tokens``. Issuing a new
token invalidates any prior unused ones for that email (so a resend supersedes
the old link). Consuming checks existence, expiry and reuse, then flips
``users.verified``. The mailer is config-driven (see :mod:`agenttic.server.mailer`).
"""

from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone

from sqlmodel import Session, select

from agenttic.registry.sqlite_store import EmailTokenRow, UserRow
from agenttic.server.mailer import Mailer


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt: datetime) -> datetime:
    # SQLite round-trips naive datetimes; treat stored values as UTC
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


class VerificationStore:
    def __init__(self, engine):
        self.engine = engine

    def issue(self, email: str, ttl_hours: int = 24) -> str:
        email = email.strip().lower()
        token = secrets.token_urlsafe(32)
        with Session(self.engine) as s:
            # supersede prior unused verify tokens for this address
            for row in s.exec(select(EmailTokenRow).where(
                    EmailTokenRow.email == email,
                    EmailTokenRow.purpose == "verify",
                    EmailTokenRow.used_at == None)).all():  # noqa: E711
                row.used_at = _now()
                s.add(row)
            s.add(EmailTokenRow(
                token=token, email=email, purpose="verify",
                created_at=_now(), expires_at=_now() + timedelta(hours=ttl_hours)))
            s.commit()
        return token

    def consume(self, token: str) -> tuple[str, str | None]:
        """Return (status, email). status is one of:
        ok | invalid | expired | used. On ok, the user is marked verified."""
        with Session(self.engine) as s:
            row = s.exec(select(EmailTokenRow).where(
                EmailTokenRow.token == token)).first()
            if row is None:
                return ("invalid", None)
            if row.used_at is not None:
                return ("used", row.email)
            if _aware(row.expires_at) < _now():
                return ("expired", row.email)
            row.used_at = _now()
            s.add(row)
            user = s.exec(select(UserRow).where(
                UserRow.email == row.email)).first()
            if user is not None:
                user.verified = True
                s.add(user)
            s.commit()
            return ("ok", row.email)


def send_verification(cfg: dict, engine, email: str) -> str:
    """Issue a token and email the verify link. Returns the token (handy for
    tests / console mode). Safe to call even when mail isn't configured."""
    em = (cfg.get("email", {}) or {})
    ttl = int(em.get("token_ttl_hours", 24))
    base = em.get("verify_url_base", "https://agenttic.io/verify")
    token = VerificationStore(engine).issue(email, ttl)
    link = f"{base}?token={token}"
    text = (
        "Welcome to Agenttic — safety testing for AI agents.\n\n"
        "Confirm your email address to activate your account:\n\n"
        f"{link}\n\n"
        f"This link expires in {ttl} hours. If you didn't create an account, "
        "you can ignore this email.\n")
    html = (
        '<div style="font-family:sans-serif;line-height:1.6">'
        "<h2>Confirm your email</h2>"
        "<p>Welcome to Agenttic — safety testing for AI agents.</p>"
        f'<p><a href="{link}" style="background:#C96442;color:#fff;'
        'padding:10px 18px;border-radius:8px;text-decoration:none;'
        'display:inline-block">Verify my email</a></p>'
        f'<p style="color:#666">Or paste this link: <br>{link}</p>'
        f'<p style="color:#999">This link expires in {ttl} hours.</p></div>')
    Mailer(cfg).send(email, "Confirm your Agenttic email", text, html)
    return token
