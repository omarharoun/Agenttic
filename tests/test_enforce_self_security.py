"""T27.3 — enforcement self-security (SPEC-2 M13)."""

from __future__ import annotations

import json
import tempfile

from ascore.certification.dossier import assemble
from ascore.config import load_config
from ascore.enforce.compiler import recompile_for_agent
from ascore.enforce.export import export_json
from ascore.enforce.gateway import EnforcementGateway, compute_policy_hash
from ascore.enforce.self_security import (
    assert_no_self_exemption,
    verify_policy_provenance,
)
from ascore.registry.sqlite_store import Registry
from ascore.schema.certification import (
    Attestation,
    CertificationProfile,
    TierDecision,
)
from ascore.schema.enforcement import EnforcementPolicy, Rule

CFG = load_config("config.yaml")


def test_policy_chains_to_a_real_dossier():
    reg = Registry(db_path=tempfile.mktemp(suffix=".db"))
    prof = CertificationProfile(profile_id="p", required_domains=["tool_use"])
    assemble(reg, agent_id="ref", agent_config_hash="h", profile=prof,
             tier_decision=TierDecision(tier="B", evidence_refs=["e"],
                                        caps_applied=["provisional_judge"]),
             coverage=[], attestation=Attestation(mode="self_attested",
                                                  tenant="default"))
    policy = recompile_for_agent(reg, CFG, "ref")
    ok, problems = verify_policy_provenance(reg, policy)
    assert ok, problems
    # a free-floating policy (no dossier ref) fails provenance
    floating = EnforcementPolicy(policy_id="x", agent_id="ref",
                                 rules=[Rule(rule_id="r", lane="lane1", action="allow")])
    ok2, problems2 = verify_policy_provenance(reg, floating)
    assert not ok2 and problems2


def test_secrets_redacted_in_exported_events():
    reg = Registry(db_path=tempfile.mktemp(suffix=".db"))
    p = EnforcementPolicy(policy_id="p", agent_id="a", rules=[
        Rule(rule_id="allow", lane="lane1", action="allow",
             matcher={"action_class": "read"})])
    p.content_hash = compute_policy_hash(p)
    reg.save_policy(p)
    gw = EnforcementGateway(reg, CFG)
    s = gw.start_session("a")
    # outbound args carry a secret → lane 2 redacts; the preserved original is
    # stored, but the EXPORT redacts everything.
    gw.evaluate_tool_call(s.session_id, "http.get",
                          {"body": "token is sk-live9F3AK2Qp7secret"})
    exported = export_json(reg, s.session_id)
    assert "sk-live9F3AK2Qp7secret" not in exported
    assert "[REDACTED]" in exported


def test_no_self_exemption():
    reg = Registry(db_path=tempfile.mktemp(suffix=".db"))
    p = EnforcementPolicy(policy_id="p", agent_id="a", rules=[
        Rule(rule_id="deny", lane="lane1", action="deny",
             matcher={"tool": "shell.exec"})])
    p.content_hash = compute_policy_hash(p)
    reg.save_policy(p)
    gw = EnforcementGateway(reg, CFG)
    s = gw.start_session("a")
    gw.evaluate_tool_call(s.session_id, "shell.exec", {})
    # every decision is backed by a logged event — no bypass
    assert assert_no_self_exemption(reg, s.session_id)


def test_tenancy_isolation_of_events():
    with tempfile.TemporaryDirectory() as tmp:
        # two tenants over the same DB file share an engine only under Postgres;
        # here each tenant is its own registry scope
        reg_a = Registry(db_path=f"{tmp}/a.db", tenant="tenant-a")
        reg_b = Registry(db_path=f"{tmp}/a.db", tenant="tenant-b")
        p = EnforcementPolicy(policy_id="p", agent_id="x", rules=[
            Rule(rule_id="allow", lane="lane1", action="allow",
                 matcher={"tools": ["http.get"]})])
        p.content_hash = compute_policy_hash(p)
        reg_a.save_policy(p)
        gw = EnforcementGateway(reg_a, CFG)
        s = gw.start_session("x")
        gw.evaluate_tool_call(s.session_id, "http.get", {})
        # tenant-b cannot see tenant-a's enforcement events
        assert reg_a.list_enforcement_events(s.session_id)
        assert reg_b.list_enforcement_events(s.session_id) == []
