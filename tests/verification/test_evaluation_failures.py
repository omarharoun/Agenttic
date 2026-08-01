"""An evaluation that could not run is not an evaluation that passed.

The defect these pin: ``verification.assertions.evaluate`` ran all eight
properties for a trace in ONE list comprehension, and ``ops.verify_op`` wrapped
the call in ``try: results.extend(evaluate(t)) / except Exception: continue``.
So a single unrelated predicate raising on a trace destroyed that trace's
results for ALL eight properties, and ``continue`` then removed the trace from
the batch with no disclosure field anywhere. ``rollup_assertions`` reported over
a silently reduced denominator.

The consequence, measured control-vs-treatment below: a trace that genuinely
violates ``never_write_without_prior_read`` reports ``verdict=FAIL,
violations=1``. Make ONE unrelated property (``never_cross_tenant_identifiers``)
raise on that same trace and it reports ``verdict=PASS, violations=0`` — while
the coverage summary still says ``samples/samples_submitted/non_results =
2/2/0``, i.e. every run present and accounted for. And because
``VerificationSignoff.signs_off`` requires only ``assertions.status ==
"populated"`` and ``violations == 0``, the swallowed exception satisfied the
SIGNING gate: a crash in the evaluator minted a certificate over a violating
agent.

The rule encoded: the assertion leg discloses evaluation failures in the same
shape the coverage leg discloses non-results, and a non-zero count blocks
sign-off.
"""

from __future__ import annotations

import dataclasses
from datetime import datetime, timedelta, timezone

import pytest

from agenttic import ops
from agenttic.schema.signoff import (
    AssertionLeg, CoverageLeg, VerificationSignoff, build_signoff)
from agenttic.schema.trace import Span, Trace
from agenttic.verification.assertions import (
    ASSERTIONS, evaluate, rollup_assertions, summarize)

T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
VICTIM = "never_write_without_prior_read"     # the property that really breaks
INNOCENT = "never_cross_tenant_identifiers"   # the unrelated one we sabotage


def _sp(kind, name, i, **kw):
    return Span(span_id=f"s{i}", kind=kind, name=name,
                start_time=T0 + timedelta(seconds=i),
                end_time=T0 + timedelta(seconds=i + 1),
                input=kw.get("input", {}), output=kw.get("output", {}),
                attributes=kw.get("attributes", {}))


def _clean(i: int) -> Trace:
    return Trace(trace_id=f"ok{i}", agent_id="a", agent_config_hash="c",
                 test_case_id=f"k{i}", visibility="glass_box",
                 final_output="balance $142.50",
                 spans=[_sp("llm_call", "llm", 0),
                        _sp("tool_call", "lookup_account", 1),
                        _sp("final_output", "final_output", 2)])


def _writes_without_read(i: int) -> Trace:
    return Trace(trace_id=f"bad{i}", agent_id="a", agent_config_hash="c",
                 test_case_id=f"kb{i}", visibility="glass_box",
                 final_output="updated",
                 spans=[_sp("llm_call", "llm", 0),
                        _sp("tool_call", "update_account", 1),
                        _sp("final_output", "final_output", 2)])


@pytest.fixture
def sabotage(monkeypatch):
    """Make one named property raise on selected traces, leaving the other seven
    working. This is the shape of any real evaluator bug: a predicate that
    happens to choke on one trace's span layout."""
    def _apply(assertion_id: str, *, on_trace_ids: set[str] | None = None):
        spec = ASSERTIONS[assertion_id]

        def boom(trace):
            if on_trace_ids is None or trace.trace_id in on_trace_ids:
                raise ValueError("span layout not understood")
            return spec.fn(trace)

        monkeypatch.setitem(ASSERTIONS, assertion_id,
                            dataclasses.replace(spec, fn=boom))
    return _apply


# --------------------------------------------------------------------------- #
# 1. control vs treatment: the violation must survive an unrelated crash
# --------------------------------------------------------------------------- #

class TestASwallowedErrorCannotHideAViolation:
    TRACES = staticmethod(lambda: [_clean(0), _writes_without_read(0)])

    def test_control_the_violation_is_reported(self):
        _r, summary = ops.verify_op(self.TRACES())
        a = summary["assertions"]
        assert a["verdict"] == "FAIL" and a["violations"] == 1
        assert a["violated_properties"][0]["assertion_id"] == VICTIM
        assert a["evaluation_failures"] == 0

    def test_treatment_an_unrelated_crash_no_longer_erases_it(self, sabotage):
        sabotage(INNOCENT, on_trace_ids={"bad0"})
        _r, summary = ops.verify_op(self.TRACES())
        a = summary["assertions"]
        # the whole defect in two lines: this used to be PASS / 0
        assert a["verdict"] == "FAIL"
        assert a["violations"] == 1
        assert a["violated_properties"][0]["assertion_id"] == VICTIM

    def test_the_failure_itself_is_disclosed_not_merely_survived(self, sabotage):
        sabotage(INNOCENT, on_trace_ids={"bad0"})
        _r, summary = ops.verify_op(self.TRACES())
        a = summary["assertions"]
        assert a["evaluation_failures"] == 1
        assert a["evaluations_submitted"] == 16       # 2 traces x 8 properties
        assert a["evaluations"] == 15
        named = [e["assertion_id"] for e in a["evaluation_failure_properties"]]
        assert named == [INNOCENT]
        assert "1/2 runs" in a["evaluation_failure_properties"][0]["traces"]

    def test_the_three_numbers_travel_together_like_the_coverage_leg(self):
        """`samples`/`samples_submitted`/`non_results` is the shape being
        copied; a consumer must never get the count without its denominator."""
        _r, summary = ops.verify_op(self.TRACES())
        a = summary["assertions"]
        for key in ("evaluations", "evaluations_submitted", "evaluation_failures"):
            assert key in a, key
        assert a["evaluations"] + a["evaluation_failures"] == a["evaluations_submitted"]


# --------------------------------------------------------------------------- #
# 2. the signing gate
# --------------------------------------------------------------------------- #

class TestEvaluationFailuresBlockSignOff:
    def _closed(self, **assertion_kw) -> VerificationSignoff:
        kw = dict(status="populated", total=8, violations=0, unexercised=0,
                  exercised_ratio=1.0)
        kw.update(assertion_kw)
        return VerificationSignoff(
            signoff_id="so", agent_id="a",
            coverage=CoverageLeg(status="populated", closed=True,
                                 trace_closure=0.97, model_ref="m"),
            assertions=AssertionLeg(**kw))

    def test_a_clean_battery_still_signs(self):
        assert self._closed().signs_off is True

    def test_one_unevaluated_property_refuses(self):
        so = self._closed(evaluations=15, evaluations_submitted=16,
                          evaluation_failures=1,
                          evaluation_failure_properties=[f"{INNOCENT} (1/2 runs)"])
        assert so.signs_off is False
        assert so.assertions.verdict == "INCOMPLETE"
        why = " ".join(so.refusal_reasons())
        assert "could not run" in why and INNOCENT in why

    def test_a_signoff_written_before_this_field_existed_still_signs(self):
        """Deny-by-default must not be applied retroactively: every certificate
        already issued has to keep verifying."""
        legacy = self._closed().model_dump(mode="json")
        legacy["assertions"].pop("evaluations")
        legacy["assertions"].pop("evaluations_submitted")
        legacy["assertions"].pop("evaluation_failures")
        legacy["assertions"].pop("evaluation_failure_properties")
        assert VerificationSignoff.model_validate(legacy).signs_off is True

    def test_the_end_to_end_gate_refuses_the_run_the_swallow_used_to_pass(
            self, sabotage):
        """`verify_op` -> `build_signoff` -> `signs_off`, on the traces from the
        control/treatment pair above.

        Coverage over two traces is nowhere near closed, so the gate would refuse
        this run anyway — which would make the test pass without the fix. The
        coverage leg is therefore forced closed here, isolating the ASSERTION
        leg's contribution: an otherwise-signable run must still be refused when
        a property could not be evaluated.
        """
        sabotage(INNOCENT, on_trace_ids={"bad0"})
        _r, summary = ops.verify_op([_clean(0), _writes_without_read(0)])
        leg = summary["signoff"]["assertions"]
        assert leg["status"] == "populated"
        assert leg["evaluation_failures"] == 1

        so = VerificationSignoff.model_validate(summary["signoff"])
        so.coverage.closed = True                  # isolate the assertion leg
        so.assertions.violations = 0               # the real violation aside
        so.assertions.violated_properties = []
        assert so.signs_off is False
        assert any("could not run" in r for r in so.refusal_reasons())


# --------------------------------------------------------------------------- #
# 3. isolation: one broken predicate costs one property, not eight
# --------------------------------------------------------------------------- #

class TestEvaluateIsolatesEachProperty:
    def test_seven_properties_still_report_when_one_raises(self, sabotage):
        sabotage(INNOCENT)
        results = evaluate(_writes_without_read(0))
        assert len(results) == 8
        errored = [r for r in results if r.status == "error"]
        assert [r.assertion_id for r in errored] == [INNOCENT]
        assert any(r.assertion_id == VICTIM and r.status == "violation"
                   for r in results)

    def test_an_errored_result_is_not_counted_as_exercised(self, sabotage):
        sabotage(INNOCENT)
        results = evaluate(_clean(0))
        err = next(r for r in results if r.assertion_id == INNOCENT)
        assert err.exercised is False and err.errored is True
        assert "NOT EVALUATED" in err.detail
        assert "not evidence that it held" in err.detail

    def test_an_unregistered_assertion_id_still_raises(self):
        """A typo in an assertion SET is a configuration error, not a per-trace
        evaluation failure — degrading it would silently drop a property."""
        from agenttic.verification.assertions import UnknownAssertionError
        with pytest.raises(UnknownAssertionError):
            evaluate(_clean(0), assertion_ids=["no_such_property"])

    def test_a_property_that_only_ever_errored_is_not_reported_unexercised(
            self, sabotage):
        """Unexercised blames the SUITE; an evaluation failure blames the
        EVALUATOR. Merging them would send the reader to fix the wrong thing."""
        sabotage(INNOCENT)
        summary = rollup_assertions(evaluate(_clean(0)) + evaluate(_clean(1)))
        assert INNOCENT not in summary["unexercised_properties"]
        assert summary["evaluation_failures"] == 2
        assert summary["verdict"] == "INCOMPLETE"

    def test_per_trace_summarize_agrees_with_the_rollup(self, sabotage):
        sabotage(INNOCENT)
        s = summarize(evaluate(_clean(0)))
        assert s["verdict"] == "INCOMPLETE"
        assert s["evaluation_failures"] == 1
        assert s["evaluation_failure_properties"] == [INNOCENT]


# --------------------------------------------------------------------------- #
# 4. the same swallow on the aggregate path
# --------------------------------------------------------------------------- #

class TestUnloadableEvidenceIsDisclosed:
    """`aggregate_op` re-loads traces from the registry for callers that only
    hold RunScores, and `continue`d on failure — so coverage and assertions ran
    over silently fewer traces than the scorecard has run_scores."""

    def _fixtures(self, tmp_path):
        from agenttic.registry.sqlite_store import Registry
        from agenttic.schema.rubric import Criterion, Rubric
        from agenttic.schema.scorecard import CriterionScore, RunScore
        from agenttic.schema.testcase import TestCase, TestSuite

        rubric = Rubric(rubric_id="r", criteria=[Criterion(
            criterion_id="answer", description="d", scorer="judge",
            scale="binary", anchors={"pass": "p", "fail": "f"})])
        reg = Registry(str(tmp_path / "agg.db"))
        traces = [_clean(i) for i in range(3)]
        cases = [TestCase(test_id=f"c{i}", suite_id="s", task_description="t",
                          rubric_id="r") for i in range(4)]
        suite = TestSuite(suite_id="s", version=1, business_context="b",
                          test_ids=[c.test_id for c in cases], approved=True)
        reg.save_suite(suite, cases)
        reg.save_rubric(rubric)
        for t in traces:
            reg.save_trace(t)
        runs = [RunScore(trace_id=t.trace_id, test_id=f"c{i}", passed=True,
                         criterion_scores=[CriterionScore(
                             criterion_id="answer", score=1.0, scorer="judge")])
                for i, t in enumerate(traces)]
        # the fourth run's trace is gone — the case this loop used to drop
        runs.append(RunScore(
            trace_id="evaporated", test_id="c3", passed=True,
            criterion_scores=[CriterionScore(criterion_id="answer", score=1.0,
                                             scorer="judge")]))
        return reg, suite, rubric, runs

    def test_a_missing_trace_is_named_and_blocks_sign_off(self, tmp_path):
        reg, suite, rubric, runs = self._fixtures(tmp_path)
        sc = ops.aggregate_op(reg, agent_id="a", suite=suite, rubric=rubric,
                              runs=runs, visibility="glass_box")
        assert sc.task_success_rate == 1.0          # scoring is untouched
        a = sc.coverage["assertions"]
        assert a["evaluation_failures"] == 8        # one absent trace x 8 properties
        assert a["evaluations_submitted"] == 4 * 8
        assert VerificationSignoff.model_validate(sc.signoff).signs_off is False

    def test_all_evidence_present_reports_no_failures(self, tmp_path):
        reg, suite, rubric, runs = self._fixtures(tmp_path)
        sc = ops.aggregate_op(reg, agent_id="a", suite=suite, rubric=rubric,
                              runs=runs[:3], visibility="glass_box")
        assert sc.coverage["assertions"]["evaluation_failures"] == 0
        assert sc.coverage["assertions"]["evaluations_submitted"] == 3 * 8

    def test_the_reason_is_carried_not_just_the_count(self, tmp_path):
        reg, suite, rubric, runs = self._fixtures(tmp_path)
        sc = ops.aggregate_op(reg, agent_id="a", suite=suite, rubric=rubric,
                              runs=runs, visibility="glass_box")
        details = " ".join(
            e["detail"] for e in
            sc.coverage["assertions"]["evaluation_failure_properties"])
        assert "evaporated" in details and "could not be loaded" in details


# --------------------------------------------------------------------------- #
# 5. build_signoff carries the disclosure through
# --------------------------------------------------------------------------- #

def test_build_signoff_populates_the_evaluation_failure_fields(sabotage):
    sabotage(INNOCENT, on_trace_ids={"ok0"})
    results = evaluate(_clean(0)) + evaluate(_clean(1))
    s = build_signoff(signoff_id="so", agent_id="a", assertion_results=results)
    assert s.assertions.evaluation_failures == 1
    assert s.assertions.evaluations_submitted == 16
    assert s.assertions.verdict == "INCOMPLETE"
    assert INNOCENT in " ".join(s.assertions.evaluation_failure_properties)


class TestTheFailureReachesAReader:
    """The count blocked the sign-off and then reached no human artifact.

    A disclosure nothing reads is the same defect as the swallow it replaced,
    one level up: the evidence exists, the gate honours it, and the person
    holding the report cannot see it.
    """

    def test_a_scorecard_of_only_errors_is_not_a_pass(self):
        """`verification_status` counted violations and nothing else, so a
        battery whose every property FAILED TO RUN published the same word as
        one that passed."""
        from agenttic.schema.scorecard import Scorecard
        from agenttic.verification.assertions import AssertionResult

        sc = Scorecard.model_construct(
            assertions=[
                AssertionResult(assertion_id="p1", status="error", span_index=None,
                                detail="predicate raised", severity="high"),
                AssertionResult(assertion_id="p2", status="error", span_index=None,
                                detail="predicate raised", severity="high"),
            ])
        assert sc.assertion_violations == 0      # nothing was found to be wrong
        assert sc.assertion_errors == 2          # because nothing was checked
        assert sc.verification_status == "INCOMPLETE"
        assert sc.verification_status != "PASS"

    def test_a_real_violation_still_outranks_an_error(self):
        """INCOMPLETE must not mask a FAIL — a violation is the more actionable
        finding, which is the precedence `verdict_for` already sets."""
        from agenttic.schema.scorecard import Scorecard
        from agenttic.verification.assertions import AssertionResult

        sc = Scorecard.model_construct(
            assertions=[
                AssertionResult(assertion_id="p1", status="violation", span_index=3,
                                detail="wrote before read", severity="critical"),
                AssertionResult(assertion_id="p2", status="error", span_index=None,
                                detail="predicate raised", severity="high"),
            ])
        assert sc.verification_status == "FAIL"

    def test_a_clean_battery_still_passes(self):
        from agenttic.schema.scorecard import Scorecard
        from agenttic.verification.assertions import AssertionResult

        sc = Scorecard.model_construct(
            assertions=[AssertionResult(assertion_id="p1", status="held", span_index=None,
                                        detail="ok", severity="high")])
        assert sc.verification_status == "PASS"

    def test_the_report_says_why_it_is_incomplete(self):
        """The reader gets the cause, the count and the named properties — not
        just the word."""
        from agenttic.reporting.scorecard_report import _verification_block
        from agenttic.schema.scorecard import Scorecard

        sc = Scorecard.model_construct(
            n_scored=1, run_scores=[], assertions=[],
            coverage={"assertions": {
                "verdict": "INCOMPLETE", "total": 8, "violations": 0,
                "unexercised": 0, "evaluations": 7, "evaluations_submitted": 8,
                "evaluation_failures": 1,
                "evaluation_failure_properties": ["never_cross_tenant_identifiers"],
                "violated_properties": [], "unexercised_properties": [],
            }})
        text = "\n".join(_verification_block(sc))
        assert "INCOMPLETE" in text
        assert "1 of 8 property evaluation(s) could not run" in text
        assert "never_cross_tenant_identifiers" in text
        # and it must not be confused with the coverage finding next to it
        assert "failure of the CHECKER" in text
