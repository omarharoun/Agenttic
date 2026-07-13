"""Step 10 acceptance tests (SPEC.md):
- Rendering a scorecard produces a client-presentable document with no
  placeholders, including tier banner, provisional flags, regression diff,
  and recommendations with example cases.
"""

import re

from agenttic.schema.rubric import Criterion, Rubric
from agenttic.schema.scorecard import CriterionScore, RunScore, Scorecard
from agenttic.reporting.scorecard_report import render_markdown

RUBRIC = Rubric(rubric_id="r-1", criteria=[
    Criterion(criterion_id="routing", description="Routes to correct queue",
              scorer="code", scale="binary",
              check_ref="final_output_matches_expected"),
    Criterion(criterion_id="tone", description="Professional, empathetic tone",
              scorer="judge", scale="three_point",
              anchors={"pass": "p", "fail": "f"}),
])


def run_score(i, routing, tone, calibrated_tone=False):
    return RunScore(
        trace_id=f"tr-{i}", test_id=f"tc-{i}",
        passed=(routing * 2 + tone) / 3 >= 0.7,
        criterion_scores=[
            CriterionScore(criterion_id="routing", score=routing, scorer="code"),
            CriterionScore(criterion_id="tone", score=tone, scorer="judge",
                           calibrated=calibrated_tone,
                           judge_rationale="Slightly curt." if tone < 1 else "Good."),
        ],
        cost_usd=0.01 * (i + 1), latency_ms=100.0 * (i + 1), steps=i + 2,
    )


def make_scorecard(sid="sc-new", runs=None):
    return Scorecard.aggregate(
        scorecard_id=sid, agent_id="agent-ref", suite_id="support-v1",
        suite_version=1, rubric_id="r-1", rubric_version=1,
        run_scores=runs or [run_score(0, 1.0, 1.0), run_score(1, 1.0, 0.5),
                            run_score(2, 0.0, 0.0)],
        visibility_tier="glass_box",
    )


def errored_run(i):
    return RunScore(
        trace_id=f"e-{i}", test_id=f"err-{i}", criterion_scores=[], passed=False,
        cost_usd=0.02, latency_ms=120.0, steps=3,
        scoring_error="CheckConfigError: test err: check requires expected['forbidden_tools']")


class TestErroredReporting:
    def test_all_errored_not_zero_percent(self):
        # the red-team bug: cases all errored on a bad check config. The report
        # must NOT call this 0% / all-FAIL — it's a scoring config error.
        sc = make_scorecard(runs=[errored_run(0), errored_run(1)])
        assert sc.errored_test_ids == ["err-0", "err-1"]
        assert sc.per_criterion_means == {}      # nothing scored
        md = render_markdown(sc, RUBRIC)
        assert "No test cases could be scored" in md
        assert "0%" not in md                    # not reported as 0% passed
        assert "Errored cases" in md             # surfaced distinctly
        assert "ERROR" in md                     # per-case result, not FAIL
        assert "no criteria scored" in md.lower()  # breakdown explains emptiness

    def test_mixed_scored_and_errored(self):
        sc = make_scorecard(runs=[run_score(0, 1.0, 1.0), errored_run(1)])
        md = render_markdown(sc, RUBRIC)
        assert "1 of 1 scored" in md             # denominator excludes the errored one
        assert "1 case(s) errored" in md
        assert "ERROR" in md
        assert sc.task_success_rate == 1.0       # rate over the scored subset only


class TestReport:
    def test_client_presentable_no_placeholders(self):
        md = render_markdown(make_scorecard(), RUBRIC)
        for section in ["Executive summary", "Results by test case",
                        "Criterion breakdown", "Recommendations"]:
            assert section in md
        assert "PASS" in md and "FAIL" in md
        assert "PROVISIONAL" in md                       # uncalibrated tone flagged
        assert "Slightly curt." in md                    # judge rationale surfaced
        assert "Calibrate the judge" in md
        assert not re.search(r"\{[a-z_]+\}|TODO|XXX|lorem", md)  # no placeholders

    def test_black_box_banner(self):
        sc = make_scorecard()
        sc.visibility_tier = "black_box"
        assert "Black-box tier" in render_markdown(sc, RUBRIC)

    def test_regression_diff_section(self):
        old = make_scorecard("sc-old",
                             runs=[run_score(0, 1.0, 1.0), run_score(1, 1.0, 1.0),
                                   run_score(2, 1.0, 1.0)])
        md = render_markdown(make_scorecard(), RUBRIC, previous=old)
        assert "Regression vs previous run" in md
        assert "regressed" in md and "100% → 67%" in md
