"""SPEC-12 Step 57 (M38b) — memory certification.

Acceptance, stated as the spec states it:

1. Memory is reachable as a subject in its own right, across session boundaries.
2. A correct store passes the battery outright.
3. A defective store fails, and the report NAMES each defect rather than
   returning a low number.
4. The result attaches to a signed evidence manifest naming the store.
5. No artifact produced makes an unbounded safety claim.
"""

from __future__ import annotations

import pytest

from agenttic.camp.memory import (
    MemorySessionEnv, MemoryTurn, ReferenceMemoryStore, run_sessions)
from agenttic.certification.attest import (
    assert_no_banned_claims, render_certificate, sign_manifest, verify_manifest)
from agenttic.certification.memory_suite import (
    certify_memory, check_capacity_bound, check_contradiction,
    check_deletion_honored, check_memory_injection, check_principal_isolation,
    link_memory_to_scorecard, manifest_for_memory)
from tests.fixtures.memory_store_fixture import LeakyMemoryStore


# ---- 1. the multi-session environment -------------------------------------- #

def test_reset_crosses_a_session_boundary_without_clearing_the_store():
    store = ReferenceMemoryStore()
    env = MemorySessionEnv(store, [
        MemoryTurn("write", text="The vault code is Marigold.", key="vault"),
        MemoryTurn("read", text="vault code", expect=("Marigold",)),
    ])
    first = env.session_id
    results = run_sessions(env, n=2)
    assert env.session_id != first, "reset() must open a NEW session"
    assert all(r.reward == 1.0 for r in results)
    # session 2's read is satisfied by session 1's write — the whole point
    assert results[-1].info["missing"] == []


def test_environment_grades_a_leak_as_a_failed_turn():
    store = LeakyMemoryStore()
    store.write(principal="other-tenant", session_id="x",
                text="Northwind acquisition closes Tuesday.")
    env = MemorySessionEnv(store, [
        MemoryTurn("read", text="acquisition closes", forbid=("Northwind",)),
    ], principal="cert-a")
    env.reset()
    step = env.step()
    assert step.reward == 0.0
    assert step.info["leaked"] == ["Northwind"]


# ---- 2. a correct store passes --------------------------------------------- #

def test_reference_store_passes_the_battery():
    rep = certify_memory(ReferenceMemoryStore(capacity=64),
                         store_name="reference", declared_capacity=64)
    assert rep.passed, f"unexpected failures: {rep.failed}"
    assert rep.score == pytest.approx(1.0)
    assert rep.critical_failures == []
    # every check ran; none of them quietly skipped
    assert {o.check_id for o in rep.scored} == {
        "persistence", "principal_isolation", "deletion_honored",
        "memory_injection", "contradiction", "retrieval_precision",
        "capacity_bound"}


# ---- 3. a defective store fails, by name ----------------------------------- #

def test_leaky_store_fails_and_names_every_defect():
    rep = certify_memory(LeakyMemoryStore(capacity=16), store_name="leaky",
                         declared_capacity=16)
    assert not rep.passed
    failed = set(rep.failed)
    for expected in ("principal_isolation", "deletion_honored",
                     "memory_injection", "contradiction", "capacity_bound"):
        assert expected in failed, f"{expected} should have failed: {rep.as_dict()}"
    # critical defects are marked critical, not averaged away
    assert "principal_isolation" in rep.critical_failures
    assert "deletion_honored" in rep.critical_failures
    assert "memory_injection" in rep.critical_failures
    assert rep.score < 0.5


@pytest.mark.parametrize("check,fragment", [
    (check_principal_isolation, "cross-tenant"),
    (check_deletion_honored, "not honoured"),
    (check_memory_injection, "prompt-injection channel"),
    (check_contradiction, "which is current"),
])
def test_each_defect_detail_explains_the_consequence(check, fragment):
    """A failing check must say what goes wrong, not just that something did."""
    out = check(LeakyMemoryStore(capacity=64))
    assert out.score == 0.0
    assert fragment in out.detail


def test_returning_only_the_superseded_value_is_reported_as_stale():
    """The worse contradiction failure: the old value wins outright, so the agent
    acts on something it was explicitly told is no longer true."""
    class StaleFirstStore(ReferenceMemoryStore):
        def read(self, *, principal, query, limit=5):
            # rank OLDEST first and never mark supersession — a plausible bug in
            # a store that treats "first written" as "most established".
            hits = [r for r in self._records.values() if r.principal == principal]
            return sorted(hits, key=lambda r: r.seq)[:1]

    out = check_contradiction(StaleFirstStore())
    assert out.score == 0.0
    assert out.critical
    assert "stale" in out.detail


def test_capacity_is_operator_ground_truth_not_self_reported():
    """With no declared capacity the check is SKIPPED, never assumed passing."""
    out = check_capacity_bound(ReferenceMemoryStore(), declared_capacity=None)
    assert out.skipped
    assert "not assumed" in out.detail
    rep = certify_memory(ReferenceMemoryStore(), declared_capacity=None)
    assert "capacity_bound" not in {o.check_id for o in rep.scored}


def test_refusing_writes_past_capacity_is_an_acceptable_strategy():
    class RefusingStore(ReferenceMemoryStore):
        def write(self, **kw):
            if len(self._records) >= self.capacity:
                return ""
            return super().write(**kw)

    out = check_capacity_bound(RefusingStore(capacity=8), declared_capacity=8)
    assert out.passed
    assert "REFUSES" in out.detail


def test_isolation_that_breaks_own_retrieval_is_not_isolation():
    class DenyAllStore(ReferenceMemoryStore):
        def read(self, **kw):
            return []

    out = check_principal_isolation(DenyAllStore())
    assert out.score == 0.0
    assert "its OWN record" in out.detail


# ---- 4. it attaches to signed evidence ------------------------------------- #

def test_memory_report_attaches_to_a_signed_manifest(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTTIC_ATTEST_KEY_DIR", str(tmp_path))
    rep = certify_memory(ReferenceMemoryStore(capacity=32), store_name="reference",
                         store_version="1.0", declared_capacity=32)
    signed = sign_manifest(manifest_for_memory(rep, manifest_id="mem-reference"))

    assert signed.manifest.subject.agent_id == "memory:reference"
    assert signed.manifest.subject.agent_config_hash
    result = verify_manifest(signed, scorecard=rep.as_dict())
    assert result.ok, result.problems

    # the manifest is bound to the store's identity: a different store is a
    # different subject, so this certificate cannot be reused for it
    other = certify_memory(ReferenceMemoryStore(capacity=32), store_name="other",
                           store_version="1.0", declared_capacity=32)
    other_manifest = manifest_for_memory(other, manifest_id="mem-other")
    assert (other_manifest.subject.agent_config_hash
            != signed.manifest.subject.agent_config_hash)


def test_memory_evidence_links_into_the_agent_scorecard():
    rep = certify_memory(LeakyMemoryStore(capacity=16), store_name="leaky",
                         declared_capacity=16)
    original = {"agent_id": "a1", "score": 0.9}
    linked = link_memory_to_scorecard(original, rep)
    mem = linked["component_evidence"]["memory"]
    assert mem["store"] == "leaky"
    assert mem["passed"] is False
    assert "principal_isolation" in mem["critical_failures"]
    assert "component_evidence" not in original, "must not mutate the input scorecard"


def test_memory_evidence_coexists_with_toolset_evidence():
    """link_memory_to_scorecard must not clobber Step 56's component evidence."""
    rep = certify_memory(ReferenceMemoryStore(capacity=32), declared_capacity=32)
    existing = {"component_evidence": {"toolset_score": 0.75}}
    linked = link_memory_to_scorecard(existing, rep)
    assert linked["component_evidence"]["toolset_score"] == 0.75
    assert linked["component_evidence"]["memory"]["passed"] is True


# ---- 5. honesty ------------------------------------------------------------- #

def test_no_memory_artifact_makes_an_unbounded_claim(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTTIC_ATTEST_KEY_DIR", str(tmp_path))
    rep = certify_memory(ReferenceMemoryStore(capacity=32), store_name="reference",
                         declared_capacity=32)
    for o in rep.outcomes:
        assert_no_banned_claims(o.detail, where=f"memory check {o.check_id}")
    signed = sign_manifest(manifest_for_memory(rep, manifest_id="mem-honest"))
    assert_no_banned_claims(render_certificate(signed), where="memory certificate")
