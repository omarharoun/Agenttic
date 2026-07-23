"""SPEC-12 Step 54 — attestation acceptance tests (offline, no network)."""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from agenttic.certification.abom import (
    abom_sha256, build_abom, validate_abom)
from agenttic.certification.attest import (
    append_revocation, assert_no_banned_claims, build_manifest, local_key_path,
    local_signing_key, new_revocation_list, public_key_b64, render_certificate,
    sign_manifest, sign_revocation_list, suspend_on_drift, verify_manifest)
from agenttic.schema.attestation import BANNED_CLAIMS, content_hash

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)

SCORECARD = {
    "scorecard_id": "sc-1", "agent_id": "pilot", "task_success_rate": 0.86,
    "per_criterion_means": {"tone": 0.92, "routing": 1.0},
    "n_scored": 40, "visibility_tier": "glass_box",
}


@pytest.fixture(autouse=True)
def _isolated_key(tmp_path, monkeypatch):
    """Never touch the developer's real ~/.config/agenttic key."""
    monkeypatch.setenv("AGENTTIC_ATTEST_KEY_DIR", str(tmp_path / "cfg"))
    yield


def _manifest(**over):
    kw = dict(
        manifest_id="m-1", agent_id="pilot", agent_config_hash="cfg-abc123",
        suite_id="suite-support", suite_version=2,
        rubric_id="rubric-support", rubric_version=3,
        scorecard=SCORECARD, k=4, issued_at=NOW,
    )
    kw.update(over)
    return build_manifest(**kw)


# --- 1. deterministic hashing, across processes ---------------------------- #

def test_manifest_round_trips_and_hashes_deterministically():
    m = _manifest()
    again = type(m).model_validate_json(m.model_dump_json())
    assert m.manifest_hash() == again.manifest_hash()


def test_hash_is_stable_in_a_separate_process():
    m = _manifest()
    payload = m.model_dump_json()
    code = (
        "import sys, json;"
        "sys.path.insert(0, r'%s');"
        "from agenttic.schema.attestation import EvidenceManifest;"
        "print(EvidenceManifest.model_validate_json(sys.stdin.read()).manifest_hash())"
        % str(ROOT / "src")
    )
    out = subprocess.run([sys.executable, "-c", code], input=payload,
                         capture_output=True, text=True, check=True)
    assert out.stdout.strip() == m.manifest_hash()


def test_float_precision_and_key_order_do_not_change_the_hash():
    a = content_hash({"b": 1, "a": 0.1 + 0.2})
    b = content_hash({"a": 0.3, "b": 1})
    assert a == b


# --- 2. attest -> verify, and precise tamper detection --------------------- #

def test_attest_then_verify_passes():
    signed = sign_manifest(_manifest())
    pub = public_key_b64(local_signing_key().public_key())
    res = verify_manifest(signed, public_key_b64_str=pub, scorecard=SCORECARD,
                          current_config_hash="cfg-abc123", now=NOW)
    assert res.ok and res.status == "valid"


def test_mutating_one_byte_of_the_scorecard_fails_with_a_precise_reason():
    signed = sign_manifest(_manifest())
    tampered = {**SCORECARD, "task_success_rate": 0.87}   # one byte
    res = verify_manifest(signed, scorecard=tampered, now=NOW)
    assert not res.ok and res.status == "invalid"
    assert any("scorecard altered" in p for p in res.problems)


def test_mutating_the_rubric_version_breaks_the_signed_hash():
    signed = sign_manifest(_manifest())
    signed.manifest.rubric_version = 4          # tamper after signing
    res = verify_manifest(signed, scorecard=SCORECARD, now=NOW)
    assert not res.ok and res.status == "invalid"
    assert any("manifest body altered" in p for p in res.problems)


def test_bad_signature_is_reported():
    signed = sign_manifest(_manifest())
    other_pub = public_key_b64(
        __import__("cryptography.hazmat.primitives.asymmetric.ed25519",
                   fromlist=["Ed25519PrivateKey"]).Ed25519PrivateKey.generate().public_key())
    res = verify_manifest(signed, public_key_b64_str=other_pub,
                          scorecard=SCORECARD, now=NOW)
    assert not res.ok
    assert any("signature does not verify" in p for p in res.problems)


# --- 3. subject binding (Hard Rule 53) ------------------------------------- #

def test_verification_fails_when_the_agent_config_hash_differs():
    signed = sign_manifest(_manifest())
    res = verify_manifest(signed, scorecard=SCORECARD,
                          current_config_hash="cfg-DIFFERENT", now=NOW)
    assert not res.ok
    assert any("subject mismatch" in p for p in res.problems)


# --- 4. ABOM --------------------------------------------------------------- #

def test_abom_validates_and_is_referenced_by_hash():
    doc = build_abom(
        subject_name="pilot", subject_version="1.2.0",
        model_ids=["claude-sonnet-5"], model_parameters={"temperature": 0.0},
        prompts={"system": "You are a support agent."},
        tools=[{"name": "refund", "version": "2", "mutating": True}],
        mcp_servers=[{"name": "billing-mcp", "version": "0.9", "transport": "stdio"}],
        suite=("suite-support", 2), rubric=("rubric-support", 3),
        harness_version="1.0.1",
        dependencies=[{"name": "anthropic", "version": "0.117.1"}],
        timestamp=NOW)
    validate_abom(doc)                       # CycloneDX-shaped
    assert doc["bomFormat"] == "CycloneDX"
    # the prompt is recorded by hash, never inlined
    blob = json.dumps(doc)
    assert "You are a support agent." not in blob
    # referenced from the manifest by hash
    h = abom_sha256(doc)
    signed = sign_manifest(_manifest(abom_sha256=h))
    res = verify_manifest(signed, scorecard=SCORECARD, abom=doc, now=NOW)
    assert res.ok
    # a mutated ABOM is caught
    doc["components"].append({"type": "application", "bom-ref": "tool:x", "name": "x"})
    bad = verify_manifest(signed, scorecard=SCORECARD, abom=doc, now=NOW)
    assert not bad.ok and any("ABOM altered" in p for p in bad.problems)


# --- 5. expiry (Hard Rule 52) ---------------------------------------------- #

def test_expired_certificate_verifies_as_expired_not_valid():
    signed = sign_manifest(_manifest(expires_in_days=30))
    later = NOW + timedelta(days=31)
    res = verify_manifest(signed, scorecard=SCORECARD, now=later)
    assert res.status == "expired"
    assert not res.ok

def test_manifest_cannot_be_issued_without_an_expiry():
    with pytest.raises(ValueError):
        _manifest(expires_in_days=0)          # expires_at <= issued_at


# --- 6. drift suspends, revocation list is signed -------------------------- #

def test_drift_suspends_the_certificate_and_appends_a_signed_entry():
    m = _manifest()
    signed = sign_manifest(m)
    rl = new_revocation_list()
    # the live monitor filed a re-eval request for this subject
    entries = suspend_on_drift(
        rl, [m], reeval_reasons_for={"pilot": ["criterion tone drifted 0.21"]},
        now=NOW)
    assert len(entries) == 1 and entries[0].status == "suspended"
    assert entries[0].source == "drift:re_eval_request"

    res = verify_manifest(signed, scorecard=SCORECARD, revocations=rl, now=NOW)
    assert res.status == "suspended" and not res.ok
    assert "drifted" in res.reason

    # the published list is signed and append-only
    doc = sign_revocation_list(rl, tier="local_self_attested")
    assert doc["content_sha256"] == rl.content_sha256()
    assert doc["signature"] and doc["kid"].startswith("ed25519:")
    before = len(rl.entries)
    append_revocation(rl, manifest_id="m-2", subject_config_hash="x",
                      reason="manual", status="revoked")
    assert len(rl.entries) == before + 1      # appended, nothing rewritten


def test_revocation_beats_expiry_in_the_reported_status():
    m = _manifest(expires_in_days=1)
    signed = sign_manifest(m)
    rl = new_revocation_list()
    append_revocation(rl, manifest_id=m.manifest_id,
                      subject_config_hash=m.subject.agent_config_hash,
                      status="revoked", reason="withdrawn")
    res = verify_manifest(signed, scorecard=SCORECARD, revocations=rl,
                          now=NOW + timedelta(days=5))
    assert res.status == "revoked"


# --- 7. Hard Rule 51 — sign the evidence, never the verdict ---------------- #

def test_certificate_renders_scope_and_limits():
    signed = sign_manifest(_manifest())
    text = render_certificate(signed)
    assert "SCOPE" in text and "LIMITS" in text
    assert "NOT ASSESSED" in text

def test_local_tier_is_never_presented_as_third_party_assurance():
    text = render_certificate(sign_manifest(_manifest()))
    assert "LOCAL SELF-ATTESTATION" in text
    assert "NOT a third-party assurance" in text

def test_no_rendered_artifact_asserts_the_agent_is_safe():
    text = render_certificate(sign_manifest(_manifest()))
    low = text.lower()
    for claim in BANNED_CLAIMS:
        assert claim not in low, f"certificate asserts banned claim {claim!r}"
    # the guard itself fires on a bad artifact
    with pytest.raises(AssertionError):
        assert_no_banned_claims("This agent is safe to deploy.")

def test_manifest_rejects_a_banned_claim_in_its_own_statements():
    with pytest.raises(ValueError):
        _manifest(scope_statement="This agent is safe.")


# --- local key handling ---------------------------------------------------- #

def test_local_key_is_generated_on_first_use_and_reused():
    assert not local_key_path().exists()
    k1 = local_signing_key()
    assert local_key_path().exists()
    k2 = local_signing_key()
    assert (k1.private_bytes_raw() == k2.private_bytes_raw())
