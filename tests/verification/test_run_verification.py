"""SPEC-13 on the run path: every run carries coverage + assertions, for free.

The gap these close: the verification layer existed but the console never asked
for it, so a run still led with a pass rate that is silent about everything the
suite never tried.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from agenttic.ops import verify_op
from agenttic.reporting.scorecard_report import render_markdown
from agenttic.schema.rubric import Criterion, Rubric
from agenttic.schema.scorecard import CriterionScore, RunScore, Scorecard
from agenttic.schema.trace import Span, Trace

T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
RUBRIC = Rubric(rubric_id="r", criteria=[
    Criterion(criterion_id="answer", description="d", scorer="judge",
              scale="binary", anchors={"pass": "p", "fail": "f"})])


def _sp(kind, name, i, **kw):
    return Span(span_id=f"s{i}", kind=kind, name=name,
                start_time=T0 + timedelta(seconds=i),
                end_time=T0 + timedelta(seconds=i + 1),
                input=kw.get("input", {}), output=kw.get("output", {}),
                attributes=kw.get("attributes", {}), error=kw.get("error"))


def _happy(i: int) -> Trace:
    return Trace(trace_id=f"t{i}", agent_id="a", agent_config_hash="c",
                 test_case_id=f"k{i}",
                 spans=[_sp("llm_call", "llm", 0),
                        _sp("tool_call", "lookup_account", 1),
                        _sp("final_output", "final_output", 2)],
                 visibility="glass_box", final_output="balance $142.50")


def _writes_without_read(i: int) -> Trace:
    return Trace(trace_id=f"bad{i}", agent_id="a", agent_config_hash="c",
                 test_case_id=f"kb{i}",
                 spans=[_sp("llm_call", "llm", 0),
                        _sp("tool_call", "update_account", 1),
                        _sp("final_output", "final_output", 2)],
                 visibility="glass_box", final_output="updated")


def _scorecard(pass_n: int, total: int) -> Scorecard:
    runs = [RunScore(trace_id=f"t{i}", test_id=f"c{i}", passed=i < pass_n,
                     criterion_scores=[CriterionScore(
                         criterion_id="answer", score=1.0 if i < pass_n else 0.0,
                         scorer="judge")], cost_usd=0.0067, latency_ms=1400)
            for i in range(total)]
    return Scorecard.aggregate(
        scorecard_id="sc", agent_id="a", suite_id="s", suite_version=1,
        rubric_id="r", rubric_version=1, run_scores=runs,
        visibility_tier="glass_box")


# --- the free verification layer ------------------------------------------- #

def test_every_run_gets_coverage_with_zero_model_calls():
    _a, cov = verify_op([_happy(i) for i in range(20)])
    assert cov["model_ref"].startswith("coverage:cov-baseline-deterministic")
    assert cov["baseline"] is True
    # a happy-path-only run leaves most of the space untouched, and says so
    assert cov["trace_closure"] < 0.5
    assert cov["closed"] is False
    traj = cov["per_coverpoint"]["trajectory"]
    for never in ("retry_after_error", "recovered_from_tool_failure",
                  "escalated_to_human", "refused", "budget_exceeded"):
        assert never in traj["unhit"], f"{never} should be reported unexercised"
    tools = cov["per_coverpoint"]["tool_condition"]
    assert "timeout" in tools["unhit"] and "rate_limited" in tools["unhit"]


def test_assertions_roll_up_per_property_not_per_trace():
    """20 traces x 8 properties is 160 results but only EIGHT properties."""
    traces = [_happy(i) for i in range(20)] + [_writes_without_read(0)]
    results, cov = verify_op(traces)
    a = cov["assertions"]
    assert len(results) == 8 * 21           # raw, per trace
    assert a["total"] == 8                  # rolled up, per property
    assert a["verdict"] == "FAIL"
    assert a["violations"] == 1
    broken = a["violated_properties"][0]
    assert broken["assertion_id"] == "never_write_without_prior_read"
    assert broken["traces"] == "1/21 runs"   # the minority that broke it
    # unexercised is deduplicated and only counts properties no trace exercised
    assert len(a["unexercised_properties"]) == len(set(a["unexercised_properties"]))
    assert "never_write_without_prior_read" not in a["unexercised_properties"]


def test_a_property_exercised_on_any_trace_is_not_unexercised():
    _r, cov = verify_op([_happy(0), _writes_without_read(0)])
    a = cov["assertions"]
    assert "never_write_without_prior_read" not in a["unexercised_properties"]


# --- the report leads with verification ------------------------------------ #

def test_report_leads_with_coverage_not_the_pass_rate():
    sc = _scorecard(12, 20)
    _a, cov = verify_op([_happy(i) for i in range(20)])
    sc = sc.model_copy(update={"coverage": cov})
    md = render_markdown(sc, RUBRIC)
    assert md.index("## Verification") < md.index("## Executive summary")
    assert "Coverage closure" in md
    assert "Never exercised" in md
    # the pass rate is present but demoted and scoped
    assert "Pass rate (one line among several)" in md
    assert "BASELINE coverage model only" in md
    assert md.index("Coverage closure") < md.index("Pass rate (one line")


def test_pass_rate_with_no_coverage_model_is_labelled_unscoped():
    md = render_markdown(_scorecard(12, 20), RUBRIC)
    assert "unscoped" in md
    assert "No coverage model was applied" in md


def test_a_violated_property_is_named_in_the_report():
    sc = _scorecard(20, 20)                    # every case passes
    _a, cov = verify_op([_writes_without_read(0)])
    sc = sc.model_copy(update={"coverage": cov})
    md = render_markdown(sc, RUBRIC)
    assert "Assertions: FAIL" in md
    assert "never_write_without_prior_read" in md
    # 100% pass rate, and the run still reports a broken property
    assert "100%" in md


def test_verification_never_breaks_a_run():
    """A malformed trace must not take the scorecard down with it."""
    broken = Trace(trace_id="x", agent_id="a", agent_config_hash="c",
                   test_case_id="k", spans=[_sp("final_output", "f", 0)],
                   visibility="black_box", final_output="")
    results, cov = verify_op([broken])
    assert isinstance(results, list) and isinstance(cov, dict)


# --- aggregate_op must reach EVERY caller, not just run_and_score_op -------- #

def test_aggregate_op_resolves_traces_from_the_registry(tmp_path):
    """The console's run-node aggregates from RunScores and never holds Trace
    objects. If aggregate_op only verified when handed traces, the verification
    layer would silently never reach the console — which is exactly the gap that
    made a run still lead with a bare pass rate."""
    from agenttic import ops
    from agenttic.registry.sqlite_store import Registry
    from agenttic.schema.testcase import TestCase, TestSuite

    reg = Registry(str(tmp_path / "r.db"))
    traces = [_happy(i) for i in range(3)]
    cases = [TestCase(test_id=f"c{i}", suite_id="s", task_description="t",
                      rubric_id="r") for i in range(3)]
    suite = TestSuite(suite_id="s", version=1, business_context="b",
                      test_ids=[c.test_id for c in cases], approved=True)
    reg.save_suite(suite, cases)
    reg.save_rubric(RUBRIC)
    for t in traces:
        reg.save_trace(t)

    runs = [RunScore(trace_id=t.trace_id, test_id=f"c{i}", passed=True,
                     criterion_scores=[CriterionScore(
                         criterion_id="answer", score=1.0, scorer="judge")])
            for i, t in enumerate(traces)]

    # NOTE: traces are NOT passed — aggregate_op must find them itself
    sc = ops.aggregate_op(reg, agent_id="a", suite=suite, rubric=RUBRIC,
                          runs=runs, visibility="glass_box")
    assert sc.coverage, "aggregate_op must verify even when not handed traces"
    assert sc.coverage["model_ref"].startswith("coverage:cov-baseline")
    assert sc.coverage["assertions"]["total"] == 8
    assert sc.assertions, "assertion results must be attached to the scorecard"


def test_a_missing_trace_does_not_break_aggregation(tmp_path):
    from agenttic import ops
    from agenttic.registry.sqlite_store import Registry
    from agenttic.schema.testcase import TestCase, TestSuite

    reg = Registry(str(tmp_path / "r.db"))
    cases = [TestCase(test_id="c0", suite_id="s", task_description="t", rubric_id="r")]
    suite = TestSuite(suite_id="s", version=1, business_context="b",
                      test_ids=["c0"], approved=True)
    reg.save_suite(suite, cases)
    reg.save_rubric(RUBRIC)
    runs = [RunScore(trace_id="does-not-exist", test_id="c0", passed=True,
                     criterion_scores=[CriterionScore(
                         criterion_id="answer", score=1.0, scorer="judge")])]
    sc = ops.aggregate_op(reg, agent_id="a", suite=suite, rubric=RUBRIC,
                          runs=runs, visibility="glass_box")
    assert sc.task_success_rate == 1.0        # scoring is unaffected
