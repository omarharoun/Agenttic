"""T15.4 — PAT revocation mid-certify aborts to an errored run: no dossier,
no cache poison (SPEC-2 M6)."""

from __future__ import annotations

import asyncio
import tempfile
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from ascore import ops
from ascore.certification.certify import CertificationAborted, certify
from ascore.registry.sqlite_store import Registry
from ascore.schema.scorecard import CriterionScore, RunScore
from ascore.schema.trace import SCHEMA_VERSION, Span, Trace

NOW = datetime(2026, 6, 21, tzinfo=timezone.utc)
CFG = {
    "models": {"agent_default": "a", "judge_strong": "j", "judge_light": "l"},
    "harness": {"timeout_seconds": 10, "max_parallel": 5, "transport_retries": 1,
                "max_steps": 10},
    "scoring": {"calibration_threshold": 0.8},
    "live": {"sample_rate": 0.05, "drift_threshold": 0.15, "drift_window_runs": 50},
    "paths": {"registry_db": "x", "review_dir": "r", "calibration_dir": "c"},
    "certification": {"profiles": {"cert-agent-safety-v1": {
        "min_k": 2, "required_domains": ["tool_use", "cbrn_proxy"],
        "thresholds": {"tool_use_score": 0.5}}}},
}


@pytest.fixture()
def passing_ops(monkeypatch):
    async def frs(cfg, reg, adapter, sid, version, on_progress=None):
        cases = [SimpleNamespace(test_id=f"{sid}-c{i}") for i in range(4)]
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
                         criterion_scores=[CriterionScore(
                             criterion_id="tool_selection_accuracy", score=1.0,
                             scorer="code")]) for c, t in zip(cases, traces)]

    monkeypatch.setattr(ops, "run_suite_op", frs)
    monkeypatch.setattr(ops, "score_op", fsc)


def test_revocation_aborts_with_no_dossier_or_cache_poison(passing_ops):
    with tempfile.TemporaryDirectory() as tmp:
        reg = Registry(db_path=f"{tmp}/t.db")
        revoked = {"v": True}  # PAT already revoked → abort_check True

        with pytest.raises(CertificationAborted):
            asyncio.run(certify(
                CFG, reg, agent_id="ref-agent", profile_id="cert-agent-safety-v1",
                client=object(), judge_client=object(),
                abort_check=lambda: revoked["v"]))

        # errored run leaves NO dossier
        assert reg.list_dossiers("ref-agent") == []

        # and no cache poison: a later (authorized) certify actually runs and
        # produces a fresh dossier (a poisoned cache would have served nothing).
        revoked["v"] = False
        res = asyncio.run(certify(
            CFG, reg, agent_id="ref-agent", profile_id="cert-agent-safety-v1",
            client=object(), judge_client=object()))
        assert res.cached is False
        assert res.dossier.dossier_id
        assert len(reg.list_dossiers("ref-agent")) == 1
