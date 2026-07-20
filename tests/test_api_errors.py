"""SPEC-8 discovered gap — honest handling for every documented Anthropic error.

Taxonomy per platform.claude.com/docs/en/api/errors:
  transient      429, 500, 502, 503, 504, 529, 408, 409 + connection/timeout
  case_terminal  400 (malformed), 405, 413, 422
  run_terminal   401, 402, 403, 404, and 400 whose message is a billing
                 condition ("credit balance is too low" — the 4XX escape hatch)

Circuit-breakers: harness halts remaining cases on a run-terminal error
(everything persisted, kind=run_halted); scoring stops calling the API and
marks remaining runs "scoring halted".
"""

from __future__ import annotations

import asyncio
import uuid

import pytest

from agenttic.api_errors import (
    CASE_TERMINAL, RUN_TERMINAL, TRANSIENT, TerminalAPIError, classify,
)
from agenttic.retry import RetryPolicy, with_retry


def _exc(status: int | None, message: str = "boom", name: str = "ApiError"):
    e = type(name, (Exception,), {})(message)
    if status is not None:
        e.status_code = status
    return e


# --------------------------------------------------------------------------- #
# 1. the classifier truth table — every documented status code
# --------------------------------------------------------------------------- #
CREDIT_MSG = ("Your credit balance is too low to access the Anthropic API. "
              "Please go to Plans & Billing to upgrade or purchase credits.")

TABLE = [
    (400, "prefilling not supported", CASE_TERMINAL),   # invalid_request_error
    (400, CREDIT_MSG, RUN_TERMINAL),                     # the billing 400!
    (401, "invalid x-api-key", RUN_TERMINAL),            # authentication_error
    (402, "billing issue", RUN_TERMINAL),                # billing_error
    (403, "no permission", RUN_TERMINAL),                # permission_error
    (404, "model not found", RUN_TERMINAL),              # not_found_error
    (409, "conflict", TRANSIENT),                        # conflict: resolve+retry
    (413, "request too large", CASE_TERMINAL),           # request_too_large
    (422, "unprocessable", CASE_TERMINAL),
    (429, "rate limited", TRANSIENT),                    # rate_limit_error
    (500, "internal", TRANSIENT),                        # api_error
    (504, "timed out", TRANSIENT),                       # timeout_error
    (529, "overloaded", TRANSIENT),                      # overloaded_error
]


@pytest.mark.parametrize("status,msg,want", TABLE)
def test_classifier_truth_table(status, msg, want):
    assert classify(_exc(status, msg)) == want


def test_connection_and_timeout_errors_are_transient():
    assert classify(ConnectionError("reset")) == TRANSIENT
    assert classify(TimeoutError("slow")) == TRANSIENT
    assert classify(_exc(None, "t", name="APIConnectionError")) == TRANSIENT


# --------------------------------------------------------------------------- #
# 2. with_retry behavior per tier
# --------------------------------------------------------------------------- #
def test_run_terminal_raises_typed_error_immediately_no_retries():
    calls = {"n": 0}

    def fn():
        calls["n"] += 1
        raise _exc(400, CREDIT_MSG, name="BadRequestError")

    with pytest.raises(TerminalAPIError) as ei:
        with_retry(fn, RetryPolicy(max_attempts=5), sleep=lambda s: None)
    assert calls["n"] == 1                    # no retry against a dead API
    assert ei.value.status == 400
    assert "credit balance" in str(ei.value)


def test_case_terminal_reraises_original_no_retries():
    calls = {"n": 0}

    def fn():
        calls["n"] += 1
        raise _exc(413, "too large", name="RequestTooLargeError")

    with pytest.raises(Exception, match="too large"):
        with_retry(fn, RetryPolicy(max_attempts=5), sleep=lambda s: None)
    assert calls["n"] == 1


def test_transient_retries_then_succeeds():
    calls = {"n": 0}

    def fn():
        calls["n"] += 1
        if calls["n"] < 3:
            raise _exc(529, "overloaded", name="OverloadedError")
        return "ok"

    assert with_retry(fn, RetryPolicy(max_attempts=5), sleep=lambda s: None) == "ok"
    assert calls["n"] == 3


# --------------------------------------------------------------------------- #
# 3. the harness circuit-breaker
# --------------------------------------------------------------------------- #
from agenttic.adapters.base import AgentAdapter
from agenttic.harness.runner import HarnessConfig, run_suite
from agenttic.schema.testcase import TestCase, TestSuite
from agenttic.schema.trace import SCHEMA_VERSION, Trace


class DyingAgent(AgentAdapter):
    """First call trips a terminal error; counts every attempted call."""
    visibility = "black_box"
    agent_id = "dying"

    def __init__(self):
        self.calls = 0

    def describe(self):
        return {}

    def run(self, ti, *, test_case_id=None):
        self.calls += 1
        raise TerminalAPIError("BadRequestError: " + CREDIT_MSG,
                               status=400, request_id="req_test")


class _Store:
    def __init__(self):
        self.saved = []

    def save_trace(self, t, mode="batch"):
        self.saved.append(t)


def test_harness_halts_run_on_terminal_error_and_persists_everything():
    cases = [TestCase(test_id=f"c{i}", suite_id="s", task_description="t",
                      rubric_id="r") for i in range(6)]
    suite = TestSuite(suite_id="s", version=1, business_context="b",
                      test_ids=[c.test_id for c in cases], approved=True)
    agent = DyingAgent()
    store = _Store()
    events = []
    traces = asyncio.run(run_suite(
        agent, suite, cases, store,
        HarnessConfig(timeout_seconds=5, max_parallel=1, transport_retries=0),
        on_event=lambda t, d: events.append(t)))

    assert len(traces) == 6                          # nothing dropped (Rule 5)
    kinds = [t.final_output.split(":")[0] for t in traces]
    assert kinds.count("HARNESS_FAILURE") == 6
    names = [s.name for t in traces for s in t.spans if s.kind == "error"]
    assert names.count("terminal_api_error") == 1    # the case that hit it
    assert names.count("run_halted") == 5            # the rest short-circuited
    assert agent.calls == 1                          # ONE call against the dead API
    assert "run_halted" in events
    halted = next(t for t in traces
                  for s in t.spans if s.name == "run_halted")
    assert "credit balance" in str(
        next(s.error for s in halted.spans if s.kind == "error"))


# --------------------------------------------------------------------------- #
# 4. the scoring circuit-breaker
# --------------------------------------------------------------------------- #
def test_scoring_halts_on_terminal_judge_error(monkeypatch, tmp_path):
    from agenttic import ops
    from agenttic.registry.sqlite_store import Registry
    from agenttic.schema.rubric import Criterion, Rubric

    reg = Registry(tmp_path / "t.db")
    reg.save_rubric(Rubric(rubric_id="rb", version=1, weights={"j": 1.0}, criteria=[
        Criterion(criterion_id="j", description="d", scorer="judge",
                  scale="binary", anchors={"pass": "p", "fail": "f"})]))
    cases = [TestCase(test_id=f"c{i}", suite_id="s", task_description="t",
                      rubric_id="rb") for i in range(4)]

    def _trace(tid):
        return Trace(trace_id=uuid.uuid4().hex, agent_id="a", agent_config_hash="h",
                     test_case_id=tid, spans=[], visibility="black_box",
                     final_output="out", schema_version=SCHEMA_VERSION)

    traces = [_trace(c.test_id) for c in cases]

    scored = {"n": 0}

    def dying_score_run(*a, **k):
        scored["n"] += 1
        raise TerminalAPIError("credit balance is too low", status=400)

    monkeypatch.setattr(ops, "score_run", dying_score_run)
    monkeypatch.setattr(ops, "make_judge", lambda *a, **k: None)
    runs = asyncio.run(ops.score_op({"scoring": {}}, reg, traces, cases, "model"))

    assert len(runs) == 4                            # every run recorded
    assert scored["n"] == 1                          # ONE attempt, then halt
    assert "terminal upstream error" in runs[0].scoring_error
    assert all("scoring halted" in r.scoring_error for r in runs[1:])
