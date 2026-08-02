"""The standard run's verification leg must honour the CONFIGURED closure target.

`verify_run` took no config and called `verify_op(traces)` with none, so the
coverage model fell back to its built-in 0.95 — silently. `config.yaml` also
says 0.95, which is why nothing ever looked wrong: the defect is invisible until
someone sets a different target, and then their runs are measured against a
number they did not choose and are never told about.

Found on 2026-08-02 by a diagnostic the coverage layer prints for exactly this
case, during the first real run of the OpenHands adapter:

    coverage closure target: no config supplied (baseline.py:99 <- ops.py:476);
    trace closure measured against the built-in default 0.95 rather than
    coverage.closure_target
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from agenttic.metrics.runner import verify_run
from agenttic.schema.trace import Span, Trace

NOW = datetime(2026, 8, 2, tzinfo=timezone.utc)


def _trace(case: str) -> Trace:
    return Trace(
        trace_id=uuid.uuid4().hex, agent_id="a", agent_config_hash="h",
        test_case_id=case, visibility="glass_box", final_output="done",
        spans=[Span(span_id=uuid.uuid4().hex[:12], kind="tool_call",
                    name="lookup_kb", start_time=NOW, end_time=NOW,
                    input={"q": "x"}, output={"a": "y"})])


def test_the_configured_closure_target_is_the_one_used():
    """A tenant that sets 0.80 must be measured against 0.80."""
    v = verify_run([_trace("c1")], cfg={"coverage": {"closure_target": 0.80}})
    assert v["status"] == "populated"
    assert v["closure_target"] == 0.80, (
        "the configured target was ignored and the built-in default used "
        "instead — a run measured against a number nobody chose")


def test_no_config_still_works_and_uses_the_documented_default():
    """Callers that genuinely have no config keep working, unchanged."""
    v = verify_run([_trace("c1")])
    assert v["status"] == "populated"
    assert v["closure_target"] == 0.95


def test_an_empty_run_is_still_not_run_rather_than_a_pass():
    assert verify_run([], cfg={"coverage": {"closure_target": 0.80}})["status"] \
        == "not_run"
