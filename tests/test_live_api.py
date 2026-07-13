"""Live production path over HTTP: ingest (sampled judge scoring on
live-tagged criteria), batch-trace rejection, and drift status vs a batch
baseline. sample_rate pinned to 1.0 so every ingest scores.
"""

import json
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace as NS

import pytest
from fastapi.testclient import TestClient

from agenttic.registry.sqlite_store import Registry
from agenttic.schema.scorecard import CriterionScore, RunScore, Scorecard
from agenttic.schema.trace import SCHEMA_VERSION, Span, Trace
from agenttic.server.app import create_app
from tests.test_executor import load_pilot

CONFIG = """\
models:
  agent_default: agent-model
  judge_strong: judge-model
  judge_light: judge-light
harness: {{timeout_seconds: 10, max_parallel: 5, transport_retries: 1, max_steps: 10}}
scoring: {{calibration_threshold: 0.8}}
live: {{sample_rate: 1.0, drift_threshold: 0.15, drift_window_runs: 50}}
paths: {{registry_db: {db}, review_dir: r/, calibration_dir: c/}}
"""


class SwitchableJudgeClient:
    """Judge fake whose score can be flipped mid-test (to simulate drift)."""

    def __init__(self):
        self.score = 1.0
        self.messages = NS(create=self._create)

    def _create(self, **kw):
        return NS(content=[NS(type="text", text=json.dumps(
            {"score": self.score, "rationale": "r"}))])


def live_trace(agent_id="prod-agent", final="billing"):
    now = datetime.now(timezone.utc)
    return Trace(
        trace_id=uuid.uuid4().hex, agent_id=agent_id, agent_config_hash="h",
        test_case_id=None,  # production traffic
        spans=[Span(span_id="s1", kind="final_output", name="final_output",
                    start_time=now, end_time=now)],
        visibility="black_box", final_output=final,
        schema_version=SCHEMA_VERSION,
    ).model_dump(mode="json")


def baseline_scorecard(reg, agent_id="prod-agent", tone=1.0):
    runs = [RunScore(trace_id="t0", test_id="tc0", passed=True,
                     criterion_scores=[CriterionScore(
                         criterion_id="tone", score=tone, scorer="judge")])]
    sc = Scorecard.aggregate(
        scorecard_id=uuid.uuid4().hex[:12], agent_id=agent_id,
        suite_id="pilot-support-triage", suite_version=1,
        rubric_id="r-triage", rubric_version=1,
        run_scores=runs, visibility_tier="glass_box")
    reg.save_scorecard(sc)
    return sc


@pytest.fixture
def live_client(tmp_path):
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(CONFIG.format(db=tmp_path / "live.db"))
    reg = Registry(tmp_path / "live.db")
    load_pilot(reg)
    judge = SwitchableJudgeClient()
    app = create_app(str(cfg_path), registry=reg, clients={"judge": judge})
    with TestClient(app) as c:
        c.reg = reg
        c.judge = judge
        yield c


class TestIngest:
    def test_ingest_stores_and_scores(self, live_client):
        r = live_client.post("/api/live/ingest?rubric_id=r-triage",
                             json=live_trace())
        assert r.status_code == 200
        body = r.json()
        assert body["stored"] is True and body["scored"] is True
        traces = live_client.get("/api/traces?mode=live").json()
        assert len(traces) == 1
        # batch table untouched (Hard Rule: live never mixes into batch)
        assert live_client.get("/api/traces?mode=batch").json() == []

    def test_batch_trace_rejected(self, live_client):
        t = live_trace()
        t["test_case_id"] = "tc-1"
        r = live_client.post("/api/live/ingest?rubric_id=r-triage", json=t)
        assert r.status_code == 422
        assert "batch" in r.json()["detail"]

    def test_unknown_rubric_404(self, live_client):
        r = live_client.post("/api/live/ingest?rubric_id=nope",
                             json=live_trace())
        assert r.status_code == 404


class TestDriftStatus:
    def test_no_drift_when_live_matches_baseline(self, live_client):
        sc = baseline_scorecard(live_client.reg)
        for _ in range(5):
            live_client.post("/api/live/ingest?rubric_id=r-triage",
                             json=live_trace())
        r = live_client.get(
            f"/api/live/prod-agent/status?rubric_id=r-triage"
            f"&baseline_scorecard_id={sc.scorecard_id}").json()
        assert r["per_criterion_mean"]["tone"] == 1.0
        assert r["drift_detected"] is False
        assert r["reeval_requests"] == []

    def test_degraded_outputs_trigger_drift_and_reeval(self, live_client):
        sc = baseline_scorecard(live_client.reg, tone=1.0)
        live_client.judge.score = 0.0  # production quality collapses
        for _ in range(5):
            live_client.post("/api/live/ingest?rubric_id=r-triage",
                             json=live_trace())
        r = live_client.get(
            f"/api/live/prod-agent/status?rubric_id=r-triage"
            f"&baseline_scorecard_id={sc.scorecard_id}").json()
        assert r["drifted"] == ["tone"]
        assert r["drift_detected"] is True
        assert any("re-evaluation recommended" in m
                   for m in r["reeval_requests"])
