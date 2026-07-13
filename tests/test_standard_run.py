"""Standard run path: pass^k from real repeated runs + ECE/abstention calibration
feed the full Agenttic Index."""

import asyncio
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

from agenttic import ops
from agenttic.metrics.runner import run_standard
from agenttic.schema.scorecard import CriterionScore, RunScore
from agenttic.schema.trace import SCHEMA_VERSION, Span, Trace

NOW = datetime(2026, 6, 21, tzinfo=timezone.utc)
ADAPTER = SimpleNamespace(agent_id="agent-x", model="m", visibility="glass_box")


def _trace(tid, confidence=None):
    attrs = {"confidence": confidence} if confidence is not None else {}
    return Trace(trace_id=uuid.uuid4().hex, agent_id="a", agent_config_hash="h",
                 test_case_id=tid, visibility="glass_box", final_output="x",
                 spans=[Span(span_id="f", kind="final_output", name="final_output",
                             start_time=NOW, end_time=NOW, attributes=attrs)],
                 schema_version=SCHEMA_VERSION)


def _patch(monkeypatch, *, pass_pattern, confidence=None, extra_crit=None):
    """pass_pattern: list of bools, one per score_op call (i.e. per run)."""
    calls = {"n": 0}

    async def fake_run_suite(cfg, reg, adapter, sid, version, on_progress=None):
        tid = f"{sid}-c0"
        return (None, [SimpleNamespace(test_id=tid)], [_trace(tid, confidence)])

    async def fake_score(cfg, reg, traces, cases, model, on_progress=None,
                         judge_client=None, fi_evaluate_fn=None):
        passed = pass_pattern[calls["n"] % len(pass_pattern)]
        calls["n"] += 1
        crit = [CriterionScore(criterion_id="tool_selection_accuracy",
                               score=1.0 if passed else 0.0, scorer="code")]
        if extra_crit:
            crit.append(CriterionScore(criterion_id=extra_crit,
                                       score=1.0 if passed else 0.0, scorer="code"))
        return [RunScore(trace_id=t.trace_id, test_id=c.test_id, passed=passed,
                         criterion_scores=crit) for c, t in zip(cases, traces)]

    monkeypatch.setattr(ops, "run_suite_op", fake_run_suite)
    monkeypatch.setattr(ops, "score_op", fake_score)


def _run(**kw):
    return asyncio.run(run_standard({}, None, ADAPTER, suite_ids=["std-tool-use-v1"],
                                    judge_client=object(), **kw))


def test_pass_k_flaky_vs_stable(monkeypatch):
    # flaky: passes run 1, fails run 2 -> pass@1=1 but pass^k=0
    _patch(monkeypatch, pass_pattern=[True, False])
    res = _run(k=2)
    assert res["pass_at_1"] == 1.0
    assert res["components"]["reliability_pass_k"] == 0.0
    assert res["k"] == 2 and res["k_runs_cost_usd"] >= 0


def test_pass_k_stable(monkeypatch):
    _patch(monkeypatch, pass_pattern=[True])
    res = _run(k=3)
    assert res["components"]["reliability_pass_k"] == 1.0


def test_ece_when_confidence_present(monkeypatch):
    # agent emits confidence 0.9 but only ~50% correct -> ECE > 0, calibration < 1
    _patch(monkeypatch, pass_pattern=[True, False], confidence=0.9)
    res = _run(k=2)
    assert res["calibration_mode"] == "ece"
    assert res["ece"] is not None and res["ece"] > 0
    assert res["components"]["calibration_ece"] == round(1.0 - res["ece"], 4)


def test_abstention_fallback_when_no_confidence(monkeypatch):
    _patch(monkeypatch, pass_pattern=[True], extra_crit="abstention_correct")
    res = _run(k=1)
    assert res["calibration_mode"] == "abstention_only"
    assert res["ece"] is None
    assert res["components"]["calibration_ece"] == 1.0


def test_full_index_has_no_missing(monkeypatch):
    # both run-level metrics now present -> index covers all weighted components
    _patch(monkeypatch, pass_pattern=[True], confidence=0.95)
    res = _run(k=2)
    assert "reliability_pass_k" not in res["missing"]
    assert "calibration_ece" not in res["missing"]
    assert 0 <= res["index"] <= 100
