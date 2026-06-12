"""Step 10 acceptance tests (SPEC.md):
- Rendering a scorecard produces a client-presentable document with no
  placeholders, including tier banner, provisional flags, regression diff,
  and recommendations with example cases.
"""

import re

from ascore.schema.rubric import Criterion, Rubric
from ascore.schema.scorecard import CriterionScore, RunScore, Scorecard
from ascore.reporting.scorecard_report import render_markdown

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
