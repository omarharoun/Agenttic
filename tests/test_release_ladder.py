"""T28.4 — staged release ladder + promotion (SPEC-2 M14)."""

from __future__ import annotations

import tempfile
from datetime import datetime, timedelta, timezone

import pytest

from ascore.certification.dossier import assemble
from ascore.config import load_config
from ascore.enforce.compiler import recompile_for_agent
from ascore.enforce.gateway import EnforcementGateway, compute_policy_hash
from ascore.live.incidents import open_manual
from ascore.registry.sqlite_store import Registry
from ascore.release.ladder import agent_stage, stage_gate
from ascore.release.promotion import (
    PromotionRefused,
    auto_demote_on_incident,
    evaluate_promotion,
    grant_promotion,
)
from ascore.schema.certification import (
    Attestation,
    CertificationProfile,
    TierDecision,
)
from ascore.schema.enforcement import EnforcementPolicy, Rule
from ascore.schema.release import Cohort

CFG = load_config("config.yaml")
NOW = datetime(2026, 7, 7, tzinfo=timezone.utc)


def _reg():
    reg = Registry(db_path=tempfile.mktemp(suffix=".db"))
    assemble(reg, agent_id="a", agent_config_hash="h",
             profile=CertificationProfile(profile_id="p", required_domains=["tool_use"]),
             tier_decision=TierDecision(tier="B", evidence_refs=["e"],
                                        caps_applied=["provisional_judge"]),
             coverage=[], attestation=Attestation(mode="self_attested",
                                                  tenant="default"))
    return reg


def _policy(reg):
    p = EnforcementPolicy(policy_id="p", agent_id="a", rules=[
        Rule(rule_id="allow", lane="lane1", action="allow",
             matcher={"tools": ["http.get"]})])
    p.content_hash = compute_policy_hash(p)
    reg.save_policy(p)


def test_stage_gate_denial_records_cohort_and_stage():
    reg = _reg()
    _policy(reg)
    reg.save_cohort(Cohort(cohort_id="ga-cb", agent_id="a", stage="ga"))
    gw = EnforcementGateway(reg, CFG)
    s = gw.start_session("a")
    # agent is at internal (no promotions) → a GA-cohort caller is above stage
    d = gw.evaluate_tool_call(s.session_id, "http.get", {}, caller_cohort="ga-cb")
    assert d.action == "deny"
    assert any("stage_gate" in e for e in d.evidence)
    # the event names cohort + stages
    events = reg.list_enforcement_events(s.session_id)
    gated = [e for e in events if (e.get("detail") or {}).get("origin") == "stage_gate"]
    assert gated
    assert "ga-cb" in gated[0]["detail"]["evidence"][0]


def test_blocked_vs_granted_promotion():
    reg = _reg()
    # too soon → blocked, criterion named
    ev = evaluate_promotion(reg, CFG, "a", "vetted", now=NOW)
    assert not ev.eligible
    assert any("observation_hours" in u for u in ev.unmet)
    # after enough observation → eligible
    later = NOW + timedelta(hours=1000)
    ev2 = evaluate_promotion(reg, CFG, "a", "vetted", now=later)
    assert ev2.eligible, ev2.unmet


def test_forced_promotion_impossible():
    reg = _reg()
    with pytest.raises(PromotionRefused):
        grant_promotion(reg, CFG, "a", "cohort-x", "vetted",
                        granted_by="pat:alice", now=NOW)  # too soon
    # skipping a stage is also refused
    later = NOW + timedelta(hours=1000)
    with pytest.raises(PromotionRefused):
        grant_promotion(reg, CFG, "a", "cohort-x", "ga",  # internal→ga skips
                        granted_by="pat:alice", now=later)


def test_grant_appends_record_and_promotes():
    reg = _reg()
    _policy(reg)
    later = NOW + timedelta(hours=1000)
    rec = grant_promotion(reg, CFG, "a", "cohort-x", "vetted",
                          granted_by="pat:alice", now=later)
    assert rec.kind == "promotion"
    assert agent_stage(reg, "a") == "vetted"
    assert reg.list_promotion_records("a")


def test_auto_demotion_recompiles():
    reg = _reg()
    recompile_for_agent(reg, CFG, "a")  # base policy
    later = NOW + timedelta(hours=1000)
    grant_promotion(reg, CFG, "a", "cohort-x", "vetted",
                    granted_by="pat:alice", now=later)
    grant_promotion(reg, CFG, "a", "cohort-x", "limited",
                    granted_by="pat:alice", now=later + timedelta(hours=1000))
    assert agent_stage(reg, "a") == "limited"
    policy_before = reg.latest_policy("a").content_hash
    # an open S2 → immediate auto-demotion to internal + recompile
    open_manual(reg, agent_id="a", severity="S2", title="regression")
    dm = auto_demote_on_incident(reg, CFG, "a", now=later)
    assert dm is not None and dm.kind == "demotion"
    assert agent_stage(reg, "a") == "internal"
    # the policy was recompiled (internal stage is looser than limited → different)
    assert reg.latest_policy("a").content_hash != policy_before


def test_no_demotion_without_critical_incident():
    reg = _reg()
    later = NOW + timedelta(hours=1000)
    _policy(reg)
    grant_promotion(reg, CFG, "a", "cohort-x", "vetted",
                    granted_by="pat:alice", now=later)
    # only an S3 open → no auto-demotion
    open_manual(reg, agent_id="a", severity="S3", title="minor")
    assert auto_demote_on_incident(reg, CFG, "a", now=later) is None
    assert agent_stage(reg, "a") == "vetted"
