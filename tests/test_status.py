"""Service-status health probing (live /status board).

Covers the honesty contract: all-up rolls to ``operational``; one down rolls to
``down`` and the component reflects it; an unprobeable component is ``unknown``
and NEVER silently green. Component probes are mocked so no real DB / Redis /
crypto is exercised."""

from __future__ import annotations

from fastapi.testclient import TestClient

from ascore.registry.sqlite_store import Registry
from ascore.server.app import create_app
from ascore.server.health import (
    DEGRADED,
    DOWN,
    OPERATIONAL,
    UNKNOWN,
    HealthChecker,
    ProbeError,
    rollup,
    run_probe,
)

CONFIG = """\
models: {agent_default: a, judge_strong: j, judge_light: l}
harness: {timeout_seconds: 10, max_parallel: 5, transport_retries: 1, max_steps: 10}
scoring: {calibration_threshold: 0.8}
live: {sample_rate: 0.05, drift_threshold: 0.15, drift_window_runs: 50}
paths: {registry_db: %(db)s, review_dir: %(r)s, calibration_dir: %(c)s}
auth: {required: true, token: testtoken}
security: {login_max_attempts: 5, login_lockout_seconds: 900}
"""


# -- unit: probe running + rollup -------------------------------------------- #

def _ok(_app):
    return "fine"


def _down(_app):
    raise ProbeError(DOWN, "unreachable")


def _degraded(_app):
    raise ProbeError(DEGRADED, "slow")


def _boom(_app):
    raise RuntimeError("kaboom")  # unexpected → must become UNKNOWN, not green


def test_probe_success_is_operational():
    h = run_probe("x", _ok, app=None)
    assert h.status == OPERATIONAL
    assert h.detail == "fine"
    assert h.latency_ms is not None and h.latency_ms >= 0
    assert h.last_checked  # timestamped


def test_probe_raising_probeerror_reports_declared_state():
    assert run_probe("x", _down, app=None).status == DOWN
    assert run_probe("x", _degraded, app=None).status == DEGRADED


def test_unexpected_exception_is_unknown_never_operational():
    h = run_probe("x", _boom, app=None)
    assert h.status == UNKNOWN
    assert h.status != OPERATIONAL


def test_rollup_all_up_is_operational():
    checker = HealthChecker(probes=[("a", _ok), ("b", _ok)])
    snap = checker.snapshot(app=None)
    assert snap["status"] == OPERATIONAL
    assert {c["name"] for c in snap["components"]} == {"a", "b"}


def test_rollup_one_down_makes_overall_down():
    checker = HealthChecker(probes=[("a", _ok), ("b", _down)])
    snap = checker.snapshot(app=None)
    assert snap["status"] == DOWN
    states = {c["name"]: c["status"] for c in snap["components"]}
    assert states["a"] == OPERATIONAL
    assert states["b"] == DOWN


def test_rollup_prefers_down_over_degraded_over_unknown():
    assert rollup(_mk([DOWN, DEGRADED, UNKNOWN, OPERATIONAL])) == DOWN
    assert rollup(_mk([DEGRADED, UNKNOWN, OPERATIONAL])) == DEGRADED
    assert rollup(_mk([UNKNOWN, OPERATIONAL])) == UNKNOWN
    assert rollup(_mk([OPERATIONAL, OPERATIONAL])) == OPERATIONAL


def test_unprobeable_component_is_unknown_and_overall_not_green():
    # A component whose probe cannot determine state must never let the board
    # read all-green.
    checker = HealthChecker(probes=[("a", _ok), ("unprobeable", _boom)])
    snap = checker.snapshot(app=None)
    states = {c["name"]: c["status"] for c in snap["components"]}
    assert states["unprobeable"] == UNKNOWN
    assert snap["status"] != OPERATIONAL
    assert snap["status"] == UNKNOWN


def test_snapshot_is_cached_within_ttl():
    calls = {"n": 0}

    def counting(_app):
        calls["n"] += 1
        return "ok"

    # deterministic clock. Reads: ctor(1), then each fresh snapshot reads twice
    # (start + uptime), each cached snapshot reads once (start only).
    ticks = iter([0.0,          # ctor: started_monotonic
                  0.0, 0.0,     # snapshot #1 (fresh): now, uptime
                  1.0,          # snapshot #2 (cached, within ttl): now
                  100.0, 100.0])  # snapshot #3 (fresh, past ttl): now, uptime
    checker = HealthChecker(probes=[("a", counting)], cache_ttl=5.0,
                            clock=lambda: next(ticks))
    checker.snapshot(app=None)          # t=0 first probe
    checker.snapshot(app=None)          # t=1 within ttl → cached
    assert calls["n"] == 1
    checker.snapshot(app=None)          # t=100 past ttl → re-probe
    assert calls["n"] == 2


def test_snapshot_reports_version_and_uptime():
    checker = HealthChecker(probes=[("a", _ok)], version="9.9.9")
    snap = checker.snapshot(app=None)
    assert snap["version"] == "9.9.9"
    assert "uptime_seconds" in snap and snap["uptime_seconds"] >= 0
    assert snap["started_at"] and snap["checked_at"]


# -- integration: the public endpoint ---------------------------------------- #

def _client(tmp_path):
    reg = Registry(tmp_path / "a.db")
    cfg = tmp_path / "config.yaml"
    cfg.write_text(CONFIG % {"db": tmp_path / "a.db", "r": tmp_path / "r",
                             "c": tmp_path / "c"})
    return TestClient(create_app(str(cfg), registry=reg)), reg


def test_status_endpoint_is_public_and_probes_real_components(tmp_path):
    client, _ = _client(tmp_path)
    with client as c:
        r = c.get("/api/status")  # NO auth header — public rollup
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] in {OPERATIONAL, DEGRADED, DOWN, UNKNOWN}
        names = {comp["name"] for comp in body["components"]}
        assert {"api", "database", "worker", "certification_engine",
                "otel_ingest", "passport_signing", "scanner"} <= names
        # real registry + wired components → these probe operational
        states = {comp["name"]: comp["status"] for comp in body["components"]}
        assert states["database"] == OPERATIONAL
        assert states["api"] == OPERATIONAL
        assert body["version"]  # running version stamped


def test_status_leaks_no_internal_fields(tmp_path):
    client, _ = _client(tmp_path)
    with client as c:
        body = c.get("/api/status").json()
    # aggregate only — every component exposes exactly the public contract
    allowed = {"name", "status", "latency_ms", "detail", "last_checked"}
    for comp in body["components"]:
        assert set(comp) == allowed


def test_healthz_is_liveness(tmp_path):
    client, _ = _client(tmp_path)
    with client as c:
        r = c.get("/healthz")
        assert r.status_code == 200
        assert r.json() == {"status": "ok"}


def _mk(states):
    from ascore.server.health import ComponentHealth
    return [ComponentHealth(name=f"c{i}", status=s, latency_ms=0.0,
                            detail="", last_checked="t")
            for i, s in enumerate(states)]
