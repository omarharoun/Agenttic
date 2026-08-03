"""The gate that proves our checkers can fail — and its own inventory of defects.

Run against every shipped suite on 2026-08-02, five degenerate strategies found
**18 ways to pass a suite while doing no work**. Not one was found by reading the
checks; every one was found by running a strategy that requires no capability and
watching it win.

The inventory below is a RATCHET, not an allowlist. It exists so that:

* a **new** defect fails the build immediately, and
* a **fixed** defect also fails the build, because the entry must be deleted —
  the list can only ever shrink, and shrinking it is a deliberate act.

Recording a defect is not condoning it. Each entry names the reason it is still
open, so nobody has to re-derive it.
"""

from __future__ import annotations

import pytest

from agenttic.verification.selfcheck import (DEGENERATE_PROBES, EXEMPTIONS,
                                             InertCase, ProbeOutcome,
                                             SelfCheckResult, find_inert_cases,
                                             probe_trace, run_probe,
                                             run_selfcheck, shipped_suites)

#: (suite_id, probe_id) -> passing cases, as measured 2026-08-02.
#: DELETE an entry when it is fixed — the test fails if a defect disappears
#: without the list being updated, which is what stops this becoming an
#: allowlist nobody maintains.
KNOWN_DEFECTS: dict[tuple[str, str], int] = {
    # ---- 2026-08-02, second pass: 18 -> 6, and all 3 inert cases are gone. ----
    # What remains is not "unfixed"; it is the limit of what a TEXT-ONLY
    # degenerate can be distinguished from. Each entry is a case where the
    # probe's behaviour is genuinely correct, so the count is pinned rather than
    # exempted: the ratchet still fails if any of them passes MORE cases, which
    # is the regression that would actually matter.
    #
    # `std-faithfulness-v1::no-context` ships an empty reference_context BY
    # DESIGN — its source comment says it exercises the metric's no_reference
    # degradation path. With nothing to be faithful to, no answer can be
    # unfaithful. Silence no longer passes it (answer_attempted).
    ("std-faithfulness-v1", "refuse_all"): 1,
    ("std-faithfulness-v1", "echo_question"): 1,
    ("std-faithfulness-v1", "claim_without_acting"): 1,
    # The two `abstain: True` cases. Declining an out-of-scope request IS the
    # correct answer, so a probe that declines is right for the right reason.
    # Silence no longer passes: `abstention_correct` now requires the agent to
    # SAY it is abstaining, because an empty response is the absence of a
    # decision rather than a decision.
    ("std-tool-use-v1", "refuse_all"): 2,
    ("std-tool-use-v1", "echo_question"): 2,
    ("std-tool-use-v1", "claim_without_acting"): 2,
}


#: Cases every degenerate PASSES identically — they discriminate nothing.
KNOWN_INERT: set[tuple[str, str]] = set()
#: Was 3. All fixed on 2026-08-02:
#:  * `std-faithfulness-v1::no-context` — `answer_attempted` now distinguishes
#:    silence from an answer, so the case is no longer scored identically for
#:    every possible agent.
#:  * both `std-tool-use-v1` abstain cases — `abstention_correct` now requires
#:    the agent to say it is abstaining.


@pytest.fixture(scope="module")
def result() -> SelfCheckResult:
    return run_selfcheck()


class TestTheRatchet:
    def test_no_new_way_to_pass_without_working(self, result):
        """A degenerate strategy passing a suite we have not already recorded."""
        found = {(o.suite_id, o.probe_id): o.passed_cases for o in result.defects}
        new = {k: v for k, v in found.items() if k not in KNOWN_DEFECTS}
        assert not new, (
            "a degenerate strategy passes a suite and is not in KNOWN_DEFECTS: "
            f"{new}. Either a check regressed or a suite was added without one.")

    def test_a_fixed_defect_must_be_removed_from_the_list(self, result):
        """The half that stops this becoming an allowlist nobody prunes."""
        found = {(o.suite_id, o.probe_id) for o in result.defects}
        fixed = {k for k in KNOWN_DEFECTS if k not in found}
        assert not fixed, (
            f"these are FIXED — delete them from KNOWN_DEFECTS: {sorted(fixed)}")

    def test_no_defect_got_worse(self, result):
        found = {(o.suite_id, o.probe_id): o.passed_cases for o in result.defects}
        worse = {k: (KNOWN_DEFECTS[k], v) for k, v in found.items()
                 if k in KNOWN_DEFECTS and v > KNOWN_DEFECTS[k]}
        assert not worse, f"a known defect now passes MORE cases: {worse}"

    def test_the_inert_case_list_is_exact(self, result):
        found = {(c.suite_id, c.test_id) for c in result.inert_cases}
        assert found == KNOWN_INERT, (
            f"inert cases changed. new={sorted(found - KNOWN_INERT)} "
            f"fixed={sorted(KNOWN_INERT - found)}")


class TestTheGateItself:
    """A gate that cannot fail is the thing this whole module exists to reject,
    so the gate is held to its own standard."""

    def test_it_actually_checks_every_shipped_suite(self, result):
        assert result.suites_checked >= 15, result.suites_checked

    def test_it_reports_defects_rather_than_passing_quietly(self, result):
        assert result.ok is False
        assert result.defects
        assert result.as_dict()["verdict"] == "DEFECTS FOUND"

    def test_a_degenerate_probe_never_sees_the_answer_key(self):
        """The signature IS the guarantee: probes take `input`, not the case.

        A degenerate that could read `expected` would be reading the answer key,
        and its failure would prove nothing about the checker.
        """
        import inspect

        for name, probe in DEGENERATE_PROBES.items():
            params = list(inspect.signature(probe).parameters)
            assert len(params) == 1, f"{name} takes more than the input dict"

    def test_probes_produce_no_tool_calls(self):
        """`claim_without_acting` is only a lie because nothing happened."""
        tr = probe_trace("Done — I have completed that for you.", test_case_id="c")
        assert not [s for s in tr.spans if s.kind == "tool_call"]

    def test_it_catches_a_check_that_cannot_fail(self):
        """Inject a fault: a rubric whose only check always returns 1.0. Every
        degenerate must be reported as passing it."""
        from agenttic.schema.rubric import Criterion, Rubric
        from agenttic.schema.testcase import TestCase
        from agenttic.scoring.checks import CHECKS

        CHECKS["_selfcheck_always_true"] = lambda _t, _c: 1.0
        try:
            rubric = Rubric(rubric_id="r-broken", version=1, criteria=[
                Criterion(criterion_id="always", scorer="code", scale="binary",
                          description="always passes",
                          check_ref="_selfcheck_always_true")])
            case = TestCase(test_id="c1", suite_id="s", version=1,
                            rubric_id="r-broken", task_description="d",
                            input={"request": "do a thing"}, expected={})
            out = run_probe(DEGENERATE_PROBES["empty"], [case], rubric,
                            suite_id="s", probe_id="empty")
            assert out.is_defect and out.passed_cases == 1
            assert find_inert_cases([case], rubric, suite_id="s")
        finally:
            CHECKS.pop("_selfcheck_always_true", None)

    def test_a_scorer_that_raises_is_never_counted_as_a_pass(self):
        from agenttic.schema.rubric import Criterion, Rubric
        from agenttic.schema.testcase import TestCase
        from agenttic.scoring.checks import CHECKS

        def _boom(_t, _c):
            raise RuntimeError("scorer exploded")

        CHECKS["_selfcheck_boom"] = _boom
        try:
            rubric = Rubric(rubric_id="r-boom", version=1, criteria=[
                Criterion(criterion_id="boom", scorer="code", scale="binary",
                          description="raises", check_ref="_selfcheck_boom")])
            case = TestCase(test_id="c1", suite_id="s", version=1,
                            rubric_id="r-boom", task_description="d",
                            input={}, expected={})
            out = run_probe(DEGENERATE_PROBES["empty"], [case], rubric,
                            suite_id="s", probe_id="empty")
            assert out.passed_cases == 0 and out.errored_cases == 1
            assert not out.is_defect
        finally:
            CHECKS.pop("_selfcheck_boom", None)

    def test_no_suites_is_not_a_clean_result(self):
        r = run_selfcheck(suites=[])
        assert any("absence of evidence" in n for n in r.notes)

    def test_the_report_refuses_to_overclaim(self, result):
        assert "REFUTES" in result.as_dict()["claim"]
        assert "never proves" in result.as_dict()["claim"]


class TestExemptions:
    def test_every_exemption_carries_a_reason(self):
        """An exemption without a written reason is how a gate rots."""
        assert EXEMPTIONS
        for key, reason in EXEMPTIONS.items():
            assert reason.strip(), f"{key} is exempted with no reason"

    def test_refusing_a_harmful_request_is_exempt_not_a_defect(self, result):
        """On agentharm and std-safety-refusal the request IS harmful, so
        refusing is the correct answer — the same carve-out
        test_dual_objective_security pins for `benign_task_completed`."""
        by_key = {(o.suite_id, o.probe_id): o for o in result.outcomes}
        for suite in ("std-safety-refusal-v1", "agentharm-harmful-v1"):
            o = by_key[(suite, "refuse_all")]
            assert o.passed_cases == o.total_cases
            assert o.exempt_reason and not o.is_defect

    def test_exemptions_are_reported_not_hidden(self, result):
        assert result.as_dict()["exemptions"]
