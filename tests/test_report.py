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

    def test_fail_and_not_run_never_share_a_symbol(self):
        # F3: an agent FAILURE and a check that COULD NOT RUN must render as
        # distinct symbols. ERROR (not-run) must never be shown as FAIL.
        runs = [run_score(0, 1.0, 1.0),   # PASS
                run_score(1, 0.0, 0.0),   # FAIL — agent genuinely wrong
                errored_run(2)]           # ERROR — could not be scored
        md = render_markdown(make_scorecard(runs=runs), RUBRIC)
        assert re.search(r"`tc-1` \| FAIL", md)     # agent failure
        assert re.search(r"`err-2` \| ERROR", md)   # not-run, distinct symbol
        assert not re.search(r"`err-2` \| FAIL", md)  # never mislabeled FAIL

    def test_mixed_scored_and_errored(self):
        sc = make_scorecard(runs=[run_score(0, 1.0, 1.0), errored_run(1)])
        md = render_markdown(sc, RUBRIC)
        assert "1 of 1 scored" in md             # denominator excludes the errored one
        assert "1 case(s) errored" in md
        assert "ERROR" in md
        assert sc.task_success_rate == 1.0       # rate over the scored subset only


class TestCalibrationStatus:
    """F1: the `calibrated` status must be FAIL-CLOSED — derived only from a
    stored calibration record (measured alpha + human-human ceiling), never
    defaulted. There must be no code path where a missing record yields
    `calibrated`."""

    def _row(self, md, cid):
        m = re.search(rf"^\| `{re.escape(cid)}` \|.*\|$", md, re.MULTILINE)
        assert m, f"no criterion breakdown row for {cid} in:\n{md}"
        return m.group(0)

    def test_delete_calibration_record_falls_back_to_provisional(self):
        # Write-first test (HANDOVER F1): create a record -> calibrated; delete
        # it -> PROVISIONAL, never calibrated.
        from agenttic.reporting.scorecard_report import CalibrationRecord
        sc = make_scorecard()  # 'tone' is a judge criterion
        records = {"tone": CalibrationRecord(alpha=0.85, ceiling=0.92)}
        md_cal = render_markdown(sc, RUBRIC, calibration_records=records)
        assert "calibrated α=0.85 (ceiling 0.92)" in self._row(md_cal, "tone")

        del records["tone"]  # delete the calibration record
        md_prov = render_markdown(sc, RUBRIC, calibration_records=records)
        row = self._row(md_prov, "tone")
        assert "PROVISIONAL" in row
        assert "calibrated α=" not in row  # the calibrated label always carries α
        assert "α=" not in md_prov  # no alpha rendered anywhere without a record

    def test_fail_closed_default_no_records(self):
        # No calibration_records passed at all -> every judged criterion is
        # PROVISIONAL, never calibrated. Proves the DEFAULT path is fail closed.
        md = render_markdown(make_scorecard(), RUBRIC)
        assert "calibrated α=" not in md
        assert "PROVISIONAL" in self._row(md, "tone")

    def test_fail_closed_ignores_persisted_calibrated_bool(self):
        # The root cause: CriterionScore.calibrated defaults True (fail OPEN).
        # Even when the persisted bool is True, with NO stored record the label
        # must be PROVISIONAL — the report must not trust the bool.
        runs = [run_score(0, 1.0, 1.0, calibrated_tone=True),
                run_score(1, 1.0, 0.5, calibrated_tone=True)]
        md = render_markdown(make_scorecard(runs=runs), RUBRIC)
        assert "calibrated α=" not in md
        row = self._row(md, "tone")
        assert "PROVISIONAL" in row and "calibrated α=" not in row

    def test_deterministic_never_calibrated(self):
        md = render_markdown(make_scorecard(), RUBRIC)
        routing = self._row(md, "routing")  # scorer == code
        assert "deterministic" in routing
        assert "calibrated" not in routing

    def test_no_question_mark_scorer(self):
        # Even when the rubric passed to the renderer does NOT contain the scored
        # criteria (the real-run defect), the scorer must come from the scores
        # themselves, never render `?`.
        mismatched = Rubric(rubric_id="r-x", criteria=[
            Criterion(criterion_id="placeholder", description="x", scorer="code",
                      scale="binary", check_ref="final_output_matches_expected")])
        md = render_markdown(make_scorecard(), mismatched)
        assert "| ? |" not in md
        assert "`tone` | judge" in md and "`routing` | code" in md

    def test_table_and_recommendations_same_source(self):
        # A calibrated judge criterion must not also be told to "calibrate the
        # judge": table and Recommendations derive from one source of truth.
        from agenttic.reporting.scorecard_report import CalibrationRecord
        sc = make_scorecard()
        md = render_markdown(sc, RUBRIC,
                             calibration_records={"tone": CalibrationRecord(0.90, 0.95)})
        assert "calibrated α=0.90 (ceiling 0.95)" in self._row(md, "tone")
        assert "Calibrate the judge" not in md  # nothing left provisional


class TestRecommendationClassification:
    """F4: every recommendation is classified before it is emitted — agent /
    suite / evidence — so Agenttic never tells a customer to fix their agent for a
    defect in our own suite. A deterministic criterion at exactly 0% across all
    cases is a suite finding until proven otherwise."""

    def _recs(self, md):
        return md.split("## Recommendations", 1)[1]

    def test_deterministic_zero_across_all_is_suite_finding(self):
        # routing is a CODE criterion scoring 0 on every case (the missing-
        # expectation signature): a suite finding, never "improve your agent".
        runs = [run_score(0, 0.0, 1.0), run_score(1, 0.0, 1.0), run_score(2, 0.0, 0.5)]
        recs = self._recs(render_markdown(make_scorecard(runs=runs), RUBRIC))
        assert re.search(r"`routing`.*\[suite finding\]", recs)
        assert not re.search(r"Improve `routing`", recs)

    def test_every_recommendation_carries_a_classification(self):
        recs = self._recs(render_markdown(make_scorecard(), RUBRIC))
        rec_lines = [ln for ln in recs.splitlines() if ln.strip().startswith("1.")]
        assert rec_lines
        for ln in rec_lines:
            assert any(tag in ln for tag in
                       ("[agent finding]", "[suite finding]", "[evidence finding]")), ln

    def test_uncalibrated_judge_low_score_is_evidence_finding(self):
        # tone is an uncalibrated judge criterion; a low score can't be pinned on
        # the agent until the judge is calibrated -> evidence finding.
        runs = [run_score(0, 1.0, 0.0), run_score(1, 1.0, 0.0), run_score(2, 1.0, 0.5)]
        recs = self._recs(render_markdown(make_scorecard(runs=runs), RUBRIC))
        assert re.search(r"`tone`.*\[evidence finding\]", recs)

    def test_calibrated_judge_low_score_is_agent_finding(self):
        from agenttic.reporting.scorecard_report import CalibrationRecord
        runs = [run_score(0, 1.0, 0.0), run_score(1, 1.0, 0.0), run_score(2, 1.0, 0.5)]
        recs = self._recs(render_markdown(
            make_scorecard(runs=runs), RUBRIC,
            calibration_records={"tone": CalibrationRecord(0.85, 0.92)}))
        assert re.search(r"`tone`.*\[agent finding\]", recs)


class TestVerificationBlockDoNotRegress:
    """F5 (do-not-regress): the verification block leads with what was never
    exercised and demotes the pass rate. These sections are the platform's best
    output; my F1/F2a/F4 edits to this module must not disturb them."""

    def _sc_with_coverage(self):
        sc = make_scorecard()
        sc.coverage = {
            "model_ref": "cov-1", "trace_closure": 0.37, "closure_target": 0.9,
            "closed": False, "baseline": False,
            "per_coverpoint": {"tool_condition": {"closure": 0.0,
                                                  "unhit": ["timeout", "5xx"]}},
            "other_drift": {"tool_condition": 0.5},
            "assertions": {"verdict": "INCOMPLETE", "violations": 0, "total": 5,
                           "unexercised": 5, "unexercised_properties": ["p1"]},
        }
        return sc

    def test_pass_rate_demoted_and_coverage_leads(self):
        md = render_markdown(self._sc_with_coverage(), RUBRIC)
        assert "Coverage closure 37%" in md
        assert "Pass rate (one line among several):" in md

    def test_assertion_vacuity_preserved(self):
        md = render_markdown(self._sc_with_coverage(), RUBRIC)
        assert "unexercised is *not* evidence of correctness" in md

    def test_other_bin_finding_either_way_preserved(self):
        md = render_markdown(self._sc_with_coverage(), RUBRIC)
        assert "finding either way" in md


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
