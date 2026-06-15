"""API authentication: a configured token gates every /api route (incl. SSE
and the approval gate); no token configured leaves the API open (dev/test)."""

import pytest
from fastapi.testclient import TestClient

from ascore.registry.sqlite_store import Registry
from ascore.server.app import create_app
from ascore.server.auth import check_startup, configured_token

CONFIG = """\
models:
  agent_default: agent-model
  judge_strong: judge-model
  judge_light: judge-light
harness: {timeout_seconds: 10, max_parallel: 5, transport_retries: 1, max_steps: 10}
scoring: {calibration_threshold: 0.8}
live: {sample_rate: 0.05, drift_threshold: 0.15, drift_window_runs: 50}
paths: {registry_db: %(db)s, review_dir: %(review)s, calibration_dir: %(calib)s}
auth: {required: %(required)s, token: "%(token)s"}
"""


def _app(tmp_path, *, token="", required="false"):
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(CONFIG % {
        "db": tmp_path / "a.db", "review": tmp_path / "r",
        "calib": tmp_path / "c", "token": token, "required": required})
    return create_app(str(cfg_path), registry=Registry(tmp_path / "a.db"))


class TestTokenEnforcement:
    def test_open_when_no_token(self, tmp_path):
        with TestClient(_app(tmp_path)) as c:
            assert c.get("/api/agents").status_code == 200

    def test_401_without_token(self, tmp_path):
        with TestClient(_app(tmp_path, token="s3cret")) as c:
            assert c.get("/api/agents").status_code == 401
            assert c.get("/api/leaderboard").status_code == 401

    def test_200_with_bearer_or_apikey(self, tmp_path):
        with TestClient(_app(tmp_path, token="s3cret")) as c:
            assert c.get("/api/agents",
                         headers={"Authorization": "Bearer s3cret"}).status_code == 200
            assert c.get("/api/agents",
                         headers={"X-API-Key": "s3cret"}).status_code == 200

    def test_wrong_token_rejected(self, tmp_path):
        with TestClient(_app(tmp_path, token="s3cret")) as c:
            assert c.get("/api/agents",
                         headers={"Authorization": "Bearer nope"}).status_code == 401

    def test_sse_accepts_query_token(self, tmp_path):
        # EventSource can't set headers — the token rides as ?token=
        with TestClient(_app(tmp_path, token="s3cret")) as c:
            # missing -> 401; the events route 404s only AFTER auth passes
            assert c.get("/api/executions/none/events").status_code == 401
            r = c.get("/api/executions/none/events?token=s3cret")
            assert r.status_code == 404  # auth passed, execution simply absent

    def test_approval_gate_requires_auth(self, tmp_path):
        with TestClient(_app(tmp_path, token="s3cret")) as c:
            assert c.post("/api/executions/x/approve").status_code == 401


class TestStartupFailClosed:
    def test_required_without_token_raises(self):
        with pytest.raises(RuntimeError):
            check_startup({"auth": {"required": True, "token": ""}})

    def test_required_with_token_ok(self):
        check_startup({"auth": {"required": True, "token": "x"}})

    def test_env_token_takes_precedence(self, monkeypatch):
        monkeypatch.setenv("ASCORE_API_TOKEN", "fromenv")
        assert configured_token({"auth": {"token": "fromcfg"}}) == "fromenv"
        monkeypatch.delenv("ASCORE_API_TOKEN")
        assert configured_token({"auth": {"token": "fromcfg"}}) == "fromcfg"
