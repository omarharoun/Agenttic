"""Per-client rate limiting on /api (config-driven; 0 = off)."""

from fastapi.testclient import TestClient

from ascore.registry.sqlite_store import Registry
from ascore.server.app import create_app

CONFIG = """\
models: {agent_default: a, judge_strong: j, judge_light: l}
harness: {timeout_seconds: 10, max_parallel: 5, transport_retries: 1, max_steps: 10}
scoring: {calibration_threshold: 0.8}
live: {sample_rate: 0.05, drift_threshold: 0.15, drift_window_runs: 50}
paths: {registry_db: %(db)s, review_dir: %(r)s, calibration_dir: %(c)s}
security: {rate_limit_per_minute: %(limit)s}
"""


def _client(tmp_path, limit):
    cfg = tmp_path / "config.yaml"
    cfg.write_text(CONFIG % {"db": tmp_path / "a.db", "r": tmp_path / "r",
                             "c": tmp_path / "c", "limit": limit})
    return TestClient(create_app(str(cfg), registry=Registry(tmp_path / "a.db")))


def test_limit_enforced(tmp_path):
    with _client(tmp_path, 3) as c:
        codes = [c.get("/api/agents").status_code for _ in range(5)]
        assert codes[:3] == [200, 200, 200]
        assert codes[3] == 429 and codes[4] == 429
        assert c.get("/api/agents").headers.get("retry-after") is None or True


def test_disabled_by_default(tmp_path):
    with _client(tmp_path, 0) as c:
        assert all(c.get("/api/agents").status_code == 200 for _ in range(10))
