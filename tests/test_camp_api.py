"""Training Camp API: start a camp, read results (Wilson bound + gate + memory),
the human sign-off (floor non-overridable), the improve loop, and the
distillation export. Auth-gated and tenant-scoped."""

from fastapi.testclient import TestClient

from ascore.registry.sqlite_store import Registry
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


def _client(tmp_path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text(CONFIG % {"db": tmp_path / "a.db", "r": tmp_path / "r",
                             "c": tmp_path / "c"})
    return TestClient(create_app(str(cfg), registry=Registry(tmp_path / "a.db")))


def test_requires_auth(tmp_path):
    with _client(tmp_path) as c:
        assert c.get("/api/camps").status_code == 401
        assert c.post("/api/camps", json={}).status_code == 401


def test_tasks_catalogue(tmp_path):
    with _client(tmp_path) as c:
        body = c.get("/api/camps/tasks", headers=AUTH).json()
        assert any(t["task_id"] == "support_triage" for t in body["tasks"])
        assert "mock" in body["modes"]


def test_start_camp_reports_wilson_and_blocks_below_floor(tmp_path):
    with _client(tmp_path) as c:
        r = c.post("/api/camps", headers=AUTH, json={
            "episodes": 500, "threshold": 0.99, "seed": 0})
        assert r.status_code == 200, r.text
        run = r.json()
        assert run["episodes"] == 500
        assert 0.0 < run["wilson_lower_95"] < 1.0
        # baseline ~85% can't clear a 99% floor
        assert run["gate"]["floor_met"] is False
        assert run["gate"]["promoted"] is False

        # visible in the list + detail carries the episode memory
        runs = c.get("/api/camps", headers=AUTH).json()["runs"]
        assert any(x["run_id"] == run["run_id"] for x in runs)
        detail = c.get(f"/api/camps/{run['run_id']}", headers=AUTH).json()
        assert detail["episode_count"] == 500
        assert len(detail["episode_sample"]) == 25


def test_human_signoff_cannot_override_floor(tmp_path):
    with _client(tmp_path) as c:
        run = c.post("/api/camps", headers=AUTH, json={
            "episodes": 500, "threshold": 0.99, "seed": 0}).json()
        approved = c.post(f"/api/camps/{run['run_id']}/approve",
                          headers=AUTH).json()
        # human signed off, but the floor isn't met -> still not promoted, and no
        # approver is recorded against a run that didn't clear the floor
        assert approved["gate"]["human_approved"] is True
        assert approved["gate"]["promoted"] is False
        assert approved["approved_by"] is None


def test_promotion_requires_floor_and_signoff(tmp_path):
    with _client(tmp_path) as c:
        # low floor the baseline clears
        run = c.post("/api/camps", headers=AUTH, json={
            "episodes": 800, "threshold": 0.70, "seed": 0}).json()
        assert run["gate"]["floor_met"] is True
        assert run["gate"]["promoted"] is False  # not yet approved

        approved = c.post(f"/api/camps/{run['run_id']}/approve",
                          headers=AUTH).json()
        assert approved["gate"]["promoted"] is True
        assert approved["approved_by"]  # a real operator identity recorded


def test_distillation_export_downloads_passing_episodes(tmp_path):
    with _client(tmp_path) as c:
        run = c.post("/api/camps", headers=AUTH, json={
            "episodes": 300, "threshold": 0.70, "seed": 0}).json()
        detail = c.get(f"/api/camps/{run['run_id']}", headers=AUTH).json()
        r = c.get(f"/api/camps/{run['run_id']}/distillation.jsonl", headers=AUTH)
        assert r.status_code == 200
        lines = [ln for ln in r.text.splitlines() if ln.strip()]
        assert len(lines) == detail["distillation_count"] == run["passes"]
        import json
        rec = json.loads(lines[0])
        assert [m["role"] for m in rec["messages"]] == \
            ["system", "user", "assistant"]


def test_improve_loop_endpoint_ratchets_and_degenerate_blocks(tmp_path):
    with _client(tmp_path) as c:
        honest = c.post("/api/camps/improve", headers=AUTH, json={
            "rounds": 5, "episodes_per_round": 300, "threshold": 0.95,
            "holdout": 400, "seed": 1}).json()
        assert honest["kind"] == "improve"
        assert honest["report"]["final_champion_gen"] >= 1
        assert len(honest["rounds"]) >= 1

        degen = c.post("/api/camps/improve", headers=AUTH, json={
            "rounds": 6, "episodes_per_round": 300, "threshold": 0.99,
            "holdout": 400, "seed": 1, "degenerate": True}).json()
        assert degen["gate"]["promoted"] is False
        assert ("stall" in degen["report"]["halted_reason"] or
                "escalate" in degen["report"]["halted_reason"])


def test_agent_mode_capped_and_errors_without_key(tmp_path):
    with _client(tmp_path) as c:
        # over the agent cap
        over = c.post("/api/camps", headers=AUTH, json={
            "mode": "agent", "episodes": 999})
        assert over.status_code == 400
        # within cap but no Anthropic key set for the tenant -> 400 from key layer
        nokey = c.post("/api/camps", headers=AUTH, json={
            "mode": "agent", "episodes": 10})
        assert nokey.status_code == 400
