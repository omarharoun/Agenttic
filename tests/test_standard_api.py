"""Standard-benchmarking API: metric catalog, seeding, and the Agenttic Index
rollup endpoint (auth-gated, tenant-scoped)."""

from fastapi.testclient import TestClient

from ascore.registry.sqlite_store import Registry
from ascore.schema.scorecard import CriterionScore, RunScore, Scorecard
from ascore.server.app import create_app

CONFIG = """\
models: {agent_default: a, judge_strong: j, judge_light: l}
harness: {timeout_seconds: 10, max_parallel: 5, transport_retries: 1, max_steps: 10}
scoring: {calibration_threshold: 0.8}
live: {sample_rate: 0.05, drift_threshold: 0.15, drift_window_runs: 50}
paths: {registry_db: %(db)s, review_dir: %(r)s, calibration_dir: %(c)s}
auth: {required: true, token: testtoken}
security: {login_max_attempts: 5, login_lockout_seconds: 900}
"""
AUTH = {"Authorization": "Bearer testtoken"}


def _client(tmp_path, reg=None):
    cfg = tmp_path / "config.yaml"
    cfg.write_text(CONFIG % {"db": tmp_path / "a.db", "r": tmp_path / "r", "c": tmp_path / "c"})
    return TestClient(create_app(str(cfg), registry=reg or Registry(tmp_path / "a.db")))


def test_metric_catalog_cites_methodology(tmp_path):
    with _client(tmp_path) as c:
        r = c.get("/api/standard/metrics", headers=AUTH)
        assert r.status_code == 200
        body = r.json()
        names = {m["id"]: m for m in body["metrics"]}
        assert "BFCL" in names["tool_call_accuracy"]["methodology"]
        assert "AgentHarm" in names["harmful_refusal_rate"]["methodology"]
        assert "AgentDojo" in names["injection_robustness"]["methodology"]
        # faithfulness joined the index this increment (FActScore / RAGAS)
        assert names["faithfulness"]["status"] == "implemented"
        assert "FActScore" in names["faithfulness"]["methodology"]
        assert names["faithfulness"]["weight"] > 0
        # index weights sum to 1 over implemented, weighted metrics
        assert abs(sum(body["index_weights"].values()) - 1.0) < 1e-6


def test_requires_auth(tmp_path):
    with _client(tmp_path) as c:
        assert c.get("/api/standard/metrics").status_code == 401


def test_seed_and_leaderboard(tmp_path):
    reg = Registry(tmp_path / "a.db")
    with _client(tmp_path, reg=reg) as c:
        # seed installs the three standard suites
        seeded = c.post("/api/standard/seed", headers=AUTH).json()["seeded"]
        assert "std-tool-use-v1" in seeded
        assert set(c.get("/api/standard/suites", headers=AUTH).json()["seeded"]) >= set(seeded)

        # no runs yet -> empty leaderboard
        assert c.get("/api/standard/leaderboard", headers=AUTH).json()["agents"] == []

        # save a scorecard for a standard suite (criteria named after check_refs)
        runs = [RunScore(trace_id="t0", test_id="std-tool-use-v1-weather", passed=True,
                criterion_scores=[
                    CriterionScore(criterion_id="tool_selection_accuracy", score=1.0, scorer="code"),
                    CriterionScore(criterion_id="tool_param_accuracy", score=1.0, scorer="code"),
                    CriterionScore(criterion_id="tool_sequence_accuracy", score=1.0, scorer="code"),
                    CriterionScore(criterion_id="abstention_correct", score=1.0, scorer="code"),
                ])]
        sc = Scorecard.aggregate(
            scorecard_id="scX", agent_id="agent-7", suite_id="std-tool-use-v1",
            suite_version=1, rubric_id="std-tool-use-v1-rubric", rubric_version=1,
            run_scores=runs, visibility_tier="glass_box")
        reg.save_scorecard(sc)

        lb = c.get("/api/standard/leaderboard", headers=AUTH).json()["agents"]
        assert len(lb) == 1
        row = lb[0]
        assert row["agent_id"] == "agent-7"
        assert row["components"]["tool_call_accuracy"] == 1.0
        assert 0 <= row["index"] <= 100
        # reliability/calibration not yet run -> reported missing, honest rollup
        assert "reliability_pass_k" in row["missing"]
