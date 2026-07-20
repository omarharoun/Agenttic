"""Anthropic API error taxonomy → honest handling (SPEC-8 discovered gap).

First light 2026-07-20: the API account's credit balance ran out mid-run and the
harness dutifully attempted all 296 remaining calls against a dead API. Errors
were persisted honestly (Hard Rule 5), but a terminal condition must
CIRCUIT-BREAK, not grind. This module is the single source of truth for how
every documented Anthropic error is treated.

The full documented table (platform.claude.com/docs/en/api/errors):

  400 invalid_request_error   malformed request. ALSO used for other 4XXs —
                              including "credit balance is too low" (billing!),
                              so 400 classification must inspect the message.
  401 authentication_error    bad/revoked/expired key
  402 billing_error           billing or payment problem
  403 permission_error        key lacks permission for the resource
  404 not_found_error         wrong endpoint path / resource id (e.g. model)
  409 conflict_error          resource conflict — resolve then retry
  413 request_too_large       payload over the per-endpoint byte cap
  429 rate_limit_error        rate/acceleration limit
  500 api_error               internal — retry with backoff
  504 timeout_error           processing timeout — retry / prefer streaming
  529 overloaded_error        API temporarily overloaded — retry with backoff
  (+ SDK connection/timeout errors; + mid-stream SSE errors after a 200)

Three honest tiers:

  transient      retry with exponential backoff (429/5xx/504/529/409/408 +
                 connection/timeout). Exhausted retries FAIL THE CASE as data —
                 never fabricate, never drop.
  case_terminal  deterministic per-case failure (400 malformed, 413, 422, ...).
                 Retrying cannot help; record the error on THIS case and let the
                 run continue — other cases are unaffected.
  run_terminal   the whole run cannot proceed (401/402/403, 404 bad model or
                 endpoint config, 400-billing). Raise TerminalAPIError: the
                 harness halts remaining cases with a clear reason instead of
                 burning attempts, and everything already done stays persisted.

Detection is structural (status_code + class name + message), matching
retry.py's convention, so it works against both the real SDK and test fakes.
"""

from __future__ import annotations

TRANSIENT = "transient"
CASE_TERMINAL = "case_terminal"
RUN_TERMINAL = "run_terminal"

_RUN_TERMINAL_STATUS = {401, 402, 403, 404}
#: message markers that reveal a 400 that is REALLY a billing condition — the
#: documented "may also be used for other 4XX" escape hatch in action.
_BILLING_MARKERS = ("credit balance", "billing", "purchase credits",
                    "plans & billing", "payment")


class TerminalAPIError(RuntimeError):
    """The upstream API cannot serve ANY further request for this run
    (auth/billing/permission/config). Halt remaining cases; keep what exists."""

    def __init__(self, message: str, *, status: int | None = None,
                 request_id: str | None = None):
        super().__init__(message)
        self.status = status
        self.request_id = request_id


def _status_of(exc: BaseException) -> int | None:
    s = getattr(exc, "status_code", None)
    return s if isinstance(s, int) else None


def _request_id_of(exc: BaseException) -> str | None:
    rid = getattr(exc, "request_id", None)
    if rid:
        return str(rid)
    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        return body.get("request_id")
    return None


def classify(exc: BaseException) -> str:
    """Map any upstream exception to its honest handling tier."""
    from agenttic.retry import is_retryable
    status = _status_of(exc)
    if status in _RUN_TERMINAL_STATUS:
        return RUN_TERMINAL
    if status == 400 and any(m in str(exc).lower() for m in _BILLING_MARKERS):
        return RUN_TERMINAL
    if is_retryable(exc):
        return TRANSIENT
    return CASE_TERMINAL


def as_terminal(exc: BaseException) -> TerminalAPIError:
    """Wrap a run-terminal upstream exception, preserving status + request id."""
    return TerminalAPIError(
        f"{type(exc).__name__}: {exc}",
        status=_status_of(exc), request_id=_request_id_of(exc))
