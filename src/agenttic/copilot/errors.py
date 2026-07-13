"""Copilot upstream-error classification — turn an opaque model/API failure into
an HONEST, user-friendly message with a stable machine ``code`` the frontend can
render as a styled error card, WITHOUT ever leaking internals to the end user.

The bare ``except`` that used to swallow the real exception (and log only a 200)
is replaced by :func:`classify`, which maps an Anthropic/API error to one of a
small set of cases and returns, separately:

* a :class:`CopilotError` — the code + user-facing message + suggested action
  (retry / upgrade / none) that is safe to show and never contains a secret,
  key, prompt, or raw stack trace; and
* a ``diag`` dict — ``status`` / ``error_type`` / ``request_id`` / ``exc_type`` /
  ``case`` — for the SERVER-SIDE log, so an incident is diagnosable from
  ``docker compose logs`` alone.

Deliberately honest mapping:

* **out-of-credits / billing on Agenttic's OWN key** (Anthropic 400 "credit
  balance is too low") → ``unavailable``. We do NOT tell the end user we're out
  of Anthropic credits — that's an operational detail. They see a graceful
  "temporarily unavailable".
* **rate-limited** (Anthropic 429 / overloaded) → ``rate_limited``.
* **auth / permission** on our key → ``unavailable`` (a config/ops problem, never
  the user's fault, never leaked).
* **the tenant's OWN credits gate** (our 402) → ``out_of_credits`` with an
  upgrade affordance.
* everything else → ``generic``.

Classification is duck-typed on ``status_code`` / ``body`` / ``request_id`` so it
works against the real ``anthropic`` SDK exceptions AND the scripted fakes tests
use, without importing or calling the SDK.
"""

from __future__ import annotations

from dataclasses import dataclass

# -- case codes (stable; the frontend maps them to a card) ------------------ #
UNAVAILABLE = "unavailable"
RATE_LIMITED = "rate_limited"
OUT_OF_CREDITS = "out_of_credits"
DAILY_LIMIT = "daily_limit"
NOT_CONFIGURED = "not_configured"
GENERIC = "generic"

# User-facing copy. Honest, calm, never exposes internals/secrets. The frontend
# has its own copy keyed by ``code``; this is the server-authoritative fallback.
_MESSAGES = {
    UNAVAILABLE: "The assistant is temporarily unavailable. Please try again in "
                 "a little while.",
    RATE_LIMITED: "You're sending messages too fast — give it a moment and try "
                  "again.",
    OUT_OF_CREDITS: "You're out of credits. Upgrade your plan or add credits to "
                    "keep using the Copilot.",
    DAILY_LIMIT: "You've reached today's Copilot limit — please try again "
                 "tomorrow.",
    NOT_CONFIGURED: "The Copilot isn't configured on this server yet.",
    GENERIC: "The Copilot ran into a problem. Please try again in a moment.",
}
# Suggested affordance the UI renders: retry the turn / go upgrade / nothing.
_ACTIONS = {
    UNAVAILABLE: "retry",
    RATE_LIMITED: "retry",
    OUT_OF_CREDITS: "upgrade",
    DAILY_LIMIT: "none",
    NOT_CONFIGURED: "none",
    GENERIC: "retry",
}


@dataclass(frozen=True)
class CopilotError:
    """A safe-to-surface error: a stable ``code``, a user-facing ``message``, and
    a suggested ``action`` (``retry`` | ``upgrade`` | ``none``)."""
    code: str
    message: str
    action: str = "retry"

    def payload(self) -> dict:
        """The JSON body sent over SSE (``error`` event) or as an HTTP 4xx
        ``detail`` — identical shape on both channels so the UI has one path."""
        return {"code": self.code, "message": self.message, "action": self.action}


def make(code: str) -> CopilotError:
    """Build a :class:`CopilotError` for a known ``code`` (falls back to generic).
    Optional ``message`` override lets a caller pass through an honest,
    already-safe reason (e.g. the daily-cap text)."""
    return CopilotError(code, _MESSAGES.get(code, _MESSAGES[GENERIC]),
                        _ACTIONS.get(code, "retry"))


def with_message(code: str, message: str | None) -> CopilotError:
    """Like :func:`make` but use ``message`` when provided (it must already be
    safe/honest — used for the rate-limit and daily-cap reasons we author)."""
    base = make(code)
    return CopilotError(base.code, message or base.message, base.action)


def _status(exc: object) -> int | None:
    s = getattr(exc, "status_code", None)
    try:
        return int(s) if s is not None else None
    except (TypeError, ValueError):
        return None


def _body_error(exc: object) -> dict:
    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        err = body.get("error")
        if isinstance(err, dict):
            return err
    return {}


def _error_type(exc: object) -> str | None:
    t = _body_error(exc).get("type")
    return t if isinstance(t, str) else None


def _message_text(exc: object) -> str:
    """Best-effort upstream message, used ONLY for substring classification —
    never surfaced to the user."""
    msg = _body_error(exc).get("message")
    if isinstance(msg, str) and msg:
        return msg
    return str(exc or "")


def classify(exc: BaseException) -> tuple[CopilotError, dict]:
    """Map an upstream exception to ``(CopilotError, diag)``.

    ``diag`` carries the fields worth logging server-side (status, error.type,
    request_id, exception class, and the resolved case). Nothing in ``diag`` is
    shown to the user; the log filter still scrubs any known secret value."""
    status = _status(exc)
    name = type(exc).__name__
    lname = name.lower()
    text = _message_text(exc).lower()

    if status == 429 or "ratelimit" in lname or "overloaded" in lname:
        code = RATE_LIMITED
    elif status == 400 and "credit balance" in text:
        # Agenttic's OWN Anthropic billing is exhausted — treat as unavailable,
        # never expose "we're out of Anthropic credits" to the end user.
        code = UNAVAILABLE
    elif status in (401, 403) or name in ("AuthenticationError",
                                          "PermissionDeniedError"):
        code = UNAVAILABLE
    elif status == 402:
        code = OUT_OF_CREDITS
    else:
        code = GENERIC

    diag = {
        "case": code,
        "status": status,
        "error_type": _error_type(exc),
        "request_id": getattr(exc, "request_id", None),
        "exc_type": name,
    }
    return make(code), diag
