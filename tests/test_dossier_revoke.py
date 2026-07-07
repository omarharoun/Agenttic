"""T17.4 — append-only revocation; no manual-promotion path (SPEC-2 M7)."""

from __future__ import annotations

import inspect
import tempfile

import pytest

from ascore.certification import dossier as dossier_mod
from ascore.certification import staleness
from ascore.certification.dossier import assemble, revoke
from ascore.registry.sqlite_store import Registry
from ascore.schema.certification import (
    Attestation,
    CertificationProfile,
    TierDecision,
)


def _dossier(reg):
    prof = CertificationProfile(profile_id="p", required_domains=["tool_use"])
    return assemble(reg, agent_id="a1", agent_config_hash="h", profile=prof,
                    tier_decision=TierDecision(tier="B", evidence_refs=["x"]),
                    coverage=[], attestation=Attestation(mode="self_attested",
                                                         tenant="default"))


def test_revoke_is_sticky_and_readable_forever():
    with tempfile.TemporaryDirectory() as tmp:
        reg = Registry(db_path=f"{tmp}/t.db")
        d = _dossier(reg)
        assert staleness.status(reg, d) == "current"
        revoke(reg, d.dossier_id, reason="safety regression")
        # status is revoked, and stays revoked
        assert staleness.status(reg, d) == "revoked"
        # still readable forever
        again = reg.get_dossier(d.dossier_id)
        assert again.dossier_id == d.dossier_id
        assert staleness.status(reg, again) == "revoked"
        # the reason is recorded on the append-only event log
        events = reg.list_dossier_events(d.dossier_id)
        assert any(e["event_type"] == "revoked"
                   and e["reason"] == "safety regression" for e in events)


def test_revoke_requires_a_reason():
    with tempfile.TemporaryDirectory() as tmp:
        reg = Registry(db_path=f"{tmp}/t.db")
        d = _dossier(reg)
        with pytest.raises(ValueError):
            revoke(reg, d.dossier_id, reason="")


def test_no_manual_promotion_code_path():
    # There is deliberately no function to promote a tier or un-revoke a dossier.
    dossier_names = {n for n, _ in inspect.getmembers(dossier_mod)}
    for forbidden in ("promote", "unrevoke", "un_revoke", "set_status",
                      "grant_tier", "set_tier"):
        assert forbidden not in dossier_names
    # status() only ever returns computed values, never accepts a status to set
    sig = inspect.signature(staleness.status)
    assert "status" not in sig.parameters  # no injectable status
    assert set(inspect.signature(staleness.status).parameters) >= {"reg", "dossier"}
