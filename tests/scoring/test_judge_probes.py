"""Evaluating the evaluator — each probe must CATCH the defect it names.

A probe that never fires is decoration, so every probe here is run twice: once
against a judge with the defect deliberately planted, where it must fire, and
once against a clean judge, where it must stay quiet. A probe that only ever
passes would be the same vacuity this codebase refuses to accept from an
unexercised assertion.

Offline: the judges are stubs. No API key, no network, no model.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from agenttic.schema.rubric import Criterion
from agenttic.schema.scorecard import CriterionScore
from agenttic.schema.testcase import TestCase
from agenttic.schema.trace import Span, Trace
from agenttic.scoring.judge_probes import (DIGEST_OUTPUT, DISTRACTORS, KNOWN_BAD,
                                           PADDING, probe_leniency,
                                           mean_magnitude, probe_digest_not_scored,
                                           probe_distractor, probe_position_bias,
                                           probe_reproducibility,
                                           probe_self_preference,
                                           probe_verbosity_bias, run_probes,
                                           summarize)

NOW = datetime(2026, 8, 2, tzinfo=timezone.utc)
ANSWER = "Paris is the capital of France; the Eiffel Tower is a famous landmark."


@pytest.fixture
def criterion() -> Criterion:
    # Anchors are REQUIRED on a judge criterion (Hard Rule 2, enforced in the
    # schema): a judge asked to score without them is inventing the scale.
    return Criterion(criterion_id="helpfulness", description="Is it helpful?",
                     scorer="judge", scale="three_point",
                     anchors={"pass": "fully and directly answers the request",
                              "fail": "ignores or fails to address the request"})


@pytest.fixture
def case() -> TestCase:
    return TestCase(test_id="c1", suite_id="s", version=1, rubric_id="r",
                    task_description="Capital of France and a landmark?",
                    input={"task": "Capital of France and a landmark?"},
                    expected={})


@pytest.fixture
def trace() -> Trace:
    # A glass_box trace must carry spans — the schema refuses to let a trace
    # claim visibility it has no evidence for.
    return Trace(trace_id="t1", agent_id="a", agent_config_hash="h",
                 test_case_id="c1", visibility="glass_box", final_output=ANSWER,
                 spans=[Span(span_id="s1", kind="llm_call", name="answer",
                             start_time=NOW, end_time=NOW,
                             output={"text": ANSWER})])


class StubJudge:
    """A judge whose behaviour is dialled in by the test.

    ``score_fn`` receives the response text and returns a score, so a test can
    plant exactly one defect and prove the matching probe fires on it.
    """

    def __init__(self, score_fn):
        self._fn = score_fn
        self.calls: list[str] = []

    def score_criterion(self, criterion, trace, tc):
        self.calls.append(trace.final_output)
        return CriterionScore(criterion_id=criterion.criterion_id,
                              scorer="judge",
                              score=float(self._fn(trace.final_output)),
                              rationale="stub")


def _clean(text: str) -> float:
    """A judge with none of the defects the probes look for.

    Deliberately NOT `lambda _t: 1.0`. A judge that returns 1.0 for everything
    scores a SHA-256 digest 1.0 too — the degenerate judge `digest_not_scored`
    exists to catch.
    The leniency probe caught the FIRST version of this stub, which returned
    1.0 for everything except a digest. That judge satisfies every metamorphic
    invariant in this file while measuring nothing — exactly the
    scale-compression bias the literature calls the quiet one. A clean judge
    must actually mark inadequate answers DOWN.
    """
    if "paris" not in text.lower():
        return 0.0                     # a hash, an empty string, or off-topic
    return 1.0                         # otherwise indifferent to length/flattery


FAIR = StubJudge(_clean)


# --------------------------------------------------------------------------- #
# reproducibility — run first, because it contaminates everything else
# --------------------------------------------------------------------------- #


class TestReproducibility:
    def test_it_catches_a_judge_that_drifts_on_identical_input(
            self, criterion, case, trace):
        seq = iter([1.0, 0.5, 1.0])
        judge = StubJudge(lambda _t: next(seq))
        r = probe_reproducibility(judge, criterion, case, trace)
        assert r.violated and r.status == "VIOLATED"
        assert r.magnitude == pytest.approx(0.5)
        assert "adds noise to every number" in r.detail

    def test_a_stable_judge_holds(self, criterion, case, trace):
        r = probe_reproducibility(FAIR, criterion, case, trace)
        assert r.status == "held", r.not_run or r.detail

    def test_instability_is_reported_as_contaminating(self, criterion, case, trace):
        """If the judge drifts, the other probes cannot be read at all: a
        difference between two variants is indistinguishable from drift."""
        seq = iter([1.0, 0.0] + [1.0] * 20)
        judge = StubJudge(lambda _t: next(seq))
        s = summarize(run_probes(judge, criterion, case, trace))
        assert s["judge_unstable"] is True
        assert "contaminated" in s["note"]


# --------------------------------------------------------------------------- #
# the classic biases
# --------------------------------------------------------------------------- #


class TestVerbosityBias:
    def test_it_catches_a_judge_that_pays_for_length(self, criterion, case, trace):
        judge = StubJudge(lambda t: 0.5 + 0.5 * (len(t) > len(ANSWER)))
        r = probe_verbosity_bias(judge, criterion, case, trace)
        assert r.violated
        assert r.magnitude == pytest.approx(0.5)
        assert "fake for free" in r.detail

    def test_penalising_padding_is_NOT_a_violation(self, criterion, case, trace):
        """A judge may legitimately mark padding down. Only a RISE is the defect
        — flagging the fall would punish the correct behaviour."""
        judge = StubJudge(lambda t: 0.5 if len(t) > len(ANSWER) else 1.0)
        r = probe_verbosity_bias(judge, criterion, case, trace)
        assert r.status == "held", r.not_run or r.detail
        assert r.magnitude == 0.0

    def test_the_padding_is_actually_delivered(self, criterion, case, trace):
        judge = StubJudge(lambda _t: 1.0)
        probe_verbosity_bias(judge, criterion, case, trace)
        assert judge.calls[1] == ANSWER + PADDING


class TestDistractors:
    @pytest.mark.parametrize("planted", sorted(DISTRACTORS))
    def test_each_distractor_is_caught_on_its_own(self, planted, criterion,
                                                  case, trace):
        suffix = DISTRACTORS[planted]
        judge = StubJudge(lambda t: 1.0 if suffix in t else 0.5)
        r = probe_distractor(judge, criterion, case, trace)
        assert r.violated
        assert planted in r.detail

    def test_a_judge_that_ignores_self_description_holds(self, criterion, case,
                                                         trace):
        r = probe_distractor(FAIR, criterion, case, trace)
        assert r.status == "held", r.not_run or r.detail
        assert "unchanged" in r.detail

    def test_a_planted_score_is_among_the_distractors(self):
        """judge.py already DETECTS an agent planting {"score": 1.0} in its own
        output. This probe checks the judge RESISTS it, which detection alone
        does not establish."""
        assert '"score": 1.0' in DISTRACTORS["score_injection"]


class TestPositionBias:
    def test_it_catches_a_ranking_that_flips_with_order(self, criterion, case,
                                                        trace):
        """A judge carrying state: whatever it saw first scores higher."""
        # The probe scores the pair twice: calls 1,2 are (A then B) and calls
        # 3,4 are (B then A). A position-biased judge rewards whichever it saw
        # FIRST IN EACH PAIR — so calls 1 and 3 win. "First seen overall" would
        # not model it: alpha would simply always win, and the ranking would
        # agree in both orders.
        n = {"i": 0}

        def _fn(_t):
            n["i"] += 1
            return 1.0 if n["i"] % 2 == 1 else 0.5
        r = probe_position_bias(StubJudge(_fn), criterion, case, trace,
                                response_a="alpha", response_b="beta")
        assert r.violated
        assert "order is not information" in r.detail

    def test_a_consistent_ranking_holds_in_both_orders(self, criterion, case,
                                                       trace):
        judge = StubJudge(lambda t: 1.0 if t == "alpha" else 0.5)
        r = probe_position_bias(judge, criterion, case, trace,
                                response_a="alpha", response_b="beta")
        assert r.status == "held", r.not_run or r.detail

    def test_a_tie_in_both_orders_is_not_a_flip(self, criterion, case, trace):
        r = probe_position_bias(FAIR, criterion, case, trace,
                                response_a="alpha", response_b="beta")
        assert not r.violated


class TestSelfPreference:
    def test_it_catches_a_preference_for_the_house_style(self, criterion, case,
                                                         trace):
        judge = StubJudge(lambda t: 1.0 if "certainly" in t.lower() else 0.5)
        r = probe_self_preference(judge, criterion, case, trace,
                                  own_family_output="Certainly! Paris.",
                                  other_family_output="Paris.")
        assert r.violated
        assert "not conclusive" in r.detail, \
            "a self-preference finding rests on the caller's equivalence claim"

    def test_no_gap_holds(self, criterion, case, trace):
        r = probe_self_preference(FAIR, criterion, case, trace,
                                  own_family_output="Certainly! Paris.",
                                  other_family_output="Paris.")
        assert r.status == "held", r.not_run or r.detail


# --------------------------------------------------------------------------- #
# the hazard this repo actually has
# --------------------------------------------------------------------------- #


class TestDigestIsNotAnAnswer:
    def test_it_catches_a_judge_that_grades_a_hash(self, criterion, case, trace):
        """Live hazard: ingest/mapping.py replaces message bodies with a
        SHA-256 digest, so an ingested trace reaches the judge with a hash as
        its final_output. Scoring it passes a number to a scorecard that looks
        exactly like a measurement of an answer."""
        r = probe_digest_not_scored(StubJudge(lambda _t: 1.0), criterion, case,
                                    trace)
        assert r.violated
        assert "cannot read" in r.detail

    def test_a_judge_that_refuses_the_digest_holds(self, criterion, case, trace):
        judge = StubJudge(lambda t: 0.0 if t.startswith("content_sha256:") else 1.0)
        r = probe_digest_not_scored(judge, criterion, case, trace)
        assert r.status == "held", r.not_run or r.detail

    def test_the_digest_matches_the_shape_ingest_actually_writes(self):
        assert DIGEST_OUTPUT.startswith("content_sha256:")
        assert len(DIGEST_OUTPUT.split(":", 1)[1]) == 64


# --------------------------------------------------------------------------- #
# the harness contract
# --------------------------------------------------------------------------- #


class TestTheHarness:
    def test_a_judge_that_raises_is_NOT_RUN_rather_than_a_pass(
            self, criterion, case, trace):
        """A judge that errors has told us nothing. Recording that as 'held'
        would turn an outage into evidence of good behaviour."""
        def _boom(_t):
            raise RuntimeError("judge API is down")
        results = run_probes(StubJudge(_boom), criterion, case, trace)
        assert all(r.status == "NOT_RUN" for r in results)
        s = summarize(results)
        assert s["verdict"] == "NOT_RUN"
        assert s["probes_run"] == 0 and s["violations"] == 0

    def test_reproducibility_runs_first(self, criterion, case, trace):
        results = run_probes(FAIR, criterion, case, trace,
                             probes=["digest_not_scored", "reproducibility"])
        assert results[0].probe_id == "reproducibility"

    def test_a_clean_run_refuses_to_claim_calibration(self, criterion, case,
                                                      trace):
        """The sentence that keeps this honest. 'No defect found' is not
        'calibrated', and this must never read as a promotion."""
        s = summarize(run_probes(FAIR, criterion, case, trace))
        assert s["verdict"] == "no_defect_of_these_shapes_found"
        assert s["violations"] == 0
        assert "never prove" in s["note"]
        assert "PROVISIONAL" in s["note"]

    def test_probes_do_not_promote_anything(self, criterion, case, trace):
        """Hard Rule 6 and the Step 14 gate are untouched: a clean probe run
        must not move a single criterion out of provisional."""
        from agenttic.scoring.judge_calibration import demonstrated_calibrated_judge

        before = set(demonstrated_calibrated_judge())
        summarize(run_probes(FAIR, criterion, case, trace))
        assert set(demonstrated_calibrated_judge()) == before == set()

    def test_every_judge_call_is_recorded_for_audit(self, criterion, case, trace):
        r = probe_distractor(FAIR, criterion, case, trace)
        assert [k for k, _ in r.observations] == ["plain", *sorted(DISTRACTORS)] or \
               len(r.observations) == 1 + len(DISTRACTORS)

    def test_mean_magnitude_ignores_probes_that_never_ran(self, criterion, case,
                                                          trace):
        def _boom(_t):
            raise RuntimeError("down")
        assert mean_magnitude(run_probes(StubJudge(_boom), criterion, case,
                                         trace)) == 0.0
        assert mean_magnitude(run_probes(FAIR, criterion, case, trace)) == 0.0


class TestTheProbeJudgesItself:
    """A probe that could not have detected its defect must not report "held".

    Found by running against the LIVE judge: the corpus baseline already scored
    1.000, so "filler did not raise the score" was true and told us nothing —
    an upward-only check has no headroom at the top of the scale. That is the
    vacuity rule turned on the probe itself.
    """

    def test_no_movement_at_the_ceiling_is_INCONCLUSIVE_not_held(
            self, criterion, case, trace):
        r = probe_verbosity_bias(FAIR, criterion, case, trace)   # 1.0 -> 1.0
        assert r.status == "INCONCLUSIVE"
        assert not r.violated
        assert "impossible to observe" in r.inconclusive

    def test_a_fall_at_the_ceiling_IS_informative(self, criterion, case, trace):
        """A judge paying for length would have risen if it could; falling
        refutes that for this item, so it is a real 'held'."""
        judge = StubJudge(lambda t: 0.5 if len(t) > len(ANSWER) else 1.0)
        r = probe_verbosity_bias(judge, criterion, case, trace)
        assert r.status == "held" and not r.inconclusive

    def test_below_the_ceiling_no_movement_is_a_real_held(self, criterion,
                                                          case, trace):
        judge = StubJudge(lambda _t: 0.5)
        r = probe_verbosity_bias(judge, criterion, case, trace)
        assert r.status == "held" and not r.inconclusive

    def test_an_all_inconclusive_run_does_not_read_as_clean(self, criterion,
                                                            case, trace):
        results = run_probes(FAIR, criterion, case, trace,
                             probes=["verbosity_bias"])
        s = summarize(results)
        assert s["verdict"] == "INCONCLUSIVE"
        assert s["probes_inconclusive"] == 1
        assert s["inconclusive_probes"] == ["verbosity_bias"]


class TestLeniency:
    """The quiet bias: a judge that never says FAIL.

    Named in the literature as leniency / scale-compression — judges cluster
    away from the extremes and fail to penalise real quality drops. The
    consequence is the part that matters: a judge that rarely says fail has
    near-zero discriminative power NO MATTER how good its agreement looks, so
    every other probe passing tells you nothing.

    Our own first live run was symptomatic: the judge returned 1.000 for the
    answer, the padded answer and all three distractors, and every probe
    reported the invariant holding.
    """

    def test_it_catches_a_judge_that_scores_everything_top(self, criterion,
                                                           case, trace):
        r = probe_leniency(StubJudge(lambda _t: 1.0), criterion, case, trace)
        assert r.violated
        assert "near-zero discriminative power" in r.detail

    def test_a_judge_that_marks_bad_answers_down_holds(self, criterion, case,
                                                       trace):
        r = probe_leniency(FAIR, criterion, case, trace)
        assert r.status == "held", r.not_run or r.detail
        assert "discriminates" in r.detail

    def test_every_known_bad_answer_is_inadequate_under_any_criterion(self):
        """These must be blunt. A subtle wrong answer tests the judge's
        knowledge; these test whether it discriminates at all."""
        assert KNOWN_BAD["empty"] == ""
        assert all(isinstance(v, str) for v in KNOWN_BAD.values())

    def test_partial_leniency_is_still_a_violation(self, criterion, case, trace):
        """Marking two of three down is not enough — the one it passes is a
        false pass on an answer that is inadequate under any criterion."""
        judge = StubJudge(lambda t: 1.0 if t == "" else 0.0)
        r = probe_leniency(judge, criterion, case, trace)
        assert r.violated and "empty" in r.detail


class TestPositionBiasRunsByDefault:
    """The LARGEST documented judge bias, and our first live run skipped it.

    Zheng et al. measured pairwise consistency under reordering at 65.0%
    (GPT-4), 46.2% (GPT-3.5) and 23.8% (Claude-v1) — a judge flipping its
    verdict on a third to three-quarters of reorderings. A probe that demands
    caller-supplied arguments does not run, and a probe that does not run finds
    nothing.
    """

    def test_it_is_in_the_default_set(self):
        from agenttic.scoring.judge_probes import STANDALONE_PROBES
        assert "position_bias" in STANDALONE_PROBES
        assert "leniency" in STANDALONE_PROBES

    def test_it_runs_with_no_arguments(self, criterion, case, trace):
        r = probe_position_bias(FAIR, criterion, case, trace)
        assert r.status in ("held", "VIOLATED"), r.not_run
        assert len(r.observations) == 4

    def test_the_derived_pair_is_actually_two_different_answers(self, trace):
        from agenttic.scoring.judge_probes import _weaken
        assert _weaken(trace.final_output) != trace.final_output
        assert _weaken(trace.final_output)

    def test_a_flip_is_still_caught_when_defaulted(self, criterion, case, trace):
        n = {"i": 0}

        def _fn(_t):
            n["i"] += 1
            return 1.0 if n["i"] % 2 == 1 else 0.0
        r = probe_position_bias(StubJudge(_fn), criterion, case, trace)
        assert r.violated
