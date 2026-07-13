"""T15.3 — evaluator isolation boundaries (SPEC-2 M6).

An evaluator principal reads only certified-run artifacts (dossiers); the owner's
raw traces return 404. An operator/owner still sees traces normally.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi.testclient import TestClient

from agenttic.registry.sqlite_store import Registry
from agenttic.schema.trace import SCHEMA_VERSION, Span, Trace
from agenttic.server.app import create_app
from agenttic.server.pats import PatStore

NOW = datetime(2026, 6, 21, tzinfo=timezone.utc)

CONFIG = """\
models: {agent_default: a, judge_strong: j, judge_light: l}
harness: {timeout_seconds: 10, max_parallel: 5, transport_retries: 1, max_steps: 10}
scoring: {calibration_threshold: 0.8}
live: {sample_rate: 0.05, drift_threshold: 0.15, drift_window_runs: 50}
paths: {registry_db: %(db)s, review_dir: %(r)s, calibration_dir: %(c)s}
auth: {required: true, token: admintoken}
security: {login_max_attempts: 5, login_lockout_seconds: 900}
"""


def _setup(tmp_path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text(CONFIG % {"db": tmp_path / "a.db", "r": tmp_path / "r",
                             "c": tmp_path / "c"})
    reg = Registry(tmp_path / "a.db")
    # an owner trace exists in the default tenant
    reg.save_trace(Trace(trace_id="trace-owner", agent_id="ref-agent",
                         agent_config_hash="h", test_case_id="c1",
                         visibility="glass_box", final_output="secret owner detail",
                         spans=[Span(span_id="f", kind="final_output",
                                     name="final_output", start_time=NOW,
                                     end_time=NOW, attributes={})],
                         schema_version=SCHEMA_VERSION), mode="batch")
    # an evaluator PAT on the same tenant
    pats = PatStore(reg.engine)
    evtok = pats.create(user_email="eval@x.com", tenant="default",
                        role="evaluator", name="ev")["token"]
    app = create_app(str(cfg), registry=reg)
    return TestClient(app), evtok


def test_evaluator_cannot_read_owner_traces(tmp_path):
    client, evtok = _setup(tmp_path)
    with client:
        ev = {"Authorization": f"Bearer {evtok}"}
        admin = {"Authorization": "Bearer admintoken"}
        # owner/admin sees the trace
        assert client.get("/api/traces/trace-owner", headers=admin).status_code == 200
        # evaluator is isolated → 404 (hidden), both single + list
        assert client.get("/api/traces/trace-owner", headers=ev).status_code == 404
        assert client.get("/api/traces", headers=ev).status_code == 404
