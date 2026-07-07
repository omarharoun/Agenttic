"""T25.4 — policy compiler keystone (SPEC-2 M12)."""

from __future__ import annotations

import copy
import tempfile

import pytest

from ascore.certification.dossier import assemble
from ascore.config import load_config
from ascore.enforce.compiler import (
    OverrideError,
    apply_overrides,
    compile_policy,
    recompile_for_agent,
)
from ascore.registry.sqlite_store import Registry
from ascore.schema.certification import (
    Attestation,
    CertificationProfile,
    Dossier,
    TierDecision,
)


@pytest.fixture()
def cfg():
    return load_config("config.yaml")


def _dossier(tier, caps=None):
    return Dossier(
        dossier_id="d", agent_id="ref", agent_config_hash="h", profile_id="p",
        profile_version=1,
        tier_decision=TierDecision(tier=tier, evidence_refs=["e"],
                                   caps_applied=caps or []),
        attestation=Attestation(mode="self_attested", tenant="t"))


def test_byte_identical_determinism(cfg):
    d = _dossier("B", ["provisional_judge", "elicitation_gap:tool_use"])
    p1 = compile_policy(d, None, [], cfg)
    p2 = compile_policy(d, None, [], cfg)
    # the content hash IS the policy identity (excludes id handle + timestamp)
    assert p1.content_hash == p2.content_hash
    assert p1.hashable_content() == p2.hashable_content()
    assert p1.policy_id == p2.policy_id


def test_four_fixture_dossiers_four_documented_postures(cfg):
    postures = {}
    for name, d in {
        "A": _dossier("A"),
        "B": _dossier("B", ["provisional_judge"]),
        "C": _dossier("C"),
        "undoc": _dossier("B", ["undocumented_covered_agent"]),
    }.items():
        p = compile_policy(d, None, [], cfg)
        postures[name] = {r.rule_id: r.origin for r in p.rules}
    # four distinct postures
    hashes = {name: tuple(sorted(v)) for name, v in postures.items()}
    assert len(set(hashes.values())) == 4
    # A: light sampling only, no approvals, no deny
    assert "serve-deny" not in postures["A"] and "approvals-write" not in postures["A"]
    # C and undoc both deny; every rule names its origin
    assert "serve-deny" in postures["C"]
    assert postures["undoc"]["serve-deny"] == "cap:undocumented_covered_agent"
    # B: write approvals + elevated sampling, no deny
    assert "approvals-write" in postures["B"] and "serve-deny" not in postures["B"]


def test_loosening_rejected(cfg):
    base = compile_policy(_dossier("B", ["provisional_judge"]), None, [], cfg)
    with pytest.raises(OverrideError):
        apply_overrides(base, {"approvals": "none"})
    with pytest.raises(OverrideError):
        apply_overrides(base, {"lane3_sampling": 0.0})
    # tightening is allowed
    tighter = apply_overrides(base, {"lane3_sampling": 1.0, "serve": "deny"})
    assert any(r.rule_id == "serve-deny" for r in tighter.rules)


def test_config_hash_bump_recompiles_e2e(cfg):
    with tempfile.TemporaryDirectory() as tmp:
        reg = Registry(db_path=f"{tmp}/t.db")
        prof = CertificationProfile(profile_id="p", required_domains=["tool_use"])
        assemble(reg, agent_id="ref", agent_config_hash="h", profile=prof,
                 tier_decision=TierDecision(tier="B", evidence_refs=["e"],
                                            caps_applied=["provisional_judge"]),
                 coverage=[], attestation=Attestation(mode="self_attested",
                                                      tenant="default"))
        p1 = recompile_for_agent(reg, cfg, "ref")
        # bump the compiler config (raise Tier-B sampling) → recompile differs
        cfg2 = copy.deepcopy(cfg)
        cfg2["enforcement"]["compiler"]["tier_posture"]["B"]["lane3_sampling"] = 0.99
        p2 = recompile_for_agent(reg, cfg2, "ref")
        assert p1.content_hash != p2.content_hash
        # the newest persisted policy is the recompiled one
        assert reg.latest_policy("ref").content_hash == p2.content_hash
