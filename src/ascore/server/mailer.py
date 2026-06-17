"""Outbound email — provider-selectable, with a safe console fallback.

Providers (``email.provider``):
  * ``resend``  — Resend HTTPS API (POST https://api.resend.com/emails). Works
                  where SMTP ports are blocked but 443 is open (this droplet).
                  Needs ``RESEND_API_KEY``.
  * ``smtp``    — classic SMTP relay (``SMTP_HOST/PORT/USER/PASS``).
  * ``console`` — log the message instead of sending (default when nothing is
                  configured) so signup never fails just because mail isn't
                  wired yet.
  * ``auto``    — resend if RESEND_API_KEY is set, else smtp if SMTP_HOST is
                  set, else console.

Delivery failures are logged and never raised — the caller's flow must not
break. Secrets are read from the environment (``*_FILE`` hydrated at startup)
and never logged.
"""

from __future__ import annotations

import json
import logging
import os
import smtplib
import ssl
import urllib.request
from email.message import EmailMessage

logger = logging.getLogger(__name__)

RESEND_ENDPOINT = "https://api.resend.com/emails"


def _as_bool(env_val, default: bool) -> bool:
    if env_val is None:
        return bool(default)
    return str(env_val).strip().lower() in ("1", "true", "yes", "on")


def _http_post_json(url: str, headers: dict, payload: dict, timeout: float = 15.0):
    """POST JSON; return (status_code, body_text). Isolated for test mocking."""
    data = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=data, method="POST",
                                 headers={"Content-Type": "application/json", **headers})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode()
    except urllib.error.HTTPError as exc:  # 4xx/5xx still carry a body
        return exc.code, exc.read().decode(errors="replace")


class MailSettings:
    def __init__(self, cfg: dict):
        email = (cfg.get("email", {}) or {})
        smtp = (email.get("smtp", {}) or {})
        self.sender = (os.environ.get("SMTP_FROM")
                       or email.get("from", "noreply@agenttic.io"))
        self.resend_api_key = os.environ.get("RESEND_API_KEY", "") or ""
        # SMTP
        self.host = os.environ.get("SMTP_HOST", smtp.get("host", "")) or ""
        self.port = int(os.environ.get("SMTP_PORT", smtp.get("port", 587)) or 587)
        self.user = os.environ.get("SMTP_USER", smtp.get("user", "")) or ""
        self.password = os.environ.get("SMTP_PASS", smtp.get("password", "")) or ""
        self.starttls = _as_bool(os.environ.get("SMTP_STARTTLS"), smtp.get("starttls", True))
        self.ssl = _as_bool(os.environ.get("SMTP_SSL"), smtp.get("ssl", False))
        self.timeout = float(smtp.get("timeout_seconds", 15))
        # provider selection
        self.provider = self._resolve_provider(str(email.get("provider", "auto")).lower())

    def _resolve_provider(self, requested: str) -> str:
        if requested in ("resend", "smtp", "console"):
            return requested
        if self.resend_api_key:
            return "resend"
        if self.host:
            return "smtp"
        return "console"


class Mailer:
    """Sends mail via the configured provider; logs instead when unconfigured."""

    def __init__(self, cfg: dict):
        self.settings = MailSettings(cfg)

    def send(self, to: str, subject: str, text: str, html: str | None = None) -> bool:
        """True if handed to a provider, False if only logged. Never raises."""
        s = self.settings
        if s.provider == "resend" and s.resend_api_key:
            return self._send_resend(to, subject, text, html)
        if s.provider == "smtp" and s.host:
            return self._send_smtp(to, subject, text, html)
        logger.info("[mail:console] to=%s subject=%r\n%s", to, subject, text)
        return False

    # -- providers ---------------------------------------------------------

    def _send_resend(self, to: str, subject: str, text: str, html: str | None) -> bool:
        s = self.settings
        payload = {"from": s.sender, "to": [to], "subject": subject, "text": text}
        if html:
            payload["html"] = html
        try:
            status, body = _http_post_json(
                RESEND_ENDPOINT,
                {"Authorization": f"Bearer {s.resend_api_key}"},
                payload, timeout=s.timeout)
        except Exception as exc:  # noqa: BLE001
            logger.error("[mail:resend] request failed to=%s: %s", to, exc)
            return False
        if 200 <= status < 300:
            logger.info("[mail:resend] sent to=%s subject=%r", to, subject)
            return True
        logger.error("[mail:resend] send failed to=%s status=%s body=%s", to, status, body)
        return False

    def _send_smtp(self, to: str, subject: str, text: str, html: str | None) -> bool:
        s = self.settings
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
            logger.info("[mail:smtp] sent to=%s subject=%r via %s:%s", to, subject, s.host, s.port)
            return True
        except Exception as exc:  # noqa: BLE001 — delivery failure must not break signup
            logger.error("[mail:smtp] send failed to=%s via %s:%s: %s", to, s.host, s.port, exc)
            return False

    def _auth_send(self, srv: smtplib.SMTP, msg: EmailMessage) -> None:
        if self.settings.user:
            srv.login(self.settings.user, self.settings.password)
        srv.send_message(msg)
