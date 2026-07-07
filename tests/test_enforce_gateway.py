"""T23.5 — enforcement gateway contracts (SPEC-2 M11)."""

from __future__ import annotations

import tempfile

import pytest
from fastapi.testclient import TestClient

from ascore.enforce.gateway import (
    EnforcementGateway,
    PolicyIntegrityError,
    compute_policy_hash,
)
from ascore.registry.sqlite_store import Registry
from ascore.schema.enforcement import (
    Decision,
    EnforcementEvent,
    EnforcementPolicy,
    Rule,
)
from ascore.server.app import create_app


def _policy(agent="a", pid="p1"):
    p = EnforcementPolicy(policy_id=pid, agent_id=agent,
                          rules=[Rule(rule_id="r1", lane="lane1", action="deny",
                                      matcher={"tool": "shell.exec"},
                                      origin="tier_posture:C")])
    p.content_hash = compute_policy_hash(p)
    return p


def test_schema_round_trips():
    p = _policy()
    assert EnforcementPolicy.model_validate_json(p.model_dump_json()) == p
    d = Decision(decision_id="d", session_id="s", agent_id="a",
                 phase="tool_call", action="allow", lane="lane1")
    assert Decision.model_validate_json(d.model_dump_json()) == d
    e = EnforcementEvent(event_id="e", session_id="s", agent_id="a", kind="decision")
    assert EnforcementEvent.model_validate_json(e.model_dump_json()) == e


def test_deterministic_policy_hash():
    p1 = _policy()
    p2 = EnforcementPolicy.model_validate_json(p1.model_dump_json())
    # hash is stable across a serialize/parse round-trip and independent of the
    # created_at timestamp (excluded from hashable content)
    assert compute_policy_hash(p1) == compute_policy_hash(p2) == p1.content_hash


def test_pass_through_logs_every_call_and_result():
    with tempfile.TemporaryDirectory() as tmp:
        reg = Registry(db_path=f"{tmp}/t.db")
        reg.save_policy(_policy())
        gw = EnforcementGateway(reg, {})
        s = gw.start_session("a")
        gw.evaluate_tool_call(s.session_id, "http.get", {})
        gw.evaluate_tool_call(s.session_id, "http.get", {})
        gw.evaluate_tool_result(s.session_id, "http.get", {"ok": True})
        events = reg.list_enforcement_events(s.session_id)
        kinds = [e["kind"] for e in events]
        # 1 policy_load + 3 decisions (nothing enforced without a logged decision)
        assert kinds == ["policy_load", "decision", "decision", "decision"]


def test_tamper_refusal_is_named_and_logged():
    with tempfile.TemporaryDirectory() as tmp:
        reg = Registry(db_path=f"{tmp}/t.db")
        bad = EnforcementPolicy(policy_id="pbad", agent_id="a",
                                rules=[Rule(rule_id="r", lane="lane1", action="allow")],
                                content_hash="deadbeef")
        reg.save_policy(bad)
        gw = EnforcementGateway(reg, {})
        with pytest.raises(PolicyIntegrityError) as exc:
            gw.start_session("a")
        assert "hash verification" in str(exc.value)
        # the refusal itself is an event
        events = reg.list_enforcement_events(agent_id="a")
        assert any(e["kind"] == "refusal" for e in events)


def test_proxy_event_shape_matches_in_process(tmp_path):
    reg = Registry(tmp_path / "a.db")
    reg.save_policy(_policy())
    # in-process decision
    gw = EnforcementGateway(reg, {})
    s = gw.start_session("a")
    inproc = gw.evaluate_tool_call(s.session_id, "http.get", {}).model_dump(mode="json")

    CONFIG = f"""\
models: {{agent_default: a, judge_strong: j, judge_light: l}}
harness: {{timeout_seconds: 10, max_parallel: 5, transport_retries: 1, max_steps: 10}}
scoring: {{calibration_threshold: 0.8}}
live: {{sample_rate: 0.05, drift_threshold: 0.15, drift_window_runs: 50}}
paths: {{registry_db: {tmp_path / 'a.db'}, review_dir: {tmp_path / 'r'}, calibration_dir: {tmp_path / 'c'}}}
auth: {{required: true, token: t}}
security: {{login_max_attempts: 5, login_lockout_seconds: 900}}
"""
    cfg = tmp_path / "config.yaml"
    cfg.write_text(CONFIG)
    with TestClient(create_app(str(cfg), registry=reg)) as c:
        auth = {"Authorization": "Bearer t"}
        sess = c.post("/api/enforce/sessions", headers=auth,
                      json={"agent_id": "a"}).json()
        proxied = c.post("/api/enforce/tool-call", headers=auth,
                         json={"session_id": sess["session_id"],
                               "tool_name": "http.get", "args": {}}).json()
    # same decision fields (ids/latency differ, shape does not)
    assert set(inproc.keys()) == set(proxied.keys())
    assert inproc["action"] == proxied["action"] == "allow"
