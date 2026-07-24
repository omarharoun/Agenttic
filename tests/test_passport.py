"""T31.4 — passport contracts (SPEC-2 M16). Real Ed25519 test keys."""

from __future__ import annotations

import tempfile
from datetime import datetime, timedelta, timezone

import pytest

from agenttic.certification.dossier import assemble
from agenttic.config import load_config
from agenttic.passport.issuer import PassportIssuer, verify_passport_object
from agenttic.passport.keys import (
    PassportKeyManager,
    generate_key,
    private_seed_b64,
    verify_payload,
)
from agenttic.registry.sqlite_store import Registry
from agenttic.schema.certification import (
    Attestation,
    CertificationProfile,
    TierDecision,
)

CFG = load_config("config.yaml")


def _setup(agent="a"):
    reg = Registry(db_path=tempfile.mktemp(suffix=".db"))
    assemble(reg, agent_id=agent, agent_config_hash="h",
             profile=CertificationProfile(profile_id="p", required_domains=["tool_use"]),
             tier_decision=TierDecision(tier="B", evidence_refs=["e"],
                                        caps_applied=["provisional_judge"]),
             coverage=[], attestation=Attestation(mode="self_attested",
                                                  tenant="default"))
    km = PassportKeyManager(CFG, private_key=generate_key())
    return reg, km, PassportIssuer(reg, CFG, km)


def test_passport_verifies_against_jwks():
    reg, km, issuer = _setup()
    p = issuer.issue("a")
    # verify directly against the published JWKS public key (offline)
    jwk = km.jwks()["keys"][0]
    import base64
    raw = base64.urlsafe_b64decode(jwk["x"] + "=" * (-len(jwk["x"]) % 4))
    pub_b64 = base64.b64encode(raw).decode()
    assert verify_payload(pub_b64, p.signing_input(), p.signature)
    assert issuer.verify(p.passport_id)["valid"]


def test_tampered_claim_is_named():
    reg, km, issuer = _setup()
    p = issuer.issue("a")
    p.claims.tier = "A"  # tamper: upgrade the tier
    v = verify_passport_object(p, km, status="active")
    assert not v["valid"]
    assert not v["signature_valid"]
    assert "signature" in v["reason"]


def test_status_beats_signature_on_revoked():
    reg, km, issuer = _setup()
    p = issuer.issue("a")
    assert issuer.verify(p.passport_id)["valid"]
    issuer.revoke(p.passport_id, reason="compromised")
    v = issuer.verify(p.passport_id)
    # signature is STILL cryptographically valid...
    assert v["signature_valid"] is True
    # ...but status is checked separately and a revoked passport is rejected
    assert v["status"] == "revoked"
    assert v["valid"] is False
    assert v["reason"] == "passport revoked"


def test_expired_passport_rejected():
    reg, km, issuer = _setup()
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    p = issuer.issue("a", now=now)
    v = verify_passport_object(p, km, status="active",
                              now=now + timedelta(hours=10_000))
    assert v["expired"] and not v["valid"]


def test_rotation_overlap_keeps_old_passport_verifiable():
    reg, km, issuer = _setup()
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    p = issuer.issue("a", now=now)  # signed by key v1
    old_kid = km.key_id()
    km.rotate(now=now)              # new signing key; old stays in overlap
    assert km.key_id() != old_kid
    # the old passport still verifies during the overlap window
    v = issuer.verify(p.passport_id, now=now + timedelta(days=1))
    assert v["valid"], v


def test_private_key_never_lands_in_registry_logs_or_exports():
    reg, km, issuer = _setup()
    seed = private_seed_b64(km._priv)
    issuer.issue("a")
    # dump every persisted payload across the DB and assert the seed is absent
    from sqlalchemy import text
    with reg.engine.connect() as conn:
        tables = [r[0] for r in conn.execute(text(
            "SELECT name FROM sqlite_master WHERE type='table'"))]
        for t in tables:
            for row in conn.execute(text(f"SELECT * FROM {t}")):
                blob = " ".join(str(v) for v in row)
                assert seed not in blob, f"private seed leaked into {t}"
    # JWKS + status view carry only public material
    import json
    assert seed not in json.dumps(km.jwks())
    assert seed not in json.dumps(reg.list_passports("a"))


def test_passport_signing_fails_closed_in_production(monkeypatch):
    # No configured key + production => refuse to start (mirror cert signing).
    monkeypatch.delenv("AGENTTIC_PASSPORT_SIGNING_KEY", raising=False)
    monkeypatch.setenv("AGENTTIC_ENV", "production")
    with pytest.raises(RuntimeError, match="fail closed"):
        PassportKeyManager(CFG)


def test_passport_signing_ephemeral_in_dev_and_reports_degraded(monkeypatch):
    # No configured key outside production => ephemeral key, flagged so the
    # health probe surfaces DEGRADED rather than a silent operational.
    monkeypatch.delenv("AGENTTIC_PASSPORT_SIGNING_KEY", raising=False)
    monkeypatch.delenv("AGENTTIC_ENV", raising=False)
    monkeypatch.delenv("AGENTTIC_ENVIRONMENT", raising=False)
    km = PassportKeyManager(CFG)
    assert km.is_ephemeral() is True
    assert km.jwks()["keys"]  # still publishes a JWKS

    from agenttic.server.health import DEGRADED, run_probe
    from agenttic.server.health import _probe_passport

    class _App:
        class state:
            passport_keys = km
    h = run_probe("passport_signing", _probe_passport, _App)
    assert h.status == DEGRADED
    assert "ephemeral" in h.detail


def test_passport_signing_configured_key_is_not_ephemeral(monkeypatch):
    seed = private_seed_b64(generate_key())
    monkeypatch.setenv("AGENTTIC_PASSPORT_SIGNING_KEY", seed)
    monkeypatch.setenv("AGENTTIC_ENV", "production")  # configured => prod is fine
    km = PassportKeyManager(CFG)
    assert km.is_ephemeral() is False
