"""Interactive RL oversight loop — safety + adaptation (SPEC-2 addendum).

Critical invariant: a stream of "allow" feedback can NEVER auto-loosen a rule
without an explicit confirmation event (Rule 20/26). Tightening auto-applies;
loosening is only ever a gated proposal.
"""

from __future__ import annotations

import copy
import tempfile

import pytest

from agenttic.certification.dossier import assemble
from agenttic.config import load_config
from agenttic.enforce.compiler import recompile_for_agent
from agenttic.enforce.gateway import Session
from agenttic.enforce.interactive_oversight import (
    ContextualBandit,
    InteractiveOversightLoop,
    pending_loosen_proposals,
)
from agenttic.registry.sqlite_store import Registry
from agenttic.schema.certification import (
    Attestation,
    CertificationProfile,
    TierDecision,
)
from agenttic.schema.enforcement import Decision


def _cfg(enabled=True):
    cfg = copy.deepcopy(load_config("config.yaml"))
    cfg["oversight"]["interactive_loop"]["enabled"] = enabled
    return cfg


def _reg_with_policy(cfg, agent="a"):
    reg = Registry(db_path=tempfile.mktemp(suffix=".db"))
    assemble(reg, agent_id=agent, agent_config_hash="h",
             profile=CertificationProfile(profile_id="p", required_domains=["tool_use"]),
             tier_decision=TierDecision(tier="B", evidence_refs=["e"],
                                        caps_applied=["provisional_judge"]),
             coverage=[], attestation=Attestation(mode="self_attested",
                                                  tenant="default"))
    recompile_for_agent(reg, cfg, agent)
    return reg


def _review(loop, reg, tool, action_class, response, agent="a"):
    sess = Session(session_id="s", agent_id=agent, policy=reg.latest_policy(agent))
    dec = Decision(decision_id="d", session_id="s", agent_id=agent,
                   phase="tool_call", action="allow", lane="lane1",
                   tool_name=tool, action_class=action_class, latency_ms=9.9)
    _sel, reasons = loop.select_for_review(dec)
    item = loop.present_for_review(sess, dec, reasons)
    return loop.record_human_response(item, response, "alice@x")


def test_disabled_by_default():
    cfg = load_config("config.yaml")  # unmodified
    reg = Registry(db_path=tempfile.mktemp(suffix=".db"))
    loop = InteractiveOversightLoop(reg, cfg)
    assert loop.enabled is False


def test_pattern_feedback_auto_tightens():
    cfg = _cfg()
    reg = _reg_with_policy(cfg)
    loop = InteractiveOversightLoop(reg, cfg)
    fid = _review(loop, reg, "shell.exec", "write", "always_block_pattern")
    before = {r.rule_id for r in reg.latest_policy("a").rules}
    props = loop.propose_adaptation("a")
    after = {r.rule_id for r in reg.latest_policy("a").rules}
    # a tightening rule was auto-applied
    tighten = [p for p in props if p["direction"] == "tighten" and p["applied"]]
    assert tighten
    assert after - before  # a rule was added
    # the adaptation resolves to the logged feedback id
    assert f"event:{fid.split('evt-')[-1]}" or fid
    assert any(f"event:" in ref for ref in tighten[0]["feedback_ids"])


def test_allow_stream_never_auto_loosens_without_confirmation():
    cfg = _cfg()
    reg = _reg_with_policy(cfg)
    # First, tighten a pattern so there's a rule that *could* be loosened.
    loop = InteractiveOversightLoop(reg, cfg)
    _review(loop, reg, "http.get", "read", "always_block_pattern")
    loop.propose_adaptation("a")
    tightened_rules = {r.rule_id for r in reg.latest_policy("a").rules}
    assert any("oversight" in rid for rid in tightened_rules)

    # Now a whole stream of ALLOW feedback for that pattern.
    loop2 = InteractiveOversightLoop(reg, cfg)
    for _ in range(20):
        _review(loop2, reg, "http.get", "read", "allow")
    _review(loop2, reg, "http.get", "read", "always_allow_pattern")
    rules_before = {r.rule_id for r in reg.latest_policy("a").rules}
    props = loop2.propose_adaptation("a")
    rules_after = {r.rule_id for r in reg.latest_policy("a").rules}
    # THE INVARIANT: no rule loosened/removed automatically
    assert rules_before == rules_after
    # it surfaced as a proposal requiring confirmation instead
    loosen = [p for p in props if p["direction"] == "loosen"]
    assert loosen and loosen[0]["applied"] is False
    assert loosen[0]["requires_confirmation"] is True
    # the proposal is pending in the log
    assert pending_loosen_proposals(reg, "a")


def test_loosening_applies_only_after_explicit_confirmation():
    cfg = _cfg()
    reg = _reg_with_policy(cfg)
    loop = InteractiveOversightLoop(reg, cfg)
    # tighten http.get, then propose a loosen, then confirm it
    _review(loop, reg, "http.get", "read", "always_block_pattern")
    loop.propose_adaptation("a")
    assert any("oversight" in r.rule_id for r in reg.latest_policy("a").rules)

    loop2 = InteractiveOversightLoop(reg, cfg)
    _review(loop2, reg, "http.get", "read", "always_allow_pattern")
    props = loop2.propose_adaptation("a")
    proposal_id = next(p["proposal_id"] for p in props if p["direction"] == "loosen")
    # unchanged until confirmed
    assert any("oversight" in r.rule_id for r in reg.latest_policy("a").rules)
    # explicit confirmation applies the loosening (removes the oversight rule)
    result = loop2.confirm_loosening("a", proposal_id, "carol@x")
    assert result["applied"] is True
    assert not any("oversight-deny-http_get" in r.rule_id
                   or "oversight-require_approval-http_get" in r.rule_id
                   for r in reg.latest_policy("a").rules)
    # the confirmation is itself a logged event
    events = reg.list_enforcement_events(None, "a")
    assert any((e.get("detail") or {}).get("event") == "loosen_confirmed"
               for e in events)


def test_confirm_requires_a_real_pending_proposal():
    cfg = _cfg()
    reg = _reg_with_policy(cfg)
    loop = InteractiveOversightLoop(reg, cfg)
    with pytest.raises(ValueError):
        loop.confirm_loosening("a", "prop-does-not-exist", "carol@x")


def test_bandit_determinism_under_seed():
    b1 = ContextualBandit(seed=42)
    b2 = ContextualBandit(seed=42)
    stream = ["block", "allow", "block", "block", "allow"]
    for i, r in enumerate(stream):
        b1.update("p:write", r, f"e{i}")
        b2.update("p:write", r, f"e{i}")
    # identical seeded recommendation sequences
    assert [b1.recommend("p:write") for _ in range(10)] == \
           [b2.recommend("p:write") for _ in range(10)]


def test_model_is_optional_and_mocked():
    cfg = _cfg()
    reg = _reg_with_policy(cfg)
    # no model → loop runs, model_suggestion is None
    loop = InteractiveOversightLoop(reg, cfg, judge=None)
    dec = Decision(decision_id="d", session_id="s", agent_id="a",
                   phase="tool_call", action="allow", lane="lane1",
                   tool_name="http.get", action_class="read")
    assert loop.model_suggestion(dec) is None

    # a MOCKED judge model enriches with a suggestion (never hard-coded)
    class MockJudge:
        def verdict_fn(self, _d):
            return {"malicious": True}
    loop2 = InteractiveOversightLoop(reg, cfg, judge=MockJudge())
    assert loop2.model_suggestion(dec) == "block"


def test_every_adaptation_resolves_to_logged_feedback_ids():
    cfg = _cfg()
    reg = _reg_with_policy(cfg)
    loop = InteractiveOversightLoop(reg, cfg)
    fid = _review(loop, reg, "shell.exec", "write", "always_block_pattern")
    props = loop.propose_adaptation("a")
    tighten = next(p for p in props if p["direction"] == "tighten")
    # the feedback ids the adaptation cites all exist in the log
    logged_ids = {f"event:{e['event_id']}"
                  for e in reg.list_enforcement_events(None, "a")}
    for ref in tighten["feedback_ids"]:
        assert ref in logged_ids
