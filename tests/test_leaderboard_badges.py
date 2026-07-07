"""T18.3 — leaderboard certification badges + certified filter (SPEC-2 M7)."""

from __future__ import annotations

from ascore.certification.dossier import assemble
from ascore.registry.sqlite_store import Registry
from ascore.schema.certification import (
    Attestation,
    CertificationProfile,
    TierDecision,
)
from ascore.server.routes.leaderboard import _attach_certification_badges


def test_badges_only_on_certified_rows(tmp_path):
    reg = Registry(tmp_path / "a.db")
    prof = CertificationProfile(profile_id="p", required_domains=["tool_use"])
    assemble(reg, agent_id="certified-agent", agent_config_hash="h", profile=prof,
             tier_decision=TierDecision(tier="B", evidence_refs=["e"],
                                        caps_applied=["provisional_judge"]),
             coverage=[], attestation=Attestation(mode="self_attested",
                                                  tenant="default"))
    rows = [{"agent_id": "certified-agent"}, {"agent_id": "uncertified-agent"}]
    _attach_certification_badges(reg, rows)
    by_id = {r["agent_id"]: r for r in rows}
    # certified row carries the badge
    badge = by_id["certified-agent"]["certification"]
    assert badge["tier"] == "B"
    assert badge["attestation"] == "self_attested"
    assert badge["status"] == "current"
    # uncertified row shows nothing (None), never a fabricated badge
    assert by_id["uncertified-agent"]["certification"] is None
