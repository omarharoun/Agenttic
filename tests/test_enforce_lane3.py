"""T26.5 — lane-3 async judge + approvals (SPEC-2 M13)."""

from __future__ import annotations

import tempfile
from datetime import datetime, timedelta, timezone

from agenttic.config import load_config
from agenttic.enforce.approvals import ApprovalManager
from agenttic.enforce.async_judge import AsyncJudge
from agenttic.enforce.gateway import EnforcementGateway, Session, compute_policy_hash
from agenttic.registry.sqlite_store import Registry
from agenttic.schema.enforcement import Decision, EnforcementPolicy, Rule

CFG = load_config("config.yaml")
NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _reg():
    return Registry(db_path=tempfile.mktemp(suffix=".db"))


def _policy(agent="a", sampling=0.25):
    p = EnforcementPolicy(policy_id="p", agent_id=agent, rules=[
        Rule(rule_id="allow", lane="lane1", action="allow",
             matcher={"tools": ["http.get"]}),
        Rule(rule_id="lane3-sampling", lane="lane3", action="allow",
             matcher={"sampling": sampling})])
    p.content_hash = compute_policy_hash(p)
    return p


def test_sampling_within_tolerance_seeded_stream():
    reg = _reg()
    policy = _policy(sampling=0.25)
    judge = AsyncJudge(reg, CFG, seed=1234)
    hits = sum(1 for _ in range(1000) if judge.should_sample(policy))
    # ~250 expected; a seeded 1k stream should land within a few % of the rate
    assert 200 <= hits <= 300, hits


def test_approval_round_trip_with_identity():
    reg = _reg()
    am = ApprovalManager(reg, CFG)
    sess = Session(session_id="s", agent_id="a",
                   policy=EnforcementPolicy(policy_id="p", agent_id="a"))
    dec = Decision(decision_id="d", session_id="s", agent_id="a",
                   phase="tool_call", action="require_approval", lane="lane1",
                   tool_name="fs.write", action_class="write")
    ar = am.park(sess, dec, {"path": "/x"}, now=NOW)
    assert ar.state == "pending"
    resolved = am.resolve(ar.approval_id, True, "pat:alice@x", now=NOW)
    assert resolved.state == "approved"
    assert resolved.resolver_identity == "pat:alice@x"
    assert am.effective_action(resolved) == "allow"
    # the resolution is logged with the identity
    events = reg.list_enforcement_events("s")
    approved = [e for e in events if (e.get("detail") or {}).get("event") == "approved"]
    assert approved and approved[0]["detail"]["resolver_identity"] == "pat:alice@x"


def test_expiry_denies_write():
    reg = _reg()
    am = ApprovalManager(reg, CFG)
    sess = Session(session_id="s", agent_id="a",
                   policy=EnforcementPolicy(policy_id="p", agent_id="a"))
    dec = Decision(decision_id="d", session_id="s", agent_id="a",
                   phase="tool_call", action="require_approval", lane="lane1",
                   tool_name="fs.write", action_class="write")
    ar = am.park(sess, dec, {}, now=NOW)
    expired = am.expire(ar.approval_id, now=NOW + timedelta(hours=2))
    assert expired.state == "expired"
    assert am.effective_action(expired) == "deny"  # fail-closed on write


def test_expiry_allows_read():
    reg = _reg()
    am = ApprovalManager(reg, CFG)
    sess = Session(session_id="s", agent_id="a",
                   policy=EnforcementPolicy(policy_id="p", agent_id="a"))
    dec = Decision(decision_id="d", session_id="s", agent_id="a",
                   phase="tool_call", action="require_approval", lane="lane1",
                   tool_name="http.get", action_class="read")
    ar = am.park(sess, dec, {}, now=NOW)
    expired = am.expire(ar.approval_id, now=NOW + timedelta(hours=2))
    assert am.effective_action(expired) == "allow"  # fail-open on read


def test_malicious_verdict_opens_incident_and_terminates():
    reg = _reg()
    policy = _policy(sampling=1.0)
    reg.save_policy(policy)

    def verdict(_d):
        return {"malicious": True, "severity": "S2", "terminate": True,
                "revoke": False, "rationale": "exfiltration attempt"}

    judge = AsyncJudge(reg, CFG, verdict_fn=verdict, seed=1)
    gw = EnforcementGateway(reg, CFG, async_enqueue=judge.enqueue)
    s = gw.start_session("a")
    gw.evaluate_tool_call(s.session_id, "http.get", {})
    # verdict terminated the session
    assert s.active is False
    # an incident was opened at the verdict severity
    incidents = reg.list_incidents("a")
    assert incidents and incidents[0]["severity"] == "S2"
    # every downstream event carries a verdict ref
    events = reg.list_enforcement_events(s.session_id)
    judge_events = [e for e in events if e["kind"] == "judge"]
    assert judge_events
    term = [e for e in events
            if e.get("action") == "terminate_session"]
    assert term and "verdict_ref" in (term[0].get("detail") or {})


def test_judge_is_never_inline():
    # The gateway's inline path returns a Decision immediately; the judge runs
    # only via the async enqueue hook (out of band).
    reg = _reg()
    policy = _policy(sampling=1.0)
    reg.save_policy(policy)
    calls = {"n": 0}

    def counting_verdict(_d):
        calls["n"] += 1
        return {"malicious": False}

    judge = AsyncJudge(reg, CFG, verdict_fn=counting_verdict, seed=1)
    # gateway WITHOUT the async hook → judge never runs inline
    gw = EnforcementGateway(reg, CFG, async_enqueue=None)
    s = gw.start_session("a")
    gw.evaluate_tool_call(s.session_id, "http.get", {})
    assert calls["n"] == 0
