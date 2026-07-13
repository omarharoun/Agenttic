"""T11.5 — certification + incident schema contracts (SPEC-2 M4)."""

from __future__ import annotations

import tempfile
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from agenttic.certification.hashing import (
    canonical_json,
    compute_dossier_hash,
    sha256_hex,
)
from agenttic.registry.sqlite_store import DuplicateVersionError, Registry
from agenttic.schema.certification import (
    Attestation,
    CertificationProfile,
    Dossier,
    DomainCoverage,
    SuiteRef,
    TierDecision,
)
from agenttic.schema.incident import Incident


def _dossier(**kw) -> Dossier:
    base = dict(
        dossier_id="d1",
        agent_id="a1",
        agent_config_hash="cfg",
        profile_id="p1",
        profile_version=1,
        tier_decision=TierDecision(tier="B", evidence_refs=["sc:1"]),
        attestation=Attestation(mode="self_attested", tenant="default"),
    )
    base.update(kw)
    return Dossier(**base)


# -- round-trips ------------------------------------------------------------ #

def test_profile_round_trip():
    p = CertificationProfile(
        profile_id="p1",
        suite_refs=[SuiteRef(suite_id="s", version=2)],
        required_domains=["tool_use", "cbrn_proxy"],
        thresholds={"tool_use_score": 0.7},
    )
    back = CertificationProfile.model_validate_json(p.model_dump_json())
    assert back == p


def test_dossier_round_trip():
    d = _dossier(coverage=[DomainCoverage(domain="tool_use", status="assessed_real")])
    back = Dossier.model_validate_json(d.model_dump_json())
    assert back == d


def test_incident_round_trip():
    inc = Incident(incident_id="i1", agent_id="a1", severity="S2")
    back = Incident.model_validate_json(inc.model_dump_json())
    assert back == inc


# -- evidence is mandatory -------------------------------------------------- #

def test_empty_evidence_rejected():
    with pytest.raises(ValueError):
        TierDecision(tier="A", evidence_refs=[])


def test_unknown_domain_rejected():
    with pytest.raises(ValueError):
        CertificationProfile(profile_id="p", required_domains=["not_a_domain"])
    with pytest.raises(ValueError):
        DomainCoverage(domain="nope", status="not_assessed")


# -- hash stability --------------------------------------------------------- #

def test_hash_stable_across_key_order():
    a = {"b": 1, "a": {"z": 2, "y": 3}, "c": [1, 2, 3]}
    b = {"c": [1, 2, 3], "a": {"y": 3, "z": 2}, "b": 1}
    assert sha256_hex(a) == sha256_hex(b)


def test_dossier_hash_excludes_content_sha256():
    d = _dossier()
    h = compute_dossier_hash(d)
    d.content_sha256 = h
    # recompute must ignore content_sha256 -> identical
    assert compute_dossier_hash(d) == h


def test_canonical_json_utf8_not_ascii_escaped():
    assert canonical_json({"n": "café"}) == '{"n":"café"}'


def test_hash_changes_when_content_changes():
    d1 = _dossier()
    d2 = _dossier(tier_decision=TierDecision(tier="C", evidence_refs=["sc:1"]))
    assert compute_dossier_hash(d1) != compute_dossier_hash(d2)


# -- SLA clock across tz / DST ---------------------------------------------- #

def test_sla_due_is_absolute_across_dst():
    # US spring-forward 2025-03-09 02:00 EST -> EDT.
    opened = datetime(2025, 3, 9, 0, 0, tzinfo=ZoneInfo("America/New_York"))
    inc = Incident(incident_id="i", agent_id="a", severity="S2", opened_at=opened)
    # 72 absolute hours later regardless of the DST shift.
    assert inc.sla_due() == opened + timedelta(hours=72)


def test_sla_hours_per_severity():
    for sev, hrs in (("S1", 72), ("S2", 72), ("S3", 168), ("S4", 336)):
        inc = Incident(incident_id="i", agent_id="a", severity=sev)
        assert inc.sla_due() - inc.opened_at == timedelta(hours=hrs)


def test_naive_datetime_coerced_to_utc():
    inc = Incident(
        incident_id="i", agent_id="a", severity="S1",
        opened_at=datetime(2025, 1, 1, 0, 0),  # naive
    )
    assert inc.opened_at.tzinfo is not None


def test_overdue_flag():
    opened = datetime(2025, 1, 1, tzinfo=timezone.utc)
    inc = Incident(incident_id="i", agent_id="a", severity="S1", opened_at=opened)
    assert inc.is_overdue(now=opened + timedelta(hours=73))
    assert not inc.is_overdue(now=opened + timedelta(hours=71))


def test_closed_incident_never_overdue():
    opened = datetime(2025, 1, 1, tzinfo=timezone.utc)
    inc = Incident(incident_id="i", agent_id="a", severity="S1",
                   state="closed", opened_at=opened)
    assert not inc.is_overdue(now=opened + timedelta(days=999))


# -- registry append-only --------------------------------------------------- #

def test_registry_dossier_is_immutable():
    with tempfile.TemporaryDirectory() as tmp:
        r = Registry(db_path=f"{tmp}/t.db")
        d = _dossier()
        d.content_sha256 = compute_dossier_hash(d)
        r.save_dossier(d)
        with pytest.raises(DuplicateVersionError):
            r.save_dossier(d)
        assert r.get_dossier("d1") == d


def test_registry_profile_versioned():
    with tempfile.TemporaryDirectory() as tmp:
        r = Registry(db_path=f"{tmp}/t.db")
        p = CertificationProfile(profile_id="p1", required_domains=["tool_use"])
        r.save_profile(p)
        with pytest.raises(DuplicateVersionError):
            r.save_profile(p)
        assert r.get_profile("p1").profile_id == "p1"


def test_incident_events_are_append_only_state():
    with tempfile.TemporaryDirectory() as tmp:
        r = Registry(db_path=f"{tmp}/t.db")
        inc = Incident(incident_id="i1", agent_id="a1", severity="S3", origin="drift")
        r.save_incident(inc)
        r.append_incident_event("i1", "a1", "triaged", actor="ops")
        r.append_incident_event("i1", "a1", "closed", actor="ops")
        events = [e["event_type"] for e in r.list_incident_events("i1")]
        assert events == ["opened", "triaged", "closed"]
