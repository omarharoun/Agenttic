"""SPEC-13 M44 — sign-off + vPlan (Step 64).

The deliverable stops being "your agent scored 86%". These tests pin the four
acceptance criteria: all six legs populated for the pilot, an unmapped
requirement flagged untested, the report leading with closure, and the SPEC-12
certificate embedding the sign-off hash.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from agenttic.coverage import Sample, collect
from agenttic.coverage.model import Bin, CoverageModel, Coverpoint
from agenttic.reporting.signoff_report import headline, render
from agenttic.schema.signoff import VerificationSignoff, build_signoff
from agenttic.schema.trace import Span, Trace
from agenttic.verification import evaluate
from agenttic.verification.cdv import (
    Budget, ExecutionResult, FailureSignature, run_until_closure)
from agenttic.verification.formal import (
    PolicyGraph, ToolEdge, no_tool_without_confirmation,
    no_write_from_unauthenticated, prove_all)
from agenttic.verification.vplan import Requirement, VPlan, trace as vtrace

T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _sp(kind, name, i=0, **kw):
    return Span(span_id=f"s{i}", kind=kind, name=name,
                start_time=T0 + timedelta(seconds=i),
                end_time=T0 + timedelta(seconds=i + 1),
                input=kw.get("input", {}), output=kw.get("output", {}),
                attributes=kw.get("attributes", {}), error=kw.get("error"))


def _trace(*spans, out="done"):
    fixed = [s.model_copy(update={"span_id": f"s{i}"}) for i, s in enumerate(spans)]
    return Trace(trace_id="t", agent_id="pilot", agent_config_hash="cfg-abc",
                 test_case_id="k", spans=fixed, visibility="glass_box",
                 final_output=out)


COV_MODEL = CoverageModel(
    model_id="pilot-cov", version=1,
    coverpoints=[Coverpoint(coverpoint_id="tool_condition", kind="deterministic",
                            bins=[Bin(bin_id="all_ok", predicate_ref="tool_all_ok"),
                                  Bin(bin_id="timeout", predicate_ref="tool_timeout"),
                                  Bin(bin_id="other")])],
    closure_target=0.95)

GUARD = PolicyGraph(edges=[
    ToolEdge("authenticate", requires_auth=False, grants_auth=True),
    ToolEdge("get_order", action_class="read", loads_entity=True),
    ToolEdge("confirm::issue_refund", confirms="issue_refund"),
    ToolEdge("issue_refund", action_class="write", requires_entity=True,
             requires_confirmation=True)])


def _pilot_signoff() -> VerificationSignoff:
    """A pilot run with every leg populated."""
    from agenttic.stimulus.spaces.conversational_transactional import seed_space

    good = _trace(_sp("tool_call", "get_order"), _sp("final_output", "final_output"))
    slow = _trace(_sp("tool_call", "get_order", error="timeout after 30s"),
                  _sp("final_output", "final_output"))
    report = collect(COV_MODEL, [Sample(good), Sample(slow)])
    assertions = evaluate(good)
    proofs = prove_all(GUARD, [no_tool_without_confirmation("issue_refund"),
                               no_write_from_unauthenticated()])

    def execute(scn):
        return ExecutionResult(trace=good, passed=True,
                               failures=[FailureSignature("c1", "m1", "t")],
                               cost_usd=0.02)
    cdv = run_until_closure(seed_space(), COV_MODEL, execute,
                            Budget(max_scenarios=10, max_rounds=1), batch_size=10)

    class _SC:
        task_success_rate = 0.86
        p95_latency_ms = 1900.0

    return build_signoff(
        signoff_id="so-pilot", agent_id="pilot", agent_config_hash="cfg-abc",
        coverage_report=report, assertion_results=assertions,
        proof_results=proofs, cdv_result=cdv,
        regression={"frozen_cases": 3, "k": 8, "pass_hat_k": 1.0},
        scorecard=_SC(),
        provenance={"judges": {"tone": "calibrated"},
                    "classifiers": {"intent": "provisional"},
                    "harness_version": "1.0.1"})


# --- 1. all six legs populated for the pilot ------------------------------- #

def test_signoff_has_all_six_legs_populated():
    s = _pilot_signoff()
    assert s.missing_legs() == [], s.missing_legs()
    assert s.complete
    assert set(s.populated_legs()) == set(VerificationSignoff.LEGS)
    assert s.coverage.status == "populated" and s.coverage.model_ref
    assert s.assertions.status == "populated"
    assert s.formal.status == "populated" and s.formal.proven >= 1
    assert s.convergence.status == "populated" and s.convergence.scenarios_run == 10
    assert s.regression.pass_hat_k == 1.0
    assert s.envelope.p95_latency_ms == 1900.0
    assert s.provenance.any_provisional        # the classifier is provisional


def test_a_leg_that_did_not_run_never_reads_as_a_pass():
    s = build_signoff(signoff_id="so-empty", agent_id="a")
    assert s.missing_legs() == list(VerificationSignoff.LEGS)
    assert s.signs_off is False                # deny-by-default
    assert "not run" in render(s)


def test_signoff_verdict_requires_closure_and_clean_assertions():
    s = _pilot_signoff()
    # closure on this tiny model is met, assertions clean, no counterexample
    s.coverage.closed = True
    s.assertions.violations = 0
    assert s.signs_off is True
    s.assertions.violations = 1                # one property broken
    assert s.signs_off is False
    s.assertions.violations = 0
    s.formal.counterexample = 1                # a reachable violating path
    assert s.signs_off is False


# --- 2. an unmapped requirement is flagged untested ------------------------ #

def test_an_unmapped_requirement_is_flagged_untested():
    good = _trace(_sp("tool_call", "get_order"), _sp("final_output", "final_output"))
    report = collect(COV_MODEL, [Sample(good)])
    assertions = evaluate(good)
    plan = VPlan(plan_id="pilot", requirements=[
        Requirement(requirement_id="R1", text="handle tool timeouts",
                    coverpoints=["tool_condition"]),
        Requirement(requirement_id="R2",
                    text="never disclose another tenant's data",
                    assertions=["never_cross_tenant_identifiers"]),
        # nothing maps to this one — the line that is the product
        Requirement(requirement_id="R3",
                    text="honour the refund window in every currency"),
    ])
    t = vtrace(plan, coverage_report=report, assertion_results=assertions)
    untested = {r.requirement_id for r in t.untested}
    assert "R3" in untested
    assert "NOTHING TESTS THIS" in next(r.detail for r in t.untested
                                        if r.requirement_id == "R3")
    # and it is loud in the report
    text = render(_pilot_signoff(), t)
    assert "UNTESTED REQUIREMENTS" in text
    assert "R3" in text


def test_mapped_but_unexercised_is_distinct_from_untested():
    """Different diagnosis, different fix: write a test vs run more stimulus."""
    quiet = _trace(_sp("llm_call", "think"), _sp("final_output", "final_output"))
    report = collect(COV_MODEL, [Sample(quiet)])       # no tool spans at all
    assertions = evaluate(quiet)
    plan = VPlan(plan_id="p", requirements=[
        Requirement(requirement_id="R1", text="handle timeouts",
                    coverpoints=["tool_condition"])])
    t = vtrace(plan, coverage_report=report, assertion_results=assertions)
    assert [r.status for r in t.rows] == ["unexercised"]
    assert not t.untested


def test_a_requirement_mapped_to_something_that_never_ran_is_untested():
    plan = VPlan(plan_id="p", requirements=[
        Requirement(requirement_id="R9", text="x", coverpoints=["nonexistent_cp"])])
    t = vtrace(plan, coverage_report=collect(COV_MODEL, []))
    assert t.untested and "treated as UNTESTED" in t.rows[0].detail


def test_a_waived_requirement_is_not_untested():
    plan = VPlan(plan_id="p", requirements=[
        Requirement(requirement_id="R0", text="out of scope for this release",
                    waived=True, reason="feature not shipped")])
    t = vtrace(plan)
    assert not t.untested
    assert "waived" in t.rows[0].detail


# --- 3. the report leads with closure, pass rate demoted ------------------- #

def test_report_leads_with_closure_then_assertions_then_formal():
    text = render(_pilot_signoff())
    lines = [ln for ln in text.splitlines() if "·" in ln and ln.strip()[0].isdigit()]
    order = [ln.split("·")[1].strip().split()[0] for ln in lines]
    assert order[:3] == ["COVERAGE", "ASSERTIONS", "FORMAL"]
    # the pass rate appears, but AFTER all of them
    assert text.index("COVERAGE CLOSURE") < text.index("pass rate")


def test_pass_rate_without_a_coverage_model_renders_unscoped():
    class _SC:
        task_success_rate = 0.86
        p95_latency_ms = 10.0
    s = build_signoff(signoff_id="s", agent_id="a", scorecard=_SC())
    assert s.unscoped
    assert "unscoped (no coverage model)" in s.pass_rate_label
    assert "unscoped (no coverage model)" in render(s)


def test_report_header_block_snapshot():
    s = _pilot_signoff()
    head = "\n".join(render(s).splitlines()[:4])
    assert head.splitlines()[0] == "VERIFICATION SIGN-OFF"
    assert "subject" in head and "pilot" in head
    assert "verdict" in head


def test_report_refuses_to_make_an_unqualified_claim():
    s = _pilot_signoff()
    s.formal.claims = ["This agent is proven safe."]
    with pytest.raises(AssertionError):
        render(s)


def test_headline_replaces_the_pass_rate_sentence():
    h = headline(_pilot_signoff())
    assert "coverage" in h and "assertion violation" in h and "proven" in h
    assert "bug curve" in h


# --- 4. the certificate embeds the sign-off hash --------------------------- #

def test_certificate_embeds_the_signoff_hash(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTTIC_ATTEST_KEY_DIR", str(tmp_path / "cfg"))
    from agenttic.certification.attest import (
        build_manifest, sign_manifest, verify_manifest)
    s = _pilot_signoff()
    scorecard = {"scorecard_id": "sc-1", "task_success_rate": 0.86}
    m = build_manifest(
        manifest_id="m-1", agent_id="pilot", agent_config_hash="cfg-abc",
        suite_id="s", suite_version=1, rubric_id="r", rubric_version=1,
        scorecard=scorecard, signoff_sha256=s.content_sha256())
    assert m.signoff_sha256 == s.content_sha256()
    signed = sign_manifest(m)
    assert verify_manifest(signed, scorecard=scorecard).ok
    # tampering with the sign-off breaks the manifest hash
    signed.manifest.signoff_sha256 = "0" * 64
    assert not verify_manifest(signed, scorecard=scorecard).ok


def test_signoff_hash_is_deterministic_and_ignores_timestamp():
    a, b = _pilot_signoff(), _pilot_signoff()
    b.created_at = a.created_at + timedelta(days=1)
    assert a.content_sha256() == b.content_sha256()
