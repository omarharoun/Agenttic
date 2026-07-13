"""API authentication: a configured token gates every /api route (incl. SSE
and the approval gate); no token configured leaves the API open (dev/test)."""

import pytest
from fastapi.testclient import TestClient

from agenttic.registry.sqlite_store import Registry
from agenttic.server.app import create_app
from agenttic.server.auth import check_startup, configured_token

CONFIG = """\
models:
  agent_default: agent-model
  judge_strong: judge-model
  judge_light: judge-light
harness: {timeout_seconds: 10, max_parallel: 5, transport_retries: 1, max_steps: 10}
scoring: {calibration_threshold: 0.8}
live: {sample_rate: 0.05, drift_threshold: 0.15, drift_window_runs: 50}
paths: {registry_db: %(db)s, review_dir: %(review)s, calibration_dir: %(calib)s}
auth: {required: %(required)s, token: "%(token)s", tokens: %(tokens)s}
"""


def _app(tmp_path, *, token="", required="false", tokens="{}"):
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(CONFIG % {
        "db": tmp_path / "a.db", "review": tmp_path / "r",
        "calib": tmp_path / "c", "token": token, "required": required,
        "tokens": tokens})
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


class TestRoles:
    # admin token "adm", operator "op", viewer "vw"
    TOKENS = '{"op": "operator", "vw": "viewer"}'

    def _c(self, tmp_path):
        return TestClient(_app(tmp_path, token="adm", tokens=self.TOKENS))

    def _hdr(self, t):
        return {"Authorization": f"Bearer {t}"}

    def test_viewer_can_read_not_write(self, tmp_path):
        with self._c(tmp_path) as c:
            assert c.get("/api/agents", headers=self._hdr("vw")).status_code == 200
            # catalog write requires operator
            r = c.post("/api/agents/catalog", headers=self._hdr("vw"),
                       json={"agent_id": "x", "variant": "reference"})
            assert r.status_code == 403

    def test_operator_can_write(self, tmp_path):
        with self._c(tmp_path) as c:
            r = c.post("/api/agents/catalog", headers=self._hdr("op"),
                       json={"agent_id": "x", "variant": "reference"})
            assert r.status_code == 200

    def test_admin_can_write(self, tmp_path):
        with self._c(tmp_path) as c:
            r = c.post("/api/agents/catalog", headers=self._hdr("adm"),
                       json={"agent_id": "y", "variant": "reference"})
            assert r.status_code == 200

    def test_viewer_cannot_trigger_runs_or_approve(self, tmp_path):
        with self._c(tmp_path) as c:
            assert c.post("/api/workflows/none/executions",
                          headers=self._hdr("vw")).status_code == 403
            assert c.post("/api/executions/none/approve",
                          headers=self._hdr("vw")).status_code == 403

    def test_open_api_treats_everyone_as_admin(self, tmp_path):
        # no tokens configured -> open, writes allowed
        with TestClient(_app(tmp_path)) as c:
            assert c.post("/api/agents/catalog",
                          json={"agent_id": "z", "variant": "reference"}
                          ).status_code == 200
            assert c.get("/api/me").json()["role"] == "admin"

    def test_me_reports_role(self, tmp_path):
        with self._c(tmp_path) as c:
            assert c.get("/api/me", headers=self._hdr("vw")).json()["role"] == "viewer"
            assert c.get("/api/me", headers=self._hdr("op")).json()["role"] == "operator"


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
