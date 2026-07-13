"""Rate limiting for /api — pluggable backend, in-memory by default.

Sliding 60s window per client (API token if present, else IP). The limit comes
from ``security.rate_limit_per_minute`` (0 = off). The backend is selected by
``security.rate_limit_backend`` (``memory`` | ``redis``); Redis is only imported
when actually selected, so the default path has no extra dependency. A
multi-worker deployment should use ``redis`` so the window is shared across
processes.
"""

from __future__ import annotations

import time
import uuid
from abc import ABC, abstractmethod
from collections import defaultdict, deque

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

WINDOW_SECONDS = 60.0


class RateLimiterBackend(ABC):
    """Returns True if a request for ``key`` is allowed under ``limit`` in the
    trailing ``window_seconds``, and records the hit."""

    @abstractmethod
    def allow(self, key: str, limit: int, window_seconds: float) -> bool: ...


class InMemoryRateLimiter(RateLimiterBackend):
    """Per-process sliding window. Fine for a single process; a multi-worker
    deployment won't share state — use Redis there."""

    def __init__(self) -> None:
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def allow(self, key: str, limit: int, window_seconds: float) -> bool:
        now = time.monotonic()
        bucket = self._hits[key]
        cutoff = now - window_seconds
        while bucket and bucket[0] <= cutoff:
            bucket.popleft()
        if len(bucket) >= limit:
            return False
        bucket.append(now)
        return True


class RedisRateLimiter(RateLimiterBackend):
    """Shared sliding window via a Redis sorted set (works across workers).
    Redis is imported lazily so this module never hard-depends on it."""

    def __init__(self, url: str = "", client=None):
        self._url = url
        self._client = client

    @property
    def client(self):
        if self._client is None:
            try:
                import redis  # lazy: only when the redis backend is used
            except ImportError as exc:  # pragma: no cover - env-dependent
                raise RuntimeError(
                    "security.rate_limit_backend=redis requires the 'redis' "
                    "package (pip install redis)") from exc
            self._client = redis.Redis.from_url(self._url)
        return self._client

    def allow(self, key: str, limit: int, window_seconds: float) -> bool:
        now = time.time()  # wall clock — shared across processes
        rkey = f"ratelimit:{key}"
        pipe = self.client.pipeline()
        pipe.zremrangebyscore(rkey, 0, now - window_seconds)
        pipe.zadd(rkey, {uuid.uuid4().hex: now})
        pipe.zcard(rkey)
        pipe.expire(rkey, int(window_seconds) + 1)
        count = pipe.execute()[2]
        return count <= limit


def make_rate_limiter(cfg: dict) -> RateLimiterBackend:
    import os
    sec = cfg.get("security", {}) or {}
    backend = str(sec.get("rate_limit_backend", "memory")).lower()
    url = os.environ.get("ASCORE_REDIS_URL") or sec.get("redis_url", "")
    if backend == "redis" and url:
        return RedisRateLimiter(url=url)
    return InMemoryRateLimiter()


class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app):
        super().__init__(app)
        self._backend: RateLimiterBackend | None = None

    def _limit(self, request) -> int:
        cfg = getattr(request.app.state, "cfg", {}) or {}
        return int((cfg.get("security", {}) or {}).get("rate_limit_per_minute", 0))

    def _get_backend(self, request) -> RateLimiterBackend:
        if self._backend is None:
            self._backend = make_rate_limiter(
                getattr(request.app.state, "cfg", {}) or {})
        return self._backend

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
        if not self._get_backend(request).allow(self._key(request), limit,
                                                WINDOW_SECONDS):
            return JSONResponse(
                {"detail": "rate limit exceeded"}, status_code=429,
                headers={"Retry-After": str(int(WINDOW_SECONDS))})
        return await call_next(request)
