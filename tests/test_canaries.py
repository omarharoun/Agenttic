"""T29.3 — honeypot canaries (SPEC-2 M15)."""

from __future__ import annotations

import tempfile
from datetime import datetime, timedelta, timezone

from ascore.config import load_config
from ascore.enforce.canaries import CanaryManager
from ascore.enforce.gateway import EnforcementGateway, compute_policy_hash
from ascore.registry.sqlite_store import Registry
from ascore.schema.enforcement import EnforcementPolicy, Rule
from ascore.schema.scorecard import CriterionScore, RunScore, Scorecard

CFG = load_config("config.yaml")


def _setup():
    reg = Registry(db_path=tempfile.mktemp(suffix=".db"))
    p = EnforcementPolicy(policy_id="p", agent_id="a", rules=[
        Rule(rule_id="allow", lane="lane1", action="allow",
             matcher={"tools": ["http.get", "http.post"]})])
    p.content_hash = compute_policy_hash(p)
    reg.save_policy(p)
    cm = CanaryManager(reg, CFG)
    cm.install("a", decoy_tools=["admin.deleteAll"],
               canary_credentials=["sk-canary-SECRET"],
               tripwire_domains=["evil.example"])
    gw = EnforcementGateway(reg, CFG)
    return reg, cm, gw


def test_decoy_call_denies_and_opens_s1():
    reg, cm, gw = _setup()
    s = gw.start_session("a")
    d = gw.evaluate_tool_call(s.session_id, "admin.deleteAll", {})
    assert d.action == "deny"
    incidents = reg.list_incidents("a")
    assert incidents and incidents[0]["severity"] == "S1"
    assert incidents[0]["origin"] == "canary"


def test_planted_credential_reuse_trips_with_both_refs():
    reg, cm, gw = _setup()
    s = gw.start_session("a")
    d = gw.evaluate_tool_call(s.session_id, "http.post",
                              {"body": "auth: sk-canary-SECRET"})
    assert d.action == "deny"
    # evidence carries BOTH the canary id and the call ref
    assert any("canary:" in e for e in d.evidence)
    assert any("toolcall:" in e for e in d.evidence)
    trips = cm.trip_history("a")
    assert trips and trips[0]["detail"]["trip_kind"] == "canary_credential"
    assert "call_ref" in trips[0]["detail"]


def test_zero_false_positive_on_legit_use():
    reg, cm, gw = _setup()
    s = gw.start_session("a")
    # legitimate calls never trip
    assert gw.evaluate_tool_call(s.session_id, "http.get", {}).action == "allow"
    assert gw.evaluate_tool_call(s.session_id, "http.post",
                                 {"body": "hello"}).action == "allow"
    assert cm.trip_history("a") == []


def test_canaries_never_appear_in_scorecards():
    reg, cm, gw = _setup()
    # a normal scorecard for the agent — must not reference any canary marker
    reg.save_scorecard(Scorecard(
        scorecard_id="sc1", agent_id="a", suite_id="std-tool-use-v1",
        suite_version=1, rubric_id="r", rubric_version=1,
        run_scores=[RunScore(trace_id="t", test_id="c", passed=True,
                             criterion_scores=[CriterionScore(
                                 criterion_id="tool_selection_accuracy",
                                 score=1.0, scorer="code")])],
        task_success_rate=1.0, mean_cost_usd=0.0, p95_latency_ms=0.0,
        visibility_tier="glass_box"))
    ok, offending = cm.separation_ok("a")
    assert ok, offending


def test_scorecard_separation_detects_leak():
    reg, cm, gw = _setup()
    # a (bad) scorecard that leaked a decoy tool into a rationale
    reg.save_scorecard(Scorecard(
        scorecard_id="sc-bad", agent_id="a", suite_id="s", suite_version=1,
        rubric_id="r", rubric_version=1,
        run_scores=[RunScore(trace_id="t", test_id="c", passed=True,
                             criterion_scores=[CriterionScore(
                                 criterion_id="x", score=1.0, scorer="judge",
                                 judge_rationale="called admin.deleteAll")])],
        task_success_rate=1.0, mean_cost_usd=0.0, p95_latency_ms=0.0,
        visibility_tier="glass_box"))
    ok, offending = cm.separation_ok("a")
    assert not ok and offending


def test_rotation_preserves_trip_history():
    reg, cm, gw = _setup()
    s = gw.start_session("a")
    gw.evaluate_tool_call(s.session_id, "admin.deleteAll", {})  # trip on v1
    assert len(cm.trip_history("a")) == 1
    old = reg.active_canary_set("a")
    new = cm.rotate("a")
    assert new.version == old.version + 1
    assert new.canary_credentials != old.canary_credentials
    # trip history survives rotation (append-only)
    assert len(cm.trip_history("a")) == 1


def test_needs_rotation_after_rotation_days():
    reg, cm, gw = _setup()
    now = datetime.now(timezone.utc)
    assert not cm.needs_rotation("a", now=now)
    assert cm.needs_rotation("a", now=now + timedelta(days=31))
