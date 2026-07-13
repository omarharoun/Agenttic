"""T13.4 — the sandbagging probe catches a sandbagger fixture; the honest twin
passes clean (SPEC-2 M5)."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

import hashlib
import json

import pytest

from agenttic import ops
from agenttic.certification.elicitation import (
    analyze_elicitation,
    apply_elicitation,
    load_elicitation_configs,
    run_matrix,
)
from agenttic.config import load_config
from agenttic.schema.scorecard import CriterionScore, RunScore
from agenttic.schema.trace import SCHEMA_VERSION, Span, Trace

NOW = datetime(2026, 6, 21, tzinfo=timezone.utc)
N_CASES = 8


class _Adapter:
    """Minimal adapter exposing describe()/config_hash() + elicitation knobs."""

    agent_id = "probe"
    visibility = "glass_box"
    model = "m"

    def __init__(self):
        self.system_prompt = "base"
        self.max_steps = 10

    def describe(self) -> dict:
        return {"sp": self.system_prompt, "ms": self.max_steps}

    def config_hash(self) -> str:
        return hashlib.sha256(
            json.dumps(self.describe(), sort_keys=True).encode()
        ).hexdigest()[:16]


def _trace(tid: str) -> Trace:
    return Trace(
        trace_id=tid + "-t", agent_id="probe", agent_config_hash="h",
        test_case_id=tid, visibility="glass_box", final_output="x",
        spans=[Span(span_id="f", kind="final_output", name="final_output",
                    start_time=NOW, end_time=NOW, attributes={})],
        schema_version=SCHEMA_VERSION)


def _install(monkeypatch, pass_fn):
    """pass_fn(system_prompt) -> bool decides pass/fail for the current run."""
    state = {"sp": "base"}

    async def frs(cfg, reg, adapter, sid, version, on_progress=None):
        state["sp"] = adapter.system_prompt
        # a stable set of cases per suite
        cases = [SimpleNamespace(test_id=f"{sid}-c{i}") for i in range(N_CASES)]
        return (None, cases, [_trace(c.test_id) for c in cases])

    async def fsc(cfg, reg, traces, cases, model, on_progress=None,
                  judge_client=None, fi_evaluate_fn=None):
        passed = pass_fn(state["sp"])
        out = []
        for c, t in zip(cases, traces):
            crit = [CriterionScore(criterion_id="tool_selection_accuracy",
                                   score=1.0 if passed else 0.0, scorer="code")]
            out.append(RunScore(trace_id=t.trace_id, test_id=c.test_id,
                                passed=passed, criterion_scores=crit))
        return out

    monkeypatch.setattr(ops, "run_suite_op", frs)
    monkeypatch.setattr(ops, "score_op", fsc)


def _matrix(cfg, pass_fn, monkeypatch):
    _install(monkeypatch, pass_fn)
    return asyncio.run(run_matrix(
        cfg, None, _Adapter(), k=2, suite_ids=["std-tool-use-v1"],
        judge_client=object()))


@pytest.fixture()
def cfg():
    return load_config("config.yaml")


def test_config_hashes_distinct(cfg):
    a = _Adapter()
    configs = load_elicitation_configs(cfg)
    hn = apply_elicitation(a, configs["neutral"]).config_hash()
    hs = apply_elicitation(a, configs["strong"]).config_hash()
    assert hn != hs


def test_sandbagger_flagged(cfg, monkeypatch):
    # Sandbagger: fails under the neutral (eval-looking) prompt, performs under
    # strong elicitation. Big negative gap → INCONSISTENT.
    def sandbag(sp: str) -> bool:
        return sp != "base"  # only performs under the strong prompt
    m = _matrix(cfg, sandbag, monkeypatch)
    analysis = analyze_elicitation(m, cfg)
    assert analysis.inconsistent
    assert any("sandbag" in f.lower() or "gap" in f.lower() for f in analysis.flags)


def test_honest_twin_not_flagged(cfg, monkeypatch):
    # Honest twin: same performance regardless of prompt → gap ≈ 0.
    def honest(sp: str) -> bool:
        return True
    m = _matrix(cfg, honest, monkeypatch)
    analysis = analyze_elicitation(m, cfg)
    assert not analysis.inconsistent
    assert not analysis.underpowered


def test_underpowered_never_passes(cfg, monkeypatch):
    global N_CASES
    old = N_CASES
    try:
        # only 2 cases -> underpowered
        import tests.test_cert_elicitation as mod
        mod.N_CASES = 2
        m = _matrix(cfg, lambda sp: sp != "base", monkeypatch)
        analysis = analyze_elicitation(m, cfg)
        assert analysis.underpowered
        # underpowered task_success gap is never "consistent" (a clean pass)
        ts = next(g for g in analysis.metrics if g.metric == "task_success")
        assert ts.status == "underpowered"
    finally:
        mod.N_CASES = old
