"""Settings API (BYO Anthropic key) + the run gate when no key is set.

These use create_app WITHOUT injected LLM clients, so the per-tenant key path
is exercised (the rest of the suite injects clients and bypasses it)."""

import agenttic.server.keys as keys
from agenttic.registry.sqlite_store import Registry
from agenttic.server.app import create_app
from fastapi.testclient import TestClient

CONFIG = """\
models: {agent_default: a, judge_strong: j, judge_light: l}
harness: {timeout_seconds: 10, max_parallel: 5, transport_retries: 1, max_steps: 10}
scoring: {calibration_threshold: 0.8}
live: {sample_rate: 0.05, drift_threshold: 0.15, drift_window_runs: 50}
paths: {registry_db: %(db)s, review_dir: %(r)s, calibration_dir: %(c)s}
auth: {required: true, allow_signup: true, signup_role: admin, session_secret: testsecret}
security: {login_max_attempts: 5, login_lockout_seconds: 900}
"""


def _client(tmp_path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text(CONFIG % {"db": tmp_path / "a.db", "r": tmp_path / "r",
                             "c": tmp_path / "c"})
    return TestClient(create_app(str(cfg), registry=Registry(tmp_path / "a.db")))


def _signup(c):
    r = c.post("/api/auth/signup", json={"email": "a@b.com", "password": "password123"})
    # cookie session: unsafe methods need the CSRF double-submit header
    c.headers.update({"X-CSRF-Token": r.json()["csrf_token"]})


def test_key_status_empty_then_set_masked(tmp_path, monkeypatch):
    monkeypatch.setattr(keys, "validate_anthropic_key", lambda k: (True, ""))
    with _client(tmp_path) as c:
        _signup(c)
        assert c.get("/api/settings/anthropic-key").json() == {
            "set": False, "masked": None, "updated_at": None}
        r = c.put("/api/settings/anthropic-key",
                  json={"key": "sk-ant-supersecretkey-4242"})
        assert r.status_code == 200 and r.json()["set"] is True
        assert r.json()["masked"] == "sk-ant-…4242"
        # the raw key must never come back in any response
        assert "supersecretkey" not in r.text
        assert "supersecretkey" not in c.get("/api/settings/anthropic-key").text


def test_raw_key_never_in_responses_or_logs(tmp_path, monkeypatch, caplog):
    import logging
    monkeypatch.setattr(keys, "validate_anthropic_key", lambda k: (True, ""))
    SECRET = "sk-ant-DONOTLEAKvalue-0001"
    with _client(tmp_path) as c, caplog.at_level(logging.DEBUG):
        _signup(c)
        r_put = c.put("/api/settings/anthropic-key", json={"key": SECRET})
        r_get = c.get("/api/settings/anthropic-key")
        r_test = c.post("/api/settings/anthropic-key/test", json={"key": SECRET})
    # the secret must not appear in ANY response body...
    for r in (r_put, r_get, r_test):
        assert "DONOTLEAK" not in r.text
    # ...nor in any log line
    assert "DONOTLEAK" not in caplog.text
    # only the masked last4 is surfaced
    assert r_get.json()["masked"] == "sk-ant-…0001"


def test_invalid_key_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(keys, "validate_anthropic_key",
                        lambda k: (False, "authentication failed"))
    with _client(tmp_path) as c:
        _signup(c)
        r = c.put("/api/settings/anthropic-key", json={"key": "sk-ant-bad"})
        assert r.status_code == 422 and "authentication failed" in r.text


def test_run_blocked_without_key(tmp_path):
    with _client(tmp_path) as c:
        _signup(c)
        doc = {"workflow_id": "w1", "name": "t",
               "nodes": [{"node_id": "d", "type": "business_doc",
                          "config": {"text": "x"}, "position": {"x": 0, "y": 0}}],
               "edges": []}
        assert c.post("/api/workflows", json=doc).status_code == 200
        r = c.post("/api/workflows/w1/executions")
        assert r.status_code == 400
        assert "Anthropic API key" in r.json()["detail"]


def test_run_allowed_once_key_set(tmp_path, monkeypatch):
    # with a key set, the gate passes and the run starts (build clients stubbed
    # so no network); we only assert the gate no longer blocks.
    monkeypatch.setattr(keys, "validate_anthropic_key", lambda k: (True, ""))
    monkeypatch.setattr("agenttic.server.keys.build_tenant_clients",
                        lambda key: {"agent": object()})
    with _client(tmp_path) as c:
        _signup(c)
        c.put("/api/settings/anthropic-key", json={"key": "sk-ant-okokokokok-1234"})
        doc = {"workflow_id": "w1", "name": "t",
               "nodes": [{"node_id": "d", "type": "business_doc",
                          "config": {"text": "x"}, "position": {"x": 0, "y": 0}}],
               "edges": []}
        c.post("/api/workflows", json=doc)
        r = c.post("/api/workflows/w1/executions")
        assert r.status_code == 200 and "execution_id" in r.json()
