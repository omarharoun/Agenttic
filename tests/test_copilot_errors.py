"""Copilot upstream-error handling: the model call is classified into an honest,
per-case, secret-free message + a stable code the UI renders as a styled card,
and the REAL underlying error is logged server-side (never the generic 200 the
old bare-except produced).

Covered:
* :func:`agenttic.copilot.errors.classify` maps each upstream failure to the right
  case, and the user-facing message never leaks internals (no "Anthropic", no
  "credit balance") for the out-of-Agenttic-credits case;
* the agentic endpoint, when the model call raises, streams a structured SSE
  ``error`` event with the classified code + message, and logs the real cause.
"""

from __future__ import annotations

import json
import logging
from types import SimpleNamespace as NS

from fastapi.testclient import TestClient

from agenttic.copilot import credits
from agenttic.copilot.errors import (
    GENERIC, OUT_OF_CREDITS, RATE_LIMITED, UNAVAILABLE, classify,
)
from agenttic.registry.sqlite_store import Registry
from agenttic.server.app import create_app
from agenttic.server.routes import copilot as copilot_route


# --------------------------------------------------------------------------- #
# A fake Anthropic-style API error (duck-typed exactly as the SDK exposes).
# --------------------------------------------------------------------------- #


def FakeAPIError(name, status_code, etype, message, request_id="req_abc123"):
    """Build an exception whose class NAME is ``name`` (so ``type(e).__name__``
    matches the SDK's, e.g. ``RateLimitError``) carrying the duck-typed
    ``status_code`` / ``body`` / ``request_id`` the SDK exposes."""
    exc = type(name, (Exception,), {})(message)
    exc.status_code = status_code
    exc.body = {"error": {"type": etype, "message": message}}
    exc.request_id = request_id
    return exc


# --------------------------------------------------------------------------- #
# 1. Classification is correct and honest (pure unit).
# --------------------------------------------------------------------------- #


class TestClassify:
    def test_rate_limited(self):
        err, diag = classify(FakeAPIError(
            "RateLimitError", 429, "rate_limit_error", "slow down"))
        assert err.code == RATE_LIMITED and err.action == "retry"
        assert diag["status"] == 429 and diag["request_id"] == "req_abc123"
        assert diag["case"] == RATE_LIMITED

    def test_out_of_anthropic_credits_is_unavailable_and_not_leaked(self):
        # Anthropic 400 "credit balance is too low" => our OWN billing is out.
        err, diag = classify(FakeAPIError(
            "BadRequestError", 400, "invalid_request_error",
            "Your credit balance is too low to access the Anthropic API"))
        assert err.code == UNAVAILABLE
        # honest + graceful: never expose the real operational cause
        low = err.message.lower()
        assert "anthropic" not in low
        assert "credit balance" not in low
        assert "unavailable" in low
        assert diag["status"] == 400

    def test_auth_error_is_unavailable(self):
        err, _ = classify(FakeAPIError(
            "AuthenticationError", 401, "authentication_error", "bad key"))
        assert err.code == UNAVAILABLE
        assert "key" not in err.message.lower()  # no config detail leaked

    def test_user_credits_402_is_out_of_credits_with_upgrade(self):
        err, _ = classify(FakeAPIError("APIStatusError", 402, "x", "no credits"))
        assert err.code == OUT_OF_CREDITS and err.action == "upgrade"

    def test_unknown_is_generic(self):
        err, diag = classify(ValueError("boom"))
        assert err.code == GENERIC
        assert diag["status"] is None and diag["exc_type"] == "ValueError"


# --------------------------------------------------------------------------- #
# 2. The endpoint surfaces the classified error over SSE and logs the real one.
# --------------------------------------------------------------------------- #


CONFIG_YAML = """\
models: {agent_default: claude-sonnet-4-6, judge_executor: j, judge_strong: js, judge_light: jl, generator: g}
harness: {timeout_seconds: 10, max_parallel: 5, transport_retries: 1, max_steps: 8}
scoring: {calibration_threshold: 0.8}
paths: {registry_db: %(db)s, review_dir: %(r)s, calibration_dir: %(c)s}
auth: {token: "adm", required: true, allow_signup: false, session_secret: testsecret}
copilot: {model: claude-sonnet-4-6, rate_limit_per_minute: 50, daily_message_cap_per_user: 1000, daily_message_cap_global: 1000}
certification: {profiles: {cert-agent-safety-v1: {required_domains: [harm_refusal], thresholds: {}}}}
"""


class RaisingClient:
    """A client whose model stream immediately raises the given error."""

    def __init__(self, exc):
        self._exc = exc
        self.messages = NS(stream=self._stream)

    def _stream(self, **kwargs):
        raise self._exc


def _mk_app(tmp_path, exc):
    cfg = tmp_path / "config.yaml"
    cfg.write_text(CONFIG_YAML % {"db": tmp_path / "c.db", "r": tmp_path / "r",
                                  "c": tmp_path / "cal"})
    reg = Registry(tmp_path / "c.db")
    app = create_app(str(cfg), registry=reg, clients={"copilot": RaisingClient(exc)})
    copilot_route._RL._hits.clear()
    credits.reset_daily_cap()
    return app


def _events(resp):
    out, ev = [], None
    for line in resp.text.splitlines():
        if line.startswith("event: "):
            ev = line[7:]
        elif line.startswith("data: "):
            out.append((ev, line[6:].replace("\\n", "\n").replace("\\\\", "\\")))
    return out


def _capture(logger_name):
    recs: list[logging.LogRecord] = []

    class _H(logging.Handler):
        def emit(self, r): recs.append(r)

    h = _H()
    logging.getLogger(logger_name).addHandler(h)
    return recs, h


class TestEndpointErrorSurface:
    def test_rate_limit_error_streams_classified_card_and_logs_real_error(
            self, tmp_path):
        exc = FakeAPIError("RateLimitError", 429, "rate_limit_error", "slow down")
        app = _mk_app(tmp_path, exc)
        recs, h = _capture("agenttic.copilot.agent")
        try:
            with TestClient(app) as c:
                r = c.post("/api/copilot/chat", headers={"Authorization": "Bearer adm"},
                           json={"message": "hi"})
        finally:
            logging.getLogger("agenttic.copilot.agent").removeHandler(h)
        assert r.status_code == 200
        errs = [json.loads(d) for e, d in _events(r) if e == "error"]
        assert errs and errs[0]["code"] == "rate_limited"
        assert errs[0]["action"] == "retry"
        assert "too fast" in errs[0]["message"].lower()
        # the REAL error was logged with the diagnostic fields (not a generic 200)
        got = [rec for rec in recs if rec.getMessage() == "copilot_upstream_error"]
        assert got, "expected the underlying error to be logged"
        diag = getattr(got[0], "extra_fields", {})
        assert diag.get("case") == "rate_limited"
        assert diag.get("status") == 429
        assert diag.get("request_id") == "req_abc123"

    def test_billing_error_is_graceful_and_does_not_leak(self, tmp_path):
        exc = FakeAPIError("BadRequestError", 400, "invalid_request_error",
                           "Your credit balance is too low to access the Anthropic API")
        app = _mk_app(tmp_path, exc)
        with TestClient(app) as c:
            r = c.post("/api/copilot/chat", headers={"Authorization": "Bearer adm"},
                       json={"message": "hi"})
        errs = [json.loads(d) for e, d in _events(r) if e == "error"]
        assert errs and errs[0]["code"] == "unavailable"
        low = errs[0]["message"].lower()
        assert "anthropic" not in low and "credit balance" not in low
