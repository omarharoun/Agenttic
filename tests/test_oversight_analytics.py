"""T30.3 — oversight analytics: metrics + static render (SPEC-2 M15)."""

from __future__ import annotations

import copy
import tempfile
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from agenttic.config import load_config
from agenttic.enforce.approvals import ApprovalManager
from agenttic.enforce.gateway import Session
from agenttic.oversight.analytics import approval_analytics
from agenttic.registry.sqlite_store import Registry
from agenttic.schema.enforcement import Decision, EnforcementPolicy
from agenttic.server.app import create_app

CFG = load_config("config.yaml")
NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _stream(reg, agent, n, approve, latency_s):
    am = ApprovalManager(reg, CFG)
    sess = Session(session_id="s", agent_id=agent,
                   policy=EnforcementPolicy(policy_id="p", agent_id=agent))
    for i in range(n):
        dec = Decision(decision_id=f"d{i}", session_id="s", agent_id=agent,
                       phase="tool_call", action="require_approval", lane="lane1",
                       tool_name="fs.write", action_class="write")
        ar = am.park(sess, dec, {"i": i}, now=NOW)
        am.resolve(ar.approval_id,
                   approve if isinstance(approve, bool) else approve(i),
                   "pat:bob", now=NOW + timedelta(seconds=latency_s))


def test_empty_registry_renders_zeros():
    reg = Registry(db_path=tempfile.mktemp(suffix=".db"))
    m = approval_analytics(reg, CFG)
    assert m["n_resolved"] == 0
    assert m["approval_rate"] == 0.0
    assert m["rubber_stamp"] is False
    assert m["latency"]["mean"] is None


def test_rubber_stamp_stream_flagged():
    reg = Registry(db_path=tempfile.mktemp(suffix=".db"))
    _stream(reg, "a", 6, True, latency_s=1)  # fast + all-approve
    m = approval_analytics(reg, CFG, "a")
    assert m["approval_rate"] == 1.0
    assert m["reflexive_rate"] == 1.0
    assert m["rubber_stamp"] is True


def test_careful_reviewer_not_flagged():
    reg = Registry(db_path=tempfile.mktemp(suffix=".db"))
    _stream(reg, "b", 6, lambda i: i % 2 == 0, latency_s=30)  # slow + 50% approve
    m = approval_analytics(reg, CFG, "b")
    assert m["rubber_stamp"] is False
    assert m["latency"]["mean"] == 30.0


def test_analytics_endpoint_static(tmp_path):
    reg = Registry(tmp_path / "a.db")
    _stream(reg, "a", 5, True, latency_s=1)
    cfg = tmp_path / "config.yaml"
    cfg.write_text(f"""\
models: {{agent_default: a, judge_strong: j, judge_light: l}}
harness: {{timeout_seconds: 10, max_parallel: 5, transport_retries: 1, max_steps: 10}}
scoring: {{calibration_threshold: 0.8}}
live: {{sample_rate: 0.05, drift_threshold: 0.15, drift_window_runs: 50}}
paths: {{registry_db: {tmp_path / 'a.db'}, review_dir: {tmp_path / 'r'}, calibration_dir: {tmp_path / 'c'}}}
auth: {{required: true, token: t}}
security: {{login_max_attempts: 5, login_lockout_seconds: 900}}
oversight: {{reflexive_under_seconds: 3, rubber_stamp_threshold: 0.6, posture_toggle: false}}
""")
    with TestClient(create_app(str(cfg), registry=reg)) as c:
        r = c.get("/api/oversight/analytics?agent_id=a",
                  headers={"Authorization": "Bearer t"})
        assert r.status_code == 200
        assert r.json()["rubber_stamp"] is True


def test_rubber_stamp_drives_posture_only_under_toggle():
    from agenttic.enforce.compiler import compile_policy, posture_summary
    from agenttic.schema.certification import Attestation, Dossier, TierDecision
    d = Dossier(dossier_id="d", agent_id="a", agent_config_hash="h",
                profile_id="p", profile_version=1,
                tier_decision=TierDecision(tier="A", evidence_refs=["e"]),
                attestation=Attestation(mode="self_attested", tenant="t"))
    off = posture_summary(compile_policy(d, None, [], CFG,
                                         oversight_rubber_stamp=True))
    cfg_on = copy.deepcopy(CFG)
    cfg_on["oversight"]["posture_toggle"] = True
    on = posture_summary(compile_policy(d, None, [], cfg_on,
                                        oversight_rubber_stamp=True))
    assert off["approvals"] == "none"      # indicator only
    assert on["approvals"] == "write"      # tightened under toggle
