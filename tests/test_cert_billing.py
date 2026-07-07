"""T15.5 — evaluator BYO-key judge billing + ceilings (SPEC-2 M6).

The certification run's cost is billed to the running tenant's spend ledger (the
BYO key), and a tenant already at its daily cap cannot certify.
"""

from __future__ import annotations

import asyncio
import tempfile
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from ascore import ops
from ascore.budget import BudgetExceededError
from ascore.certification.certify import certify
from ascore.registry.sqlite_store import Registry
from ascore.schema.scorecard import CriterionScore, RunScore
from ascore.schema.trace import SCHEMA_VERSION, Span, Trace

NOW = datetime(2026, 6, 21, tzinfo=timezone.utc)


def _cfg(daily_cap: float = 0.0):
    return {
        "models": {"agent_default": "a", "judge_strong": "judge-model",
                   "judge_light": "l"},
        "harness": {"timeout_seconds": 10, "max_parallel": 5,
                    "transport_retries": 1, "max_steps": 10},
        "scoring": {"calibration_threshold": 0.8},
        "live": {"sample_rate": 0.05, "drift_threshold": 0.15,
                 "drift_window_runs": 50},
        "paths": {"registry_db": "x", "review_dir": "r", "calibration_dir": "c"},
        "budget": {"max_run_cost_usd": 0.0, "max_daily_cost_usd": daily_cap,
                   "warn_only": False},
        "certification": {"profiles": {"cert-agent-safety-v1": {
            "min_k": 2, "required_domains": ["tool_use", "cbrn_proxy"],
            "thresholds": {"tool_use_score": 0.5}}}},
    }


@pytest.fixture()
def costing_ops(monkeypatch):
    async def frs(cfg, reg, adapter, sid, version, on_progress=None):
        cases = [SimpleNamespace(test_id=f"{sid}-c{i}") for i in range(3)]
        traces = [Trace(trace_id=c.test_id + "-t", agent_id="a",
                        agent_config_hash="h", test_case_id=c.test_id,
                        visibility="glass_box", final_output="x",
                        spans=[Span(span_id="f", kind="final_output",
                                    name="final_output", start_time=NOW,
                                    end_time=NOW, attributes={})],
                        schema_version=SCHEMA_VERSION) for c in cases]
        return (None, cases, traces)

    async def fsc(cfg, reg, traces, cases, model, on_progress=None,
                  judge_client=None, fi_evaluate_fn=None):
        return [RunScore(trace_id=t.trace_id, test_id=c.test_id, passed=True,
                         cost_usd=0.01,
                         criterion_scores=[CriterionScore(
                             criterion_id="tool_selection_accuracy", score=1.0,
                             scorer="code")]) for c, t in zip(cases, traces)]

    monkeypatch.setattr(ops, "run_suite_op", frs)
    monkeypatch.setattr(ops, "score_op", fsc)


def test_certification_cost_billed_to_tenant(costing_ops):
    with tempfile.TemporaryDirectory() as tmp:
        reg = Registry(db_path=f"{tmp}/t.db")
        assert reg.spend_today() == 0.0
        asyncio.run(certify(_cfg(), reg, agent_id="ref-agent",
                            profile_id="cert-agent-safety-v1",
                            client=object(), judge_client=object()))
        assert reg.spend_today() > 0.0  # billed


def test_tenant_over_daily_cap_cannot_certify(costing_ops):
    with tempfile.TemporaryDirectory() as tmp:
        reg = Registry(db_path=f"{tmp}/t.db")
        reg.record_spend("judge-model", 5.0)  # already over the $1 daily cap
        with pytest.raises(BudgetExceededError):
            asyncio.run(certify(_cfg(daily_cap=1.0), reg, agent_id="ref-agent",
                                profile_id="cert-agent-safety-v1",
                                client=object(), judge_client=object()))
        assert reg.list_dossiers("ref-agent") == []
