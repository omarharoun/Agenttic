"""A small in-process rate limiter for the /api surface.

Sliding 60-second window per client (keyed by API token if present, else
client IP). Limit comes from ``security.rate_limit_per_minute`` in config
(0 = disabled). In-memory and therefore per-process — fine for the current
single-process deployment; a multi-worker deployment needs a shared store
(Redis), noted in PRODUCTION_READINESS.md.
"""

from __future__ import annotations

import time
from collections import defaultdict, deque

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

WINDOW_SECONDS = 60.0


class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app):
        super().__init__(app)
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def _limit(self, request) -> int:
        cfg = getattr(request.app.state, "cfg", {}) or {}
        return int((cfg.get("security", {}) or {}).get("rate_limit_per_minute", 0))

    @staticmethod
    def _key(request) -> str:
        auth = request.headers.get("authorization", "")
        if auth.lower().startswith("bearer "):
            return f"tok:{auth[7:].strip()}"
        api_key = request.headers.get("x-api-key")
        if api_key:
            return f"tok:{api_key.strip()}"
        client = request.client
        return f"ip:{client.host if client else 'unknown'}"

    async def dispatch(self, request, call_next):
        limit = self._limit(request)
        if limit <= 0 or not request.url.path.startswith("/api"):
            return await call_next(request)
        now = time.monotonic()
        bucket = self._hits[self._key(request)]
        cutoff = now - WINDOW_SECONDS
        while bucket and bucket[0] <= cutoff:
            bucket.popleft()
        if len(bucket) >= limit:
            return JSONResponse(
                {"detail": "rate limit exceeded"}, status_code=429,
                headers={"Retry-After": str(int(WINDOW_SECONDS))})
        bucket.append(now)
        return await call_next(request)
