"""Basic load/concurrency check for the async harness: many cases run
concurrently, bounded by max_parallel, with every trace persisted."""

import asyncio
import threading
import time
import uuid
from datetime import datetime, timezone

from agenttic.adapters.base import AgentAdapter
from agenttic.harness.runner import HarnessConfig, run_suite
from agenttic.registry.sqlite_store import Registry
from agenttic.schema.testcase import TestCase, TestSuite
from agenttic.schema.trace import SCHEMA_VERSION, Span, Trace


class _ConcurrencyProbe(AgentAdapter):
    """Records the peak number of concurrently-running adapter calls."""
    visibility = "glass_box"

    def __init__(self):
        self.agent_id = "probe"
        self._lock = threading.Lock()
        self.current = 0
        self.peak = 0

    def describe(self):
        return {"adapter": "probe"}

    def run(self, test_input, *, test_case_id=None):
        with self._lock:
            self.current += 1
            self.peak = max(self.peak, self.current)
        time.sleep(0.02)  # hold the slot so concurrency is observable
        with self._lock:
            self.current -= 1
        now = datetime.now(timezone.utc)
        return Trace(trace_id=uuid.uuid4().hex, agent_id=self.agent_id,
                     agent_config_hash=self.config_hash(), test_case_id=test_case_id,
                     spans=[Span(span_id="s", kind="final_output", name="f",
                                 start_time=now, end_time=now)],
                     visibility="glass_box", final_output="ok",
                     total_cost_usd=0.0, total_latency_ms=20.0, total_steps=1,
                     schema_version=SCHEMA_VERSION)


def test_harness_concurrency_bounded_and_complete(tmp_path):
    reg = Registry(tmp_path / "load.db")
    n = 50
    suite = TestSuite(suite_id="s", business_context="x", approved=True,
                      test_ids=[f"c{i}" for i in range(n)])
    cases = [TestCase(test_id=f"c{i}", suite_id="s", task_description="t",
                      input={}, rubric_id="r") for i in range(n)]
    probe = _ConcurrencyProbe()
    traces = asyncio.run(run_suite(
        probe, suite, cases, reg,
        HarnessConfig(max_parallel=5, timeout_seconds=10)))

    assert len(traces) == n                       # every case produced a trace
    assert all(t.final_output == "ok" for t in traces)
    assert probe.peak <= 5                         # semaphore bounded concurrency
    assert probe.peak >= 2                         # but it DID run concurrently
    assert len(reg.traces("probe")) == n           # all persisted
