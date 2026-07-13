"""T34.3 — underwriter/procurement risk feed + webhooks (SPEC-2 M17)."""

from __future__ import annotations

import json
import tempfile

from agenttic.certification.dossier import assemble
from agenttic.config import load_config
from agenttic.enforce.compiler import recompile_for_agent
from agenttic.feeds.risk_api import FEED_VERSION, risk_feed
from agenttic.feeds.webhooks import (
    deliver_pending,
    enqueue_webhook,
    pending_webhooks,
)
from agenttic.passport.issuer import PassportIssuer
from agenttic.passport.keys import PassportKeyManager, generate_key
from agenttic.registry.sqlite_store import Registry
from agenttic.schema.certification import (
    Attestation,
    CertificationProfile,
    TierDecision,
)

CFG = load_config("config.yaml")

_GOLDEN_KEYS = {
    "feed_version", "agent_id", "certification", "posture", "stage",
    "incidents", "enforcement", "canaries", "oversight", "passports",
}


def _reg(agent="a"):
    reg = Registry(db_path=tempfile.mktemp(suffix=".db"))
    assemble(reg, agent_id=agent, agent_config_hash="h",
             profile=CertificationProfile(profile_id="p", required_domains=["tool_use"]),
             tier_decision=TierDecision(tier="B", evidence_refs=["e"],
                                        caps_applied=["provisional_judge"]),
             coverage=[], attestation=Attestation(mode="self_attested",
                                                  tenant="default"))
    recompile_for_agent(reg, CFG, agent)
    return reg


def test_feed_matches_golden_schema():
    reg = _reg()
    feed = risk_feed(reg, CFG, "a")
    assert set(feed) == _GOLDEN_KEYS
    assert feed["feed_version"] == FEED_VERSION
    assert feed["certification"]["tier"] == "B"
    assert feed["posture"]["approvals"] == "write"
    assert "sla_adherence" in feed["incidents"]


def test_feed_leaks_no_traces_or_pii():
    reg = _reg()
    blob = json.dumps(risk_feed(reg, CFG, "a"))
    # aggregate-only: no trace payloads, no raw args, no rationales
    for forbidden in ("trace_id", "final_output", "judge_rationale",
                      "input_sha", "payload", "args"):
        assert forbidden not in blob


def test_feed_passport_validity_agrees_with_sdk():
    from agenttic.verifier import RevokedError, verify_passport
    reg = _reg()
    km = PassportKeyManager(CFG, private_key=generate_key())
    issuer = PassportIssuer(reg, CFG, km)
    p = issuer.issue("a")
    pd, jwks = json.loads(p.model_dump_json()), km.jwks()

    feed = risk_feed(reg, CFG, "a")
    assert feed["passports"] == {"total": 1, "active": 1, "revoked": 0}
    # independent SDK agrees it's valid
    assert verify_passport(pd, jwks)["tier"] == "B"

    # revoke → feed flips AND the SDK rejects it (they agree)
    issuer.revoke(p.passport_id, reason="x")
    feed2 = risk_feed(reg, CFG, "a")
    assert feed2["passports"] == {"total": 1, "active": 0, "revoked": 1}
    status = reg.passport_status(p.passport_id)
    import pytest
    with pytest.raises(RevokedError):
        verify_passport(pd, jwks, status=status)


def test_tenancy_invisibility():
    with tempfile.TemporaryDirectory() as tmp:
        reg_a = Registry(db_path=f"{tmp}/db.sqlite", tenant="tenant-a")
        reg_b = Registry(db_path=f"{tmp}/db.sqlite", tenant="tenant-b")
        assemble(reg_a, agent_id="secret-agent", agent_config_hash="h",
                 profile=CertificationProfile(profile_id="p", required_domains=["tool_use"]),
                 tier_decision=TierDecision(tier="A", evidence_refs=["e"]),
                 coverage=[], attestation=Attestation(mode="self_attested",
                                                      tenant="tenant-a"))
        # tenant B's feed for the same agent id sees nothing
        feed_b = risk_feed(reg_b, CFG, "secret-agent")
        assert feed_b["certification"] is None
        assert feed_b["passports"]["total"] == 0


def test_webhook_golden_and_ssrf():
    reg = _reg()
    enqueue_webhook(reg, CFG, "revocation", "a", {"reason": "x"})
    pend = pending_webhooks(reg, "a")
    assert pend and pend[0]["detail"]["webhook_event"] == "revocation"
    # a private/SSRF URL is blocked; a public one delivers
    cfg = dict(CFG)
    cfg["feeds"] = {**CFG.get("feeds", {}),
                    "webhook_urls": ["http://169.254.169.254/hook",
                                     "https://underwriter.example/hook"]}
    sent = []
    res = deliver_pending(reg, cfg, sender=lambda u, p: sent.append(u) or "ok",
                          agent_id="a")
    statuses = {r["url"]: r["status"] for r in res}
    assert statuses["http://169.254.169.254/hook"].startswith("blocked")
    assert statuses["https://underwriter.example/hook"] == "ok"
    assert "http://169.254.169.254/hook" not in sent
