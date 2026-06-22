"""Authn/authz matrix: every mutating route requires a token (401 without) and
the operator role (403 for a viewer). Read routes are viewer-accessible.

Complements the focused security tests: SSRF (test_security.py), path traversal
(test_static_safety.py), rate limiting (test_ratelimit.py)."""

import pytest
from fastapi.testclient import TestClient

from ascore.registry.sqlite_store import Registry
from ascore.server.app import create_app

CONFIG = """\
models: {agent_default: a, judge_strong: j, judge_light: l}
harness: {timeout_seconds: 10, max_parallel: 5, transport_retries: 1, max_steps: 10}
scoring: {calibration_threshold: 0.8}
live: {sample_rate: 0.05, drift_threshold: 0.15, drift_window_runs: 50}
paths: {registry_db: %(db)s, review_dir: %(r)s, calibration_dir: %(c)s}
auth: {token: "adm", tokens: {vw: viewer, op: operator}}
"""

# (method, path) for every state-changing endpoint that must require operator+
MUTATING_ROUTES = [
    ("post", "/api/workflows"),
    ("delete", "/api/workflows/x"),
    ("post", "/api/workflows/x/executions"),
    ("post", "/api/executions/x/approve"),
    ("post", "/api/executions/x/cancel"),
    ("post", "/api/executions/x/resume"),
    ("post", "/api/suites/x/approve"),
    ("post", "/api/agents/catalog"),
    ("delete", "/api/agents/catalog/x"),
    ("post", "/api/uploads"),
    ("post", "/api/documents/extract"),
    ("post", "/api/quickstart/from-requirement"),
    ("post", "/api/live/ingest?rubric_id=r"),
]

READ_ROUTES = ["/api/agents", "/api/leaderboard", "/api/scorecards",
               "/api/workflows", "/api/suites", "/api/me",
               "/api/estimate?suite_id=x"]


@pytest.fixture
def client(tmp_path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text(CONFIG % {"db": tmp_path / "a.db", "r": tmp_path / "r",
                             "c": tmp_path / "c"})
    with TestClient(create_app(str(cfg), registry=Registry(tmp_path / "a.db"))) as c:
        yield c


def _call(client, method, path, token=None):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return getattr(client, method)(path, headers=headers)


@pytest.mark.parametrize("method,path", MUTATING_ROUTES)
def test_mutating_route_requires_token(client, method, path):
    assert _call(client, method, path).status_code == 401


@pytest.mark.parametrize("method,path", MUTATING_ROUTES)
def test_mutating_route_forbids_viewer(client, method, path):
    # auth passes (viewer token) but authz must reject with 403
    assert _call(client, method, path, token="vw").status_code == 403


def test_reads_require_token_but_allow_viewer(client):
    for path in READ_ROUTES:
        assert client.get(path).status_code == 401            # no token
        r = client.get(path, headers={"Authorization": "Bearer vw"})
        assert r.status_code != 401 and r.status_code != 403  # viewer can read
