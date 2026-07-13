"""T14.6 — certify + dossier server endpoints (tenancy + async job)."""

from __future__ import annotations

import time

from fastapi.testclient import TestClient

from agenttic import ops
from agenttic.registry.sqlite_store import Registry
from agenttic.schema.scorecard import CriterionScore, RunScore
from agenttic.schema.trace import SCHEMA_VERSION, Span, Trace
from agenttic.server.app import create_app
from datetime import datetime, timezone
from types import SimpleNamespace

NOW = datetime(2026, 6, 21, tzinfo=timezone.utc)

CONFIG = """\
models: {agent_default: a, judge_strong: j, judge_light: l}
harness: {timeout_seconds: 10, max_parallel: 5, transport_retries: 1, max_steps: 10}
scoring: {calibration_threshold: 0.8}
live: {sample_rate: 0.05, drift_threshold: 0.15, drift_window_runs: 50}
paths: {registry_db: %(db)s, review_dir: %(r)s, calibration_dir: %(c)s}
auth: {required: true, token: testtoken}
security: {login_max_attempts: 5, login_lockout_seconds: 900}
certification:
  profiles:
    cert-agent-safety-v1:
      min_k: 2
      required_domains: [tool_use, harm_refusal, injection_robustness, autonomy_proxy, deception_probe, cbrn_proxy]
      thresholds: {tool_use_score: 0.5}
incidents:
  sla_hours: {S1: 72, S2: 72, S3: 168, S4: 336}
"""
AUTH = {"Authorization": "Bearer testtoken"}


def _install_passing(monkeypatch):
    async def frs(cfg, reg, adapter, sid, version, on_progress=None):
        cases = [SimpleNamespace(test_id=f"{sid}-c{i}") for i in range(6)]
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
                             criterion_id="tool_selection_accuracy",
                             score=1.0, scorer="code")])
                for c, t in zip(cases, traces)]

    monkeypatch.setattr(ops, "run_suite_op", frs)
    monkeypatch.setattr(ops, "score_op", fsc)


def _client(tmp_path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text(CONFIG % {"db": tmp_path / "a.db", "r": tmp_path / "r",
                             "c": tmp_path / "c"})
    reg = Registry(tmp_path / "a.db")
    fake = object()
    app = create_app(str(cfg), registry=reg,
                     clients={"agent": fake, "judge": fake})
    return TestClient(app)


def test_certify_job_and_dossier_endpoints(tmp_path, monkeypatch):
    _install_passing(monkeypatch)
    with _client(tmp_path) as c:
        # unknown profile -> 404
        assert c.post("/api/certify", headers=AUTH,
                      json={"profile_id": "nope"}).status_code == 404
        # launch a real certify job
        r = c.post("/api/certify", headers=AUTH,
                   json={"agent_id": "ref-agent",
                         "profile_id": "cert-agent-safety-v1"})
        assert r.status_code == 200
        job_id = r.json()["job_id"]
        # poll to completion
        status = None
        for _ in range(50):
            status = c.get(f"/api/certify/jobs/{job_id}", headers=AUTH).json()
            if status["status"] in ("succeeded", "failed"):
                break
            time.sleep(0.05)
        assert status["status"] == "succeeded", status
        dossier_id = status["dossier_id"]
        # list + fetch + pdf
        lst = c.get("/api/dossiers", headers=AUTH).json()
        assert any(d["dossier_id"] == dossier_id for d in lst)
        got = c.get(f"/api/dossiers/{dossier_id}", headers=AUTH).json()
        assert got["agent_id"] == "ref-agent"
        pdf = c.get(f"/api/dossiers/{dossier_id}/report.pdf", headers=AUTH)
        assert pdf.status_code == 200 and pdf.content[:5] == b"%PDF-"


def test_dossiers_require_auth(tmp_path):
    with _client(tmp_path) as c:
        assert c.get("/api/dossiers").status_code == 401
