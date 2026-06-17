"""Outbound email — configurable SMTP with a safe console fallback.

The mail settings are config-driven (``email`` section) with environment
overrides (``SMTP_HOST/PORT/USER/PASS/FROM``), so the same code path works
against a self-hosted server, a relay/smarthost, or — when nothing is
configured — a console fallback that logs the message instead of sending.
That fallback means signup never fails just because mail isn't wired yet
(the verification link is logged for operators), rather than silently
pretending to deliver.

NOTE: this droplet has outbound 25/465/587 blocked; a relay reachable on an
open port (e.g. 2525) or an HTTP-API provider is required for real delivery.
"""

from __future__ import annotations

import logging
import os
import smtplib
import ssl
from email.message import EmailMessage

logger = logging.getLogger(__name__)


class MailSettings:
    def __init__(self, cfg: dict):
        email = (cfg.get("email", {}) or {})
        smtp = (email.get("smtp", {}) or {})
        # env wins over config; *_FILE secrets are hydrated into env at startup
        self.host = os.environ.get("SMTP_HOST", smtp.get("host", "")) or ""
        self.port = int(os.environ.get("SMTP_PORT", smtp.get("port", 587)) or 587)
        self.user = os.environ.get("SMTP_USER", smtp.get("user", "")) or ""
        self.password = os.environ.get("SMTP_PASS", smtp.get("password", "")) or ""
        self.starttls = _as_bool(os.environ.get("SMTP_STARTTLS"), smtp.get("starttls", True))
        self.ssl = _as_bool(os.environ.get("SMTP_SSL"), smtp.get("ssl", False))
        self.sender = (os.environ.get("SMTP_FROM")
                       or email.get("from", "noreply@agenttic.io"))
        self.timeout = float(smtp.get("timeout_seconds", 15))

    @property
    def configured(self) -> bool:
        return bool(self.host)


def _as_bool(env_val, default: bool) -> bool:
    if env_val is None:
        return bool(default)
    return str(env_val).strip().lower() in ("1", "true", "yes", "on")


class Mailer:
    """Sends mail per :class:`MailSettings`; logs instead when unconfigured."""

    def __init__(self, cfg: dict):
        self.settings = MailSettings(cfg)

    def send(self, to: str, subject: str, text: str, html: str | None = None) -> bool:
        """Returns True if handed to an SMTP server, False if only logged.
        Never raises on a delivery error — the caller's flow must not break."""
        s = self.settings
        if not s.configured:
            logger.info("[mail:console] to=%s subject=%r\n%s", to, subject, text)
            return False
        msg = EmailMessage()
        msg["From"] = s.sender
        msg["To"] = to
        msg["Subject"] = subject
        msg.set_content(text)
        if html:
            msg.add_alternative(html, subtype="html")
        try:
            if s.ssl:
                with smtplib.SMTP_SSL(s.host, s.port, timeout=s.timeout,
                                      context=ssl.create_default_context()) as srv:
                    self._auth_send(srv, msg)
            else:
                with smtplib.SMTP(s.host, s.port, timeout=s.timeout) as srv:
                    if s.starttls:
                        srv.starttls(context=ssl.create_default_context())
                    self._auth_send(srv, msg)
            logger.info("[mail] sent to=%s subject=%r via %s:%s", to, subject, s.host, s.port)
            return True
        except Exception as exc:  # noqa: BLE001 — delivery failure must not break signup
            logger.error("[mail] send failed to=%s via %s:%s: %s",
                         to, s.host, s.port, exc)
            return False

    def _auth_send(self, srv: smtplib.SMTP, msg: EmailMessage) -> None:
        if self.settings.user:
            srv.login(self.settings.user, self.settings.password)
        srv.send_message(msg)
