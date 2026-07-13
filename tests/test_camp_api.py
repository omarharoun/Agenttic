"""Training Camp API: ASYNC start (returns 202 + a running row immediately),
background completion → succeeded with results, the failure path → failed +
message, the human sign-off (floor non-overridable), the improve loop, and the
distillation export. Auth-gated and tenant-scoped."""

import time

from fastapi.testclient import TestClient

from agenttic.registry.sqlite_store import Registry
from agenttic.server.app import create_app

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


def _await(c, run_id, timeout=25):
    """Poll a run to a terminal state (mirrors what the frontend does)."""
    deadline = time.time() + timeout
    run = None
    while time.time() < deadline:
        run = c.get(f"/api/camps/{run_id}", headers=AUTH).json()
        if run["status"] in ("succeeded", "failed"):
            return run
        time.sleep(0.05)
    raise AssertionError(f"run {run_id} never finished (status={run and run['status']})")


def test_requires_auth(tmp_path):
    with _client(tmp_path) as c:
        assert c.get("/api/camps").status_code == 401
        assert c.post("/api/camps", json={}).status_code == 401


def test_tasks_catalogue(tmp_path):
    with _client(tmp_path) as c:
        body = c.get("/api/camps/tasks", headers=AUTH).json()
        assert any(t["task_id"] == "support_triage" for t in body["tasks"])
        assert "mock" in body["modes"]


def test_start_is_async_then_succeeds_with_wilson(tmp_path):
    with _client(tmp_path) as c:
        r = c.post("/api/camps", headers=AUTH, json={
            "episodes": 500, "threshold": 0.99, "seed": 0})
        # returns immediately: 202 + a running row (no long-held request)
        assert r.status_code == 202, r.text
        started = r.json()
        assert started["status"] == "running"
        assert started["total_episodes"] == 500
        assert started["episodes_completed"] == 0

        run = _await(c, started["run_id"])
        assert run["status"] == "succeeded"
        assert 0.0 < run["wilson_lower_95"] < 1.0
        assert run["gate"]["floor_met"] is False   # baseline ~85% < 99% floor
        assert run["gate"]["promoted"] is False
        assert run["episode_count"] == 500
        assert len(run["episode_sample"]) == 25

        runs = c.get("/api/camps", headers=AUTH).json()["runs"]
        assert any(x["run_id"] == run["run_id"] for x in runs)


def test_background_failure_sets_failed_with_message(tmp_path, monkeypatch):
    import agenttic.server.routes.camp as camp_routes

    def boom(**_kw):
        raise RuntimeError("kaboom in the background")
    monkeypatch.setattr(camp_routes.service, "run_single_camp", boom)

    with _client(tmp_path) as c:
        started = c.post("/api/camps", headers=AUTH, json={"episodes": 50}).json()
        assert started["status"] == "running"
        run = _await(c, started["run_id"])
        assert run["status"] == "failed"
        assert "kaboom" in (run["error"] or "")


def test_human_signoff_cannot_override_floor(tmp_path):
    with _client(tmp_path) as c:
        started = c.post("/api/camps", headers=AUTH, json={
            "episodes": 500, "threshold": 0.99, "seed": 0}).json()
        _await(c, started["run_id"])
        approved = c.post(f"/api/camps/{started['run_id']}/approve",
                          headers=AUTH).json()
        assert approved["gate"]["human_approved"] is True
        assert approved["gate"]["promoted"] is False
        assert approved["approved_by"] is None


def test_promotion_requires_floor_and_signoff(tmp_path):
    with _client(tmp_path) as c:
        started = c.post("/api/camps", headers=AUTH, json={
            "episodes": 800, "threshold": 0.70, "seed": 0}).json()
        run = _await(c, started["run_id"])
        assert run["gate"]["floor_met"] is True
        assert run["gate"]["promoted"] is False

        approved = c.post(f"/api/camps/{run['run_id']}/approve",
                          headers=AUTH).json()
        assert approved["gate"]["promoted"] is True
        assert approved["approved_by"]


def test_cannot_approve_while_running(tmp_path, monkeypatch):
    # A run stuck 'running' can't be approved (422).
    import agenttic.server.routes.camp as camp_routes

    def slow(**_kw):
        time.sleep(2)
        raise RuntimeError("done late")
    monkeypatch.setattr(camp_routes.service, "run_single_camp", slow)
    with _client(tmp_path) as c:
        started = c.post("/api/camps", headers=AUTH, json={"episodes": 10}).json()
        early = c.post(f"/api/camps/{started['run_id']}/approve", headers=AUTH)
        assert early.status_code == 422


def test_distillation_export_downloads_passing_episodes(tmp_path):
    with _client(tmp_path) as c:
        started = c.post("/api/camps", headers=AUTH, json={
            "episodes": 300, "threshold": 0.70, "seed": 0}).json()
        run = _await(c, started["run_id"])
        r = c.get(f"/api/camps/{run['run_id']}/distillation.jsonl", headers=AUTH)
        assert r.status_code == 200
        lines = [ln for ln in r.text.splitlines() if ln.strip()]
        assert len(lines) == run["distillation_count"] == run["passes"]
        import json
        rec = json.loads(lines[0])
        assert [m["role"] for m in rec["messages"]] == \
            ["system", "user", "assistant"]


def test_improve_loop_endpoint_ratchets_and_degenerate_blocks(tmp_path):
    with _client(tmp_path) as c:
        h = c.post("/api/camps/improve", headers=AUTH, json={
            "rounds": 5, "episodes_per_round": 300, "threshold": 0.95,
            "holdout": 400, "seed": 1}).json()
        assert h["status"] == "running"
        honest = _await(c, h["run_id"])
        assert honest["kind"] == "improve"
        assert honest["report"]["final_champion_gen"] >= 1
        assert len(honest["rounds"]) >= 1

        d = c.post("/api/camps/improve", headers=AUTH, json={
            "rounds": 6, "episodes_per_round": 300, "threshold": 0.99,
            "holdout": 400, "seed": 1, "degenerate": True}).json()
        degen = _await(c, d["run_id"])
        assert degen["gate"]["promoted"] is False
        assert ("stall" in degen["report"]["halted_reason"] or
                "escalate" in degen["report"]["halted_reason"])


def test_agent_mode_capped_and_errors_without_key(tmp_path):
    with _client(tmp_path) as c:
        over = c.post("/api/camps", headers=AUTH, json={
            "mode": "agent", "episodes": 999})
        assert over.status_code == 400  # over the agent cap (fails fast)
        nokey = c.post("/api/camps", headers=AUTH, json={
            "mode": "agent", "episodes": 10})
        assert nokey.status_code == 400  # no Anthropic key set (fails fast)
