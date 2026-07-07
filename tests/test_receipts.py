"""T32.3 — signed action receipts + delegation chain (SPEC-2 M16)."""

from __future__ import annotations

import tempfile

import pytest

from ascore.certification.dossier import assemble
from ascore.config import load_config
from ascore.enforce.gateway import EnforcementGateway, compute_policy_hash
from ascore.passport.issuer import PassportIssuer
from ascore.passport.keys import PassportKeyManager, generate_key
from ascore.passport.receipts import ReceiptError, ReceiptIssuer, verify_chain
from ascore.registry.sqlite_store import Registry
from ascore.schema.certification import (
    Attestation,
    CertificationProfile,
    TierDecision,
)
from ascore.schema.enforcement import Decision, EnforcementPolicy, Rule

CFG = load_config("config.yaml")


def _setup():
    reg = Registry(db_path=tempfile.mktemp(suffix=".db"))
    km = PassportKeyManager(CFG, private_key=generate_key())
    assemble(reg, agent_id="a", agent_config_hash="h",
             profile=CertificationProfile(profile_id="p", required_domains=["tool_use"]),
             tier_decision=TierDecision(tier="B", evidence_refs=["e"]),
             coverage=[], attestation=Attestation(mode="self_attested",
                                                  tenant="default"))
    passport = PassportIssuer(reg, CFG, km).issue("a")
    pol = EnforcementPolicy(policy_id="pol", agent_id="a", rules=[
        Rule(rule_id="allow", lane="lane1", action="allow",
             matcher={"tools": ["http.get", "shell.exec"]})])
    pol.content_hash = compute_policy_hash(pol)
    reg.save_policy(pol)
    gw = EnforcementGateway(reg, CFG)
    return reg, km, passport, gw, ReceiptIssuer(reg, CFG, km)


def test_receipt_binds_a_logged_allow_decision():
    reg, km, passport, gw, ri = _setup()
    s = gw.start_session("a")
    d = gw.evaluate_tool_call(s.session_id, "http.get", {"q": 1})
    r = ri.issue_receipt(passport, s.session_id, d, input_data={"q": 1},
                         output_data={"ok": True})
    assert r.decision_id == d.decision_id
    assert r.input_sha256 and r.output_sha256
    # the receipt is itself an event
    receipts = [e for e in reg.list_enforcement_events(s.session_id)
                if e["kind"] == "receipt"]
    assert len(receipts) == 1
    assert ri.verify_receipt(r)["valid"]


def test_denied_or_unlogged_cannot_produce_receipts():
    reg, km, passport, gw, ri = _setup()
    s = gw.start_session("a")
    # a deny decision → no receipt
    deny = Decision(decision_id="dx", session_id=s.session_id, agent_id="a",
                    phase="tool_call", action="deny", lane="lane1",
                    tool_name="shell.exec")
    with pytest.raises(ReceiptError):
        ri.issue_receipt(passport, s.session_id, deny)
    # an allow decision that was never logged (fabricated) → no receipt
    ghost = Decision(decision_id="ghost", session_id=s.session_id, agent_id="a",
                     phase="tool_call", action="allow", lane="lane1",
                     tool_name="http.get")
    with pytest.raises(ReceiptError):
        ri.issue_receipt(passport, s.session_id, ghost)


def test_no_payload_by_default_but_opt_in_content_redacted():
    reg, km, passport, gw, ri = _setup()
    s = gw.start_session("a")
    d = gw.evaluate_tool_call(s.session_id, "http.get", {})
    # default: no content
    ri.issue_receipt(passport, s.session_id, d, input_data={"secret": "sk-XYZ"})
    ev = [e for e in reg.list_enforcement_events(s.session_id)
          if e["kind"] == "receipt"][-1]
    assert "content" not in ev["detail"]

    # opt-in content is redaction-checked (secrets scrubbed)
    d2 = gw.evaluate_tool_call(s.session_id, "http.get", {})
    ri.issue_receipt(passport, s.session_id, d2,
                     input_data={"body": "token sk-live9F3AK2Qp7secret"},
                     include_content=True)
    ev2 = [e for e in reg.list_enforcement_events(s.session_id)
           if e["kind"] == "receipt"][-1]
    assert "content" in ev2["detail"]
    import json
    assert "sk-live9F3AK2Qp7secret" not in json.dumps(ev2["detail"]["content"])


def test_two_level_chain_resolves_to_principal():
    reg, km, passport, gw, ri = _setup()
    s = gw.start_session("a")
    d1 = gw.evaluate_tool_call(s.session_id, "http.get", {"root": 1})
    parent = ri.issue_receipt(passport, s.session_id, d1)
    d2 = gw.evaluate_tool_call(s.session_id, "http.get", {"child": 1})
    child = ri.issue_receipt(passport, s.session_id, d2,
                             parent_receipt_id=parent.receipt_id)
    chain = verify_chain(reg, child.receipt_id, km)
    assert chain["resolved"]
    assert len(chain["hops"]) == 2
    assert all(h["signature_valid"] for h in chain["hops"])
    # every hop carries its policy hash; resolves to the passport principal
    assert all(h["policy_hash"] for h in chain["hops"])
    assert chain["principal"]["passport_id"] == passport.passport_id


def test_broken_hop_is_named():
    reg, km, passport, gw, ri = _setup()
    s = gw.start_session("a")
    d = gw.evaluate_tool_call(s.session_id, "http.get", {})
    child = ri.issue_receipt(passport, s.session_id, d,
                             parent_receipt_id="rcpt-missing")
    chain = verify_chain(reg, child.receipt_id, km)
    assert not chain["resolved"]
    assert any("broken hop" in p for p in chain["problems"])
    assert "rcpt-missing" in " ".join(chain["problems"])
