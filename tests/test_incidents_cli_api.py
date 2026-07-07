"""T16.6 — incidents CLI + API surface (SPEC-2 M6)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from ascore.registry.sqlite_store import Registry
from ascore.server.app import create_app
from pathlib import Path

CONFIG = """\
models: {agent_default: a, judge_strong: j, judge_light: l}
harness: {timeout_seconds: 10, max_parallel: 5, transport_retries: 1, max_steps: 10}
scoring: {calibration_threshold: 0.8}
live: {sample_rate: 0.05, drift_threshold: 0.15, drift_window_runs: 50}
paths: {registry_db: %(db)s, review_dir: %(r)s, calibration_dir: %(c)s}
auth: {required: true, token: testtoken}
security: {login_max_attempts: 5, login_lockout_seconds: 900}
incidents:
  sla_hours: {S1: 72, S2: 72, S3: 168, S4: 336}
"""
AUTH = {"Authorization": "Bearer testtoken"}


def _client(tmp_path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text(CONFIG % {"db": tmp_path / "a.db", "r": tmp_path / "r",
                             "c": tmp_path / "c"})
    return TestClient(create_app(str(cfg), registry=Registry(tmp_path / "a.db")))


def test_incident_lifecycle_over_api(tmp_path):
    with _client(tmp_path) as c:
        # open
        r = c.post("/api/incidents", headers=AUTH,
                   json={"agent_id": "ref-agent", "severity": "S2",
                         "title": "drift"})
        assert r.status_code == 200
        iid = r.json()["incident_id"]
        # list with SLA clock
        rows = c.get("/api/incidents", headers=AUTH).json()
        assert any(x["incident_id"] == iid and "sla_due" in x for x in rows)
        # illegal transition (open -> reported) → 409
        assert c.post(f"/api/incidents/{iid}/transition", headers=AUTH,
                      json={"to_state": "reported"}).status_code == 409
        # legal path
        assert c.post(f"/api/incidents/{iid}/transition", headers=AUTH,
                      json={"to_state": "triaged"}).status_code == 200
        assert c.post(f"/api/incidents/{iid}/transition", headers=AUTH,
                      json={"to_state": "closed"}).status_code == 200
        # export schema
        exp = c.get(f"/api/incidents/{iid}/export", headers=AUTH).json()
        assert exp["schema"] == "agenttic-incident-export/v1"
        assert exp["status"] == "closed"


def test_incidents_cli(tmp_path, monkeypatch):
    from typer.testing import CliRunner

    from ascore.cli import app
    monkeypatch.setenv("ASCORE_TENANT", "inccli")
    monkeypatch.chdir(Path(__file__).resolve().parents[1])
    runner = CliRunner()
    try:
        out = runner.invoke(app, ["incidents", "open", "ref-agent",
                                  "--severity", "S1", "--title", "t"])
        assert out.exit_code == 0, out.stdout
        lst = runner.invoke(app, ["incidents", "list"])
        assert "ref-agent" in lst.stdout
    finally:
        import os
        if os.path.exists("ascore.inccli.db"):
            os.remove("ascore.inccli.db")
