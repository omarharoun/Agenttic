"""Structured logging + request context + HTTP metrics.

* JSON logs on the ``ascore`` logger (one line per request: method, path,
  status, duration, request_id, tenant, role), configured once via
  ``configure_logging``.
* Each request gets a request id (honoring an inbound ``X-Request-ID``), echoed
  on the response and attached to ``request.state.request_id`` and the log line.
* Per-request HTTP metrics feed ``server.metrics``.
"""

from __future__ import annotations

import json
import logging
import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware

from ascore.server import metrics
from ascore.server.tracing import span

logger = logging.getLogger("ascore.request")


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {"level": record.levelname, "logger": record.name,
                   "msg": record.getMessage()}
        for k, v in getattr(record, "extra_fields", {}).items():
            payload[k] = v
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload)


def configure_logging(cfg: dict) -> None:
    from ascore.secrets import (
        SecretRedactor, hydrate_env_secrets, known_secret_values)

    hydrate_env_secrets()  # pull *_FILE secrets into the environment
    obs = (cfg.get("observability", {}) or {})
    level = str(obs.get("log_level", "INFO")).upper()
    root = logging.getLogger("ascore")
    root.setLevel(level)
    redactor = SecretRedactor(known_secret_values(cfg))
    if any(getattr(h, "_ascore", False) for h in root.handlers):
        # idempotent across create_app calls / tests — refresh the redactor's
        # secret set in case config changed, but don't stack handlers
        for h in root.handlers:
            for f in h.filters:
                if isinstance(f, SecretRedactor):
                    f.secrets = redactor.secrets
        return
    handler = logging.StreamHandler()
    handler._ascore = True  # type: ignore[attr-defined]
    handler.setFormatter(_JsonFormatter() if obs.get("log_json", True)
                         else logging.Formatter("%(name)s %(levelname)s %(message)s"))
    handler.addFilter(redactor)
    root.addHandler(handler)
    root.propagate = False


def _log(msg: str, **fields) -> None:
    logger.info(msg, extra={"extra_fields": fields})


class ObservabilityMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        rid = request.headers.get("x-request-id") or uuid.uuid4().hex[:16]
        request.state.request_id = rid
        start = time.monotonic()
        status = 500
        response = None
        try:
            with span("http.request", **{"http.method": request.method,
                                         "http.target": request.url.path,
                                         "request_id": rid}):
                response = await call_next(request)
            status = response.status_code
            response.headers["X-Request-ID"] = rid
            return response
        finally:
            dur = time.monotonic() - start
            if request.url.path.startswith("/api"):
                metrics.record_http(request.method, status, dur)
            _log("request", method=request.method, path=request.url.path,
                 status=status, duration_ms=round(dur * 1000, 2), request_id=rid,
                 tenant=getattr(request.state, "tenant", None),
                 role=getattr(request.state, "role", None))
