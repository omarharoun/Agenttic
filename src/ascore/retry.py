"""Retry with exponential backoff + jitter for transient upstream (Anthropic)
errors.

Retryable = transient server/transport conditions: HTTP 408/409/429/500/502/503/
529, Anthropic ``APITimeoutError`` / ``APIConnectionError`` / ``InternalServerError``
/ ``RateLimitError`` / overloaded, and plain ``ConnectionError`` / ``TimeoutError``.
NOT retryable = client errors (400/401/403/404/422) — those re-raise immediately.

Detection is structural (status_code + class name) so it works against both the
real SDK and the fakes used in tests, without importing ``anthropic`` here.
Policy is config-driven (``anthropic.retry`` in config.yaml).
"""

from __future__ import annotations

import logging
import random
import time
from dataclasses import dataclass

logger = logging.getLogger("ascore.retry")

_RETRYABLE_STATUS = {408, 409, 429, 500, 502, 503, 529}
_RETRYABLE_NAMES = {
    "APITimeoutError", "APIConnectionError", "APIConnectionTimeoutError",
    "InternalServerError", "RateLimitError", "OverloadedError",
    "ServiceUnavailableError",
}
_NONRETRYABLE_STATUS = {400, 401, 403, 404, 405, 422}


def is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, (ConnectionError, TimeoutError)):
        return True
    status = getattr(exc, "status_code", None)
    if isinstance(status, int):
        if status in _NONRETRYABLE_STATUS:
            return False
        if status in _RETRYABLE_STATUS or status >= 500:
            return True
    return type(exc).__name__ in _RETRYABLE_NAMES


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 5        # total tries (1 initial + 4 retries)
    base_delay: float = 0.5      # seconds; doubles each retry
    max_delay: float = 30.0
    jitter: bool = True

    @classmethod
    def from_cfg(cls, cfg: dict) -> "RetryPolicy":
        r = (cfg.get("anthropic", {}) or {}).get("retry", {}) or {}
        return cls(
            max_attempts=int(r.get("max_attempts", 5)),
            base_delay=float(r.get("base_delay", 0.5)),
            max_delay=float(r.get("max_delay", 30.0)),
            jitter=bool(r.get("jitter", True)),
        )


def _delay(policy: RetryPolicy, attempt: int) -> float:
    d = min(policy.max_delay, policy.base_delay * (2 ** attempt))
    if policy.jitter:
        d = random.uniform(0, d)  # full jitter
    return d


def with_retry(fn, policy: RetryPolicy | None = None, *, op: str = "anthropic",
               sleep=time.sleep):
    """Call ``fn()`` with retry on transient errors. Re-raises the last error
    when attempts are exhausted or the error is non-retryable. ``sleep`` is
    injectable so tests don't actually wait."""
    policy = policy or RetryPolicy()
    last: BaseException | None = None
    for attempt in range(policy.max_attempts):
        try:
            return fn()
        except BaseException as exc:  # noqa: BLE001 — classify, then re-raise
            last = exc
            if not is_retryable(exc) or attempt + 1 >= policy.max_attempts:
                if is_retryable(exc):
                    logger.warning("op=%s exhausted %d attempts: %s",
                                   op, policy.max_attempts, type(exc).__name__)
                raise
            wait = _delay(policy, attempt)
            logger.warning("op=%s transient error %s (attempt %d/%d); retrying in %.2fs",
                           op, type(exc).__name__, attempt + 1, policy.max_attempts, wait)
            sleep(wait)
    raise last  # pragma: no cover
