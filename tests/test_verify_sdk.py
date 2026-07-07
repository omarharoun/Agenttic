"""T33.4 — verifier SDK parity + distinct named errors (SPEC-2 M17)."""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ascore.certification.dossier import assemble
from ascore.config import load_config
from ascore.passport.issuer import PassportIssuer
from ascore.passport.keys import PassportKeyManager, generate_key
from ascore.registry.sqlite_store import Registry
from ascore.schema.certification import (
    Attestation,
    CertificationProfile,
    TierDecision,
)
from ascore.verify import (
    ExpiredError,
    RevokedError,
    TamperedError,
    UnknownKeyError,
    verify_passport,
)

CFG = load_config("config.yaml")
FIXTURE = Path("tests/fixtures/passport/golden.json")
JS_RUNNER = Path("src/ascore/verify/js/verify_golden.js")


def _passport():
    reg = Registry(db_path=tempfile.mktemp(suffix=".db"))
    km = PassportKeyManager(CFG, private_key=generate_key())
    assemble(reg, agent_id="a", agent_config_hash="h",
             profile=CertificationProfile(profile_id="p", required_domains=["tool_use"]),
             tier_decision=TierDecision(tier="B", evidence_refs=["e"]),
             coverage=[], attestation=Attestation(mode="self_attested",
                                                  tenant="default"))
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    p = PassportIssuer(reg, CFG, km).issue("a", now=now)
    return json.loads(p.model_dump_json()), km.jwks(), now


def test_valid_passport_returns_claims():
    pd, jwks, now = _passport()
    claims = verify_passport(pd, jwks, now=now)
    assert claims["tier"] == "B"


def test_tampered_raises_tampered_error():
    pd, jwks, now = _passport()
    pd["claims"]["tier"] = "A"
    with pytest.raises(TamperedError):
        verify_passport(pd, jwks, now=now)


def test_unknown_key_raises_unknown_key_error():
    pd, jwks, now = _passport()
    with pytest.raises(UnknownKeyError):
        verify_passport(pd, {"keys": []}, now=now)


def test_expired_raises_expired_error():
    pd, jwks, now = _passport()
    with pytest.raises(ExpiredError):
        verify_passport(pd, jwks, now=now + timedelta(days=365))


def test_revoked_raises_revoked_error():
    pd, jwks, now = _passport()
    with pytest.raises(RevokedError):
        verify_passport(pd, jwks, now=now, status="revoked")


def test_python_verifies_golden_fixture():
    f = json.loads(FIXTURE.read_text())
    now = datetime.fromisoformat(f["now"])
    claims = verify_passport(f["passport"], f["jwks"], now=now)
    assert claims["tier"] == f["expected"]["tier"]


@pytest.mark.skipif(shutil.which("node") is None, reason="node not available")
def test_js_sdk_parity_against_golden_fixture():
    # cross-implementation parity: the JS SDK verifies the Python-signed fixture
    result = subprocess.run(
        ["node", str(JS_RUNNER), str(FIXTURE)],
        capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "parity OK" in result.stdout


def test_relying_party_accepts_valid_rejects_revoked():
    from ascore.verify.header import HEADER_NAME, encode_passport_header
    from examples.relying_party import app, configure
    pd, jwks, now = _passport()
    # the example server checks expiry against real 'now'; issue a fresh passport
    reg = Registry(db_path=tempfile.mktemp(suffix=".db"))
    km = PassportKeyManager(CFG, private_key=generate_key())
    assemble(reg, agent_id="a", agent_config_hash="h",
             profile=CertificationProfile(profile_id="p", required_domains=["tool_use"]),
             tier_decision=TierDecision(tier="B", evidence_refs=["e"]),
             coverage=[], attestation=Attestation(mode="self_attested",
                                                  tenant="default"))
    p = PassportIssuer(reg, CFG, km).issue("a")
    hdr = {HEADER_NAME: encode_passport_header(json.loads(p.model_dump_json()))}
    c = TestClient(app)
    configure(km.jwks(), status_fetcher=lambda u: {"status": "active"})
    assert c.get("/protected", headers=hdr).status_code == 200
    configure(km.jwks(), status_fetcher=lambda u: {"status": "revoked"})
    assert c.get("/protected", headers=hdr).status_code == 403
    assert c.get("/protected").status_code == 401
