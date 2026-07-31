"""Step 3 acceptance tests (SPEC.md):
- 10-case suite runs concurrently; all 10 traces persisted
- A timed-out run yields a Trace with an error span, persisted not dropped
Plus: transport-only retries, no retries for agent mistakes, approval gate,
and the adapter concurrency contract (one shared adapter, so per-run state on
``self`` is a race) pinned as an executable fact.
"""

import asyncio
import threading
import time
import uuid
from datetime import datetime, timezone

import pytest

from agenttic.harness.runner import (
    HarnessConfig,
    SuiteNotApprovedError,
    run_suite,
)
from agenttic.registry.store import InMemoryTraceStore
from agenttic.schema.testcase import TestCase, TestSuite
from agenttic.schema.trace import SCHEMA_VERSION, Span, Trace


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


class _StatefulAdapter:
    """An adapter that keeps per-run state on ``self`` — the shape of
    ``GuardedHoneypotAgent._decisions`` and ``BlackBoxHTTPAgent._last_call``.

    The barrier makes the interleaving deterministic instead of lucky: both runs
    write their own case id to ``self._current`` before either is released, so
    both then read back whichever wrote last."""

    agent_id = "stateful"
    visibility = "glass_box"

    def __init__(self, n_concurrent: int):
        self.barrier = threading.Barrier(n_concurrent)
        self.instance_ids: set[int] = set()
        self._current = None

    def describe(self):
        return {"adapter": "stateful"}

    def config_hash(self):
        return "statefulhash"

    def run(self, test_input, *, test_case_id=None):
        self.instance_ids.add(id(self))
        self._current = test_input["i"]     # per-run state parked on self
        self.barrier.wait(timeout=5)        # ...while another run is inside run()
        observed = self._current            # ...which may now be the other case's
        now = datetime.now(timezone.utc)
        return Trace(
            trace_id=uuid.uuid4().hex, agent_id=self.agent_id,
            agent_config_hash=self.config_hash(), test_case_id=test_case_id,
            spans=[Span(span_id=uuid.uuid4().hex[:12], kind="final_output",
                        name="final_output", start_time=now, end_time=now)],
            visibility="glass_box",
            final_output=f"{test_input['i']}:{observed}",
            schema_version=SCHEMA_VERSION,
        )


class TestAdapterConcurrencyContract:
    """``run_suite`` holds ONE adapter object and enters ``run()`` from up to
    ``max_parallel`` threads, so an adapter that stores per-run state on ``self``
    is racy by construction. That is a real hazard for shipped adapters, so pin
    it as a fact rather than leaving it as a comment nobody reads.

    If the harness is ever changed to hand each case its own adapter (a clone),
    this test will fail — that failure is the intended signal to rewrite it
    deliberately, not to delete it."""

    def test_one_adapter_instance_is_shared_across_concurrent_cases(self):
        cases, store = make_cases(2), InMemoryTraceStore()
        adapter = _StatefulAdapter(2)
        run(run_suite(adapter, make_suite(cases), cases, store,
                      HarnessConfig(max_parallel=2, timeout_seconds=5)))
        assert len(adapter.instance_ids) == 1   # no per-case isolation exists

    def test_per_run_state_on_self_leaks_between_concurrent_cases(self):
        cases, store = make_cases(2), InMemoryTraceStore()
        traces = run(run_suite(_StatefulAdapter(2), make_suite(cases), cases,
                               store, HarnessConfig(max_parallel=2,
                                                    timeout_seconds=5)))
        own, observed = zip(*(t.final_output.split(":") for t in traces))
        # both runs read the SAME self state, so exactly one of them read a
        # value belonging to the other case
        assert len(set(observed)) == 1
        assert sum(a != b for a, b in zip(own, observed)) == 1

    def test_serialised_runs_do_not_leak(self):
        # the same adapter is safe at max_parallel=1: the hazard is concurrency,
        # not the sharing per se — worth pinning so the contract isn't overstated
        cases, store = make_cases(2), InMemoryTraceStore()
        traces = run(run_suite(_StatefulAdapter(1), make_suite(cases), cases,
                               store, HarnessConfig(max_parallel=1,
                                                    timeout_seconds=5)))
        own, observed = zip(*(t.final_output.split(":") for t in traces))
        assert own == observed

    def test_shared_rate_limit_clock_still_spaces_concurrent_requests(self):
        """The other half of the contract: some adapter state is MEANT to be
        shared, and must therefore be locked rather than copied.

        ``BlackBoxHTTPAgent._last_call`` is the per-agent-endpoint rate-limit
        clock. Cloning the adapter per case would give every case its own clock
        and multiply the request rate against a customer's endpoint by
        ``max_parallel``; leaving it unlocked did the same thing, because every
        thread read the same stale timestamp, computed the same already-elapsed
        wait and departed together. Assert the floor the limit promises: N
        requests cannot all be away sooner than ``(N-1) * min_interval_s``."""
        from agenttic.adapters.blackbox_http import BlackBoxHTTPAgent

        interval, n = 0.05, 4
        adapter = BlackBoxHTTPAgent(
            agent_id="rate-limited", url="https://example.test/agent",
            min_interval_s=interval, transport=lambda payload: {"output": "ok"})
        cases, store = make_cases(n), InMemoryTraceStore()
        t0 = time.monotonic()
        traces = run(run_suite(adapter, make_suite(cases), cases, store,
                               HarnessConfig(max_parallel=n, timeout_seconds=5)))
        elapsed = time.monotonic() - t0
        assert len(traces) == n
        assert all(t.final_output == "ok" for t in traces)
        assert elapsed >= (n - 1) * interval

    def test_guarded_honeypot_adapter_pairs_decisions_with_its_own_case(
            self, tmp_path):
        """The repo's own adapter under the contract above.

        ``GuardedHoneypotAgent`` has to pair the enforcement decisions it took
        with the tool-call spans the superclass built. While that hand-off went
        through a list on ``self``, concurrent cases cleared and appended to the
        same list and each then stamped the FIRST decision onto its own span:
        every honeypot call in the suite came back carrying one identical
        ``decision_ref``, so blocked-vs-executed — the one distinction this
        module exists to draw — was attributed by luck. One decision per call,
        each paired with its own case."""
        from agenttic.redteam import (
            HoneypotAuthor,
            build_guarded_demo_target,
            install_honeypot_enforcement,
            plant_honeypots,
            reference_descriptor,
        )
        from agenttic.redteam.honeypot import enforcement_records
        from agenttic.redteam.probe import build_test_case
        from agenttic.registry.sqlite_store import Registry

        planted = plant_honeypots(reference_descriptor())
        names = planted.honeypot_tool_names()
        gw, sess = install_honeypot_enforcement(
            Registry(str(tmp_path / "hp.db")), planted.agent_id, names,
            enforcing=True)
        adapter = build_guarded_demo_target(planted, gw, sess.session_id,
                                           kb_path=str(tmp_path / "kb.json"))
        # the "direct" lure names the decoy in raw ASCII and the scripted agent's
        # own guard catches it — those probes never reach a decoy, so drop them
        specs = [s for s in HoneypotAuthor().author(planted)
                 if s.technique != "direct"]
        cases = [build_test_case(s, "s-1", i) for i, s in enumerate(specs)]
        traces = run(run_suite(adapter, make_suite(cases), cases,
                               InMemoryTraceStore(),
                               HarnessConfig(max_parallel=5, timeout_seconds=20)))
        records = [r for t in traces for r in enforcement_records(t, names)]
        refs = [r["decision_ref"] for r in records]
        assert refs                                  # the decoys were reached
        assert len(set(refs)) == len(refs)           # never the same decision twice
        assert all(r["enforcement"] == "blocked" for r in records)


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
