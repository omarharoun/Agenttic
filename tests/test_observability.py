"""Health/readiness probes, request ids, and Prometheus-style metrics."""

from fastapi.testclient import TestClient

from agenttic.registry.sqlite_store import Registry
from agenttic.server import metrics
from agenttic.server.app import create_app

CONFIG = """\
models: {agent_default: a, judge_strong: j, judge_light: l}
harness: {timeout_seconds: 10, max_parallel: 5, transport_retries: 1, max_steps: 10}
scoring: {calibration_threshold: 0.8}
live: {sample_rate: 0.05, drift_threshold: 0.15, drift_window_runs: 50}
paths: {registry_db: %(db)s, review_dir: %(r)s, calibration_dir: %(c)s}
"""


def _client(tmp_path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text(CONFIG % {"db": tmp_path / "a.db", "r": tmp_path / "r",
                             "c": tmp_path / "c"})
    return TestClient(create_app(str(cfg), registry=Registry(tmp_path / "a.db")))


def test_health_and_ready(tmp_path):
    with _client(tmp_path) as c:
        assert c.get("/health").json() == {"status": "ok"}
        r = c.get("/ready")
        assert r.status_code == 200 and r.json()["status"] == "ready"


def test_request_id_header(tmp_path):
    with _client(tmp_path) as c:
        r = c.get("/api/agents")
        assert r.headers.get("x-request-id")
        # inbound id is echoed
        r2 = c.get("/api/agents", headers={"X-Request-ID": "abc123"})
        assert r2.headers["x-request-id"] == "abc123"


def test_metrics_endpoint_counts_requests(tmp_path):
    metrics.reset()
    with _client(tmp_path) as c:
        c.get("/api/agents")
        c.get("/api/agents")
        body = c.get("/metrics").text
    assert "agenttic_http_requests_total" in body
    assert 'method="GET"' in body
    assert "agenttic_http_request_duration_seconds_count" in body


def test_unhandled_error_returns_clean_envelope(tmp_path):
    # force an internal error in a real endpoint; assert no traceback leaks
    from agenttic.server.app import create_app
    cfg = tmp_path / "config.yaml"
    cfg.write_text(CONFIG % {"db": tmp_path / "a.db", "r": tmp_path / "r",
                             "c": tmp_path / "c"})
    app = create_app(str(cfg), registry=Registry(tmp_path / "a.db"))
    with TestClient(app, raise_server_exceptions=False) as c:
        def boom():
            raise RuntimeError("kaboom secret detail")
        c.app.state.store.list_agents = boom   # default-tenant store the route uses
        r = c.get("/api/agents")
        assert r.status_code == 500
        body = r.json()
        assert body["error"] == "internal server error"
        assert "kaboom" not in str(body)        # internals not leaked
        assert body["request_id"]               # correlation id present


class TestMetricsRegistry:
    def test_counter_and_summary_render(self):
        metrics.reset()
        metrics.inc_counter("agenttic_runs_total", {"status": "completed"})
        metrics.inc_counter("agenttic_runs_total", {"status": "completed"})
        metrics.record_cost(0.05)
        out = metrics.render()
        assert 'agenttic_runs_total{status="completed"} 2.0' in out
        assert "agenttic_llm_cost_usd_total 0.05" in out
