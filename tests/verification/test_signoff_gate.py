"""The signing gate — a certificate cannot outrun its evidence.

The defect these pin: the sign-off verdict and the signature used to be
unconnected code paths. ``VerificationSignoff.signs_off`` was correct and
deny-by-default, but its only consumer was a *renderer* — so a run could print
``DOES NOT SIGN OFF`` and mint a signed, publicly-verifiable certificate from the
same evidence. ``sign_manifest`` is the single chokepoint every production path
reaches the key through, so the gate lives there.
"""

from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone

import pytest

from agenttic.certification.attest import (
    SignoffRefused, build_manifest, key_id, local_signing_key, sign_manifest,
    verify_manifest, _signing_key_for)
from agenttic.schema.attestation import (
    EvidenceManifest, SignedManifest, content_hash)
from agenttic.schema.signoff import (
    AssertionLeg, ComponentSignoff, CoverageLeg, VerificationSignoff)
from tests.conftest import attesting_signoff

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)
SCORECARD = {"scorecard_id": "sc-1", "agent_id": "pilot", "task_success_rate": 0.86}


@pytest.fixture(autouse=True)
def _isolated_key(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTTIC_ATTEST_KEY_DIR", str(tmp_path / "cfg"))
    yield


def _manifest(signoff, **over):
    kw = dict(
        manifest_id="m-1", agent_id="pilot", agent_config_hash="cfg-abc123",
        suite_id="s", suite_version=1, rubric_id="r", rubric_version=1,
        scorecard=SCORECARD, issued_at=NOW, signoff=signoff)
    kw.update(over)
    return build_manifest(**kw)


def _unclosed_signoff() -> VerificationSignoff:
    """Real production shape: coverage measured but nowhere near closed."""
    return VerificationSignoff(
        signoff_id="so-unclosed", agent_id="pilot", agent_config_hash="cfg-abc123",
        coverage=CoverageLeg(status="populated", closed=False, trace_closure=0.204,
                             closure_target=0.95, model_ref="cov-baseline@v2",
                             unhit_bins=["action_risk.mutating_irreversible"]),
        assertions=AssertionLeg(status="populated", total=8, violations=0,
                                unexercised=3))


# --- 1. the headline: a negative sign-off is not signable ------------------- #

def test_an_unclosed_signoff_cannot_be_signed():
    so = _unclosed_signoff()
    assert so.signs_off is False
    with pytest.raises(SignoffRefused) as e:
        sign_manifest(_manifest(so), signoff=so)
    # the refusal must name the number the reader has to move
    assert "20.4%" in str(e.value) and "95%" in str(e.value)


def test_a_violation_cannot_be_signed_even_with_closed_coverage():
    so = attesting_signoff()
    so.assertions.violations = 1
    so.assertions.violated_properties = [
        "always_irreversible_action_confirmed — ran unconfirmed"]
    assert so.signs_off is False
    with pytest.raises(SignoffRefused, match="always_irreversible_action_confirmed"):
        sign_manifest(_manifest(so), signoff=so)


def test_a_clean_signoff_signs_and_verifies():
    so = attesting_signoff()
    signed = sign_manifest(_manifest(so), signoff=so)
    assert verify_manifest(signed, scorecard=SCORECARD, now=NOW).ok


# --- 2. the evidence is bound: no signing A while attesting B --------------- #

def test_a_manifest_cannot_be_built_without_any_signoff():
    with pytest.raises(SignoffRefused, match="no verification sign-off"):
        build_manifest(
            manifest_id="m-1", agent_id="pilot", agent_config_hash="cfg-abc123",
            suite_id="s", suite_version=1, rubric_id="r", rubric_version=1,
            scorecard=SCORECARD, issued_at=NOW)


def test_presenting_a_different_signoff_than_the_manifest_names_is_refused():
    bound, other = attesting_signoff(), attesting_signoff(agent_id="someone-else")
    m = _manifest(bound)
    assert other.content_sha256() != bound.content_sha256()
    with pytest.raises(SignoffRefused, match="sign-off mismatch"):
        sign_manifest(m, signoff=other)


def test_a_manifest_that_names_a_signoff_cannot_be_signed_without_it():
    m = _manifest(attesting_signoff())
    with pytest.raises(SignoffRefused, match="was not supplied"):
        sign_manifest(m)


def test_a_hand_built_manifest_bypassing_build_manifest_is_still_refused():
    """The last hole: constructing EvidenceManifest directly."""
    m = EvidenceManifest(
        manifest_id="sneaky", subject={"agent_id": "a", "agent_config_hash": "cfg"},
        suite_id="s", suite_version=1, rubric_id="r", rubric_version=1,
        scorecard_hash=content_hash(SCORECARD), visibility_tier="glass_box",
        issued_at=NOW, expires_at=NOW + timedelta(days=90), issuer="x",
        scope_statement="Attests what was measured.",
        limits_statement="Only the listed evidence.")
    with pytest.raises(SignoffRefused, match="names no verification sign-off"):
        sign_manifest(m)


# --- 3. THE PRODUCTION GUARD: already-issued certificates still verify ------ #

def test_a_certificate_signed_before_signoffs_existed_still_verifies():
    """The change must not invalidate anything already issued.

    ``manifest_hash()`` covers every field, so a naively added field would make a
    legacy manifest re-hash differently and fail its own signature — breaking
    every live /certified/:id page. Post-v1 optional fields are dropped from the
    hash when unset precisely so this test can pass.
    """
    legacy = EvidenceManifest(
        manifest_id="legacy-1",
        subject={"agent_id": "pilot", "agent_config_hash": "cfg-abc123"},
        suite_id="s", suite_version=1, rubric_id="r", rubric_version=1,
        scorecard_hash=content_hash(SCORECARD), visibility_tier="glass_box",
        issued_at=NOW, expires_at=NOW + timedelta(days=3650),
        issuer="local-self-attested",
        scope_statement="Attests what was measured.",
        limits_statement="Only the listed evidence.")
    assert legacy.signoff_sha256 is None
    assert "scope_summary" not in legacy.payload()      # absent, not null

    key, _ = _signing_key_for("local_self_attested", None)
    digest = legacy.manifest_hash()
    signed = SignedManifest(
        manifest=legacy, manifest_sha256=digest,
        signature=base64.b64encode(key.sign(digest.encode())).decode(),
        kid=key_id(key.public_key()))

    assert verify_manifest(signed, scorecard=SCORECARD, now=NOW).ok
    # and through the DB round-trip
    again = SignedManifest.model_validate_json(signed.model_dump_json())
    assert verify_manifest(again, scorecard=SCORECARD, now=NOW).ok


# --- 4. the scope travels on the certificate, tamper-evidently -------------- #

def test_the_certificate_carries_its_own_scope_and_tampering_breaks_it():
    so = _unclosed_signoff()
    m = _manifest(so)                       # built, but refused at signing
    assert m.scope_summary is not None
    assert m.scope_summary.properties_exercised == 5     # 8 total - 3 unexercised
    assert m.scope_summary.exercised_label == "5/8 properties exercised"
    assert m.scope_summary.closed is False

    before = m.manifest_hash()
    m.scope_summary.properties_exercised = 8            # flatter the scope
    assert m.manifest_hash() != before                  # covered by the signature


# --- 5. components have their own evidence contract ------------------------- #

def _component(outcomes, scope="Covers only the listed checks."):
    return ComponentSignoff.from_outcomes(
        signoff_id="c-1", component_kind="memory_store", component_ref="store",
        outcomes=outcomes, scope_statement=scope)


def test_a_component_with_a_failed_check_cannot_be_signed():
    from agenttic.certification.mcp_suite import CheckOutcome
    so = _component([CheckOutcome("deletion_honored", 0.0, "still readable",
                                  critical=True)])
    assert so.signs_off is False
    with pytest.raises(SignoffRefused, match="deletion_honored"):
        sign_manifest(_manifest(so, manifest_id="mem-1"), signoff=so)


def test_a_skipped_critical_check_is_not_a_pass():
    """CheckOutcome.passed treats a skip as a pass. The gate must not."""
    from agenttic.certification.mcp_suite import CheckOutcome
    skipped = CheckOutcome("principal_isolation", 1.0, "", critical=True,
                           skipped=True)
    assert skipped.passed is True                  # the trap
    so = _component([skipped, CheckOutcome("persistence", 1.0, "ok")])
    assert so.signs_off is False                   # the vacuity rule holds
    assert any("skipped" in r for r in so.refusal_reasons())


def test_a_component_must_say_what_it_does_not_cover():
    from agenttic.certification.mcp_suite import CheckOutcome
    so = _component([CheckOutcome("persistence", 1.0, "ok")], scope="")
    assert so.signs_off is False


def test_the_reference_store_signs_off_and_the_leaky_one_does_not():
    from agenttic.camp.memory import ReferenceMemoryStore
    from agenttic.certification.memory_suite import (
        certify_memory, signoff_for_memory)
    from tests.fixtures.memory_store_fixture import LeakyMemoryStore

    good = signoff_for_memory(certify_memory(
        ReferenceMemoryStore(), store_name="reference", declared_capacity=64))
    assert good.signs_off is True

    bad = signoff_for_memory(certify_memory(
        LeakyMemoryStore(capacity=16), store_name="leaky", declared_capacity=16))
    assert bad.signs_off is False
    assert bad.refusal_reasons()
    with pytest.raises(SignoffRefused):
        sign_manifest(_manifest(bad, manifest_id="mem-leaky"), signoff=bad)
