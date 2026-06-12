"""Step 3 acceptance tests (SPEC.md):
- 10-case suite runs concurrently; all 10 traces persisted
- A timed-out run yields a Trace with an error span, persisted not dropped
Plus: transport-only retries, no retries for agent mistakes, approval gate.
"""

import asyncio
import threading
import time
import uuid
from datetime import datetime, timezone

import pytest

from ascore.harness.runner import (
    HarnessConfig,
    SuiteNotApprovedError,
    run_suite,
)
from ascore.registry.store import InMemoryTraceStore
from ascore.schema.testcase import TestCase, TestSuite
from ascore.schema.trace import SCHEMA_VERSION, Span, Trace


def make_cases(n, suite_id="s-1"):
    return [
        TestCase(test_id=f"tc-{i}", suite_id=suite_id, task_description="t",
                 input={"i": i}, rubric_id="r-1")
        for i in range(n)
    ]


def make_suite(cases, approved=True, suite_id="s-1"):
    return TestSuite(suite_id=suite_id, business_context="ctx",
                     test_ids=[c.test_id for c in cases], approved=approved)


class StubAdapter:
    """Configurable adapter: per-call sleep, scripted exceptions, concurrency probe."""

    agent_id = "stub"
    visibility = "glass_box"

    def __init__(self, sleep=0.0, errors=None):
        self.sleep = sleep
        self.errors = list(errors or [])  # exceptions raised before first success
        self.calls = 0
        self._live = 0
        self.max_concurrent = 0
        self._lock = threading.Lock()

    def describe(self):
        return {"adapter": "stub"}

    def config_hash(self):
        return "stubhash"

    def run(self, test_input, *, test_case_id=None):
        with self._lock:
            self.calls += 1
            self._live += 1
            self.max_concurrent = max(self.max_concurrent, self._live)
        try:
            if self.errors:
                raise self.errors.pop(0)
            time.sleep(self.sleep)
            now = datetime.now(timezone.utc)
            return Trace(
                trace_id=uuid.uuid4().hex, agent_id=self.agent_id,
                agent_config_hash=self.config_hash(), test_case_id=test_case_id,
                spans=[Span(span_id=uuid.uuid4().hex[:12], kind="final_output",
                            name="final_output", start_time=now, end_time=now)],
                visibility="glass_box", final_output="ok",
                schema_version=SCHEMA_VERSION,
            )
        finally:
            with self._lock:
                self._live -= 1


def run(coro):
    return asyncio.run(coro)


class TestConcurrency:
    def test_ten_cases_all_persisted_concurrently(self):
        cases, store = make_cases(10), InMemoryTraceStore()
        adapter = StubAdapter(sleep=0.05)
        traces = run(run_suite(adapter, make_suite(cases), cases, store,
                               HarnessConfig(max_parallel=5, timeout_seconds=5)))
        assert len(traces) == len(store.traces) == 10
        assert {t.test_case_id for t in store.traces} == {c.test_id for c in cases}
        assert adapter.max_concurrent > 1          # actually parallel
        assert adapter.max_concurrent <= 5         # semaphore respected

    def test_results_in_test_case_order(self):
        cases, store = make_cases(6), InMemoryTraceStore()
        traces = run(run_suite(StubAdapter(), make_suite(cases), cases, store))
        assert [t.test_case_id for t in traces] == [c.test_id for c in cases]


class TestTimeout:
    def test_timeout_yields_error_trace_not_drop(self):
        cases, store = make_cases(1), InMemoryTraceStore()
        traces = run(run_suite(StubAdapter(sleep=1.0), make_suite(cases), cases, store,
                               HarnessConfig(timeout_seconds=0.1)))
        assert len(store.traces) == 1
        t = traces[0]
        assert t.final_output == "HARNESS_FAILURE:timeout"
        assert any(s.kind == "error" and "exceeded" in (s.error or "") for s in t.spans)

    def test_timeouts_are_not_retried(self):
        cases, store = make_cases(1), InMemoryTraceStore()
        adapter = StubAdapter(sleep=1.0)
        run(run_suite(adapter, make_suite(cases), cases, store,
                      HarnessConfig(timeout_seconds=0.05, transport_retries=3)))
        # wait_for abandons the thread; only one attempt should have started
        assert adapter.calls == 1


class TestTransportRetries:
    def test_transport_error_retried_then_succeeds(self):
        cases, store = make_cases(1), InMemoryTraceStore()
        adapter = StubAdapter(errors=[ConnectionError("net"), ConnectionError("net")])
        traces = run(run_suite(adapter, make_suite(cases), cases, store,
                               HarnessConfig(transport_retries=2)))
        assert adapter.calls == 3
        assert traces[0].final_output == "ok"

    def test_transport_retries_exhausted_yields_failure_trace(self):
        cases, store = make_cases(1), InMemoryTraceStore()
        adapter = StubAdapter(errors=[ConnectionError("net")] * 5)
        traces = run(run_suite(adapter, make_suite(cases), cases, store,
                               HarnessConfig(transport_retries=1)))
        assert adapter.calls == 2
        assert traces[0].final_output == "HARNESS_FAILURE:transport_failure"
        assert len(store.traces) == 1

    def test_non_transport_exception_not_retried(self):
        cases, store = make_cases(1), InMemoryTraceStore()
        adapter = StubAdapter(errors=[ValueError("adapter bug")])
        traces = run(run_suite(adapter, make_suite(cases), cases, store,
                               HarnessConfig(transport_retries=3)))
        assert adapter.calls == 1  # never retried (Hard Rule 5)
        assert traces[0].final_output == "HARNESS_FAILURE:harness_error"


class TestGuards:
    def test_unapproved_suite_refuses_to_run(self):
        cases, store = make_cases(2), InMemoryTraceStore()
        with pytest.raises(SuiteNotApprovedError):
            run(run_suite(StubAdapter(), make_suite(cases, approved=False), cases, store))
        assert store.traces == []

    def test_foreign_test_case_rejected(self):
        cases, store = make_cases(2), InMemoryTraceStore()
        foreign = TestCase(test_id="x", suite_id="other", task_description="t",
                           input={}, rubric_id="r-1")
        with pytest.raises(ValueError, match="not in suite"):
            run(run_suite(StubAdapter(), make_suite(cases), cases + [foreign], store))
