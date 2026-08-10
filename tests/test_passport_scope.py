"""SPEC-14 M50 (Step 66) — the behavioral scope statement.

The scope is generated DIRECTLY from a scorecard, with no hand-authored fields.
It carries what was verified AND names the edge of the evidence: provisional
criteria are never listed as verified, every unexercised coverage bin travels
with the credential, and a scope missing a section is not a scope.
"""

import re

import pytest

from agenttic.schema.rubric import Criterion, Rubric
from agenttic.schema.scorecard import CriterionScore, RunScore, Scorecard


def _run(i, routing, tone, *, tone_cal=False):
    return RunScore(
        trace_id=f"tr-{i}", test_id=f"tc-{i}",
        passed=(routing + tone) / 2 >= 0.7,
        criterion_scores=[
            CriterionScore(criterion_id="routing", score=routing, scorer="code"),
            CriterionScore(criterion_id="tone", score=tone, scorer="judge",
                           calibrated=tone_cal),
        ],
        cost_usd=0.01 * (i + 1), latency_ms=100.0 * (i + 1), steps=i + 2)


def _scorecard(runs=None):
    sc = Scorecard.aggregate(
        scorecard_id="sc-real", agent_id="agent-x", suite_id="suite-1",
        suite_version=3, rubric_id="rub-1", rubric_version=2,
        run_scores=runs or [_run(0, 1.0, 1.0), _run(1, 1.0, 1.0)],
        visibility_tier="glass_box")
    sc.coverage = {
        "model_ref": "cov-1", "trace_closure": 0.37, "closure_target": 0.9,
        "closed": False, "baseline": False,
        "per_coverpoint": {
            "tool_condition": {"closure": 0.0, "unhit": ["timeout", "5xx",
                                                         "rate_limit"]},
            "session_shape": {"not_measurable": True,
                              "not_measurable_reason": "no user_turn span"},
        },
        "assertions": {"verdict": "INCOMPLETE", "violations": 0, "total": 5,
                       "unexercised": 5, "unexercised_properties": ["p1", "p2"]},
    }
    return sc


RUBRIC = Rubric(rubric_id="rub-1", version=2, criteria=[
    Criterion(criterion_id="routing", description="routes correctly", scorer="code",
              scale="binary", check_ref="final_output_matches_expected"),
    Criterion(criterion_id="tone", description="professional tone", scorer="judge",
              scale="three_point", anchors={"pass": "p", "fail": "f"}),
])


def _scope(sc=None, **kw):
    from agenttic.passport.scope import BehavioralScope
    return BehavioralScope.from_scorecard(sc or _scorecard(), RUBRIC, **kw)


class TestVerifiedVsProvisional:
    def test_provisional_never_listed_as_verified(self):
        scope = _scope()  # tone is an uncalibrated judge criterion
        verified_ids = {c.criterion_id for c in scope.verified_capabilities}
        provisional_ids = {c.criterion_id for c in scope.provisional_capabilities}
        assert "routing" in verified_ids          # deterministic + passed
        assert "tone" not in verified_ids         # uncalibrated judge: never verified
        assert "tone" in provisional_ids          # listed as claimed-but-unproven

    def test_calibrated_judge_can_be_verified(self):
        from agenttic.reporting.scorecard_report import CalibrationRecord
        scope = _scope(records={"tone": CalibrationRecord(0.85, 0.92)})
        verified_ids = {c.criterion_id for c in scope.verified_capabilities}
        assert "tone" in verified_ids and "routing" in verified_ids

    def test_partial_pass_is_not_a_verified_capability(self):
        sc = _scorecard(runs=[_run(0, 1.0, 1.0), _run(1, 0.0, 1.0)])  # routing 50%
        verified_ids = {c.criterion_id for c in _scope(sc).verified_capabilities}
        assert "routing" not in verified_ids  # did not pass on every case


class TestEdgeOfEvidence:
    def test_every_unexercised_bin_is_named(self):
        scope = _scope()
        holes = " ".join(scope.coverage_holes)
        for b in ("timeout", "5xx", "rate_limit"):
            assert b in holes
        nm = " ".join(str(x) for x in scope.not_measured)
        assert "session_shape" in nm  # not-measurable coverpoint surfaced

    def test_no_unexercised_bin_silently_dropped(self):
        scope = _scope()
        declared = scope.coverage_holes  # "coverpoint:bin" entries
        # every unhit bin from the coverage model appears in the scope's holes
        for cp in _scorecard().coverage["per_coverpoint"].values():
            for b in cp.get("unhit", []):
                assert any(b in hole for hole in declared), f"{b} dropped"

    def test_assertions_carry_unexercised_counts(self):
        scope = _scope()
        assert scope.assertions.get("unexercised") == 5

    def test_reliability_states_k(self):
        scope = _scope()
        assert "pass_1" in scope.reliability and "pass_k" in scope.reliability
        assert scope.reliability.get("k") == 1  # one trial per case here


class TestCompleteness:
    def test_generated_from_scorecard_no_hand_authored(self):
        sc = _scorecard()
        scope = _scope(sc)
        assert scope.scorecard_id == sc.scorecard_id
        assert scope.suite_provenance["suite_id"] == "suite-1"
        assert scope.suite_provenance["suite_version"] == 3
        assert scope.envelope["p95_latency_ms"] == sc.p95_latency_ms

    def test_empty_section_fails_validation(self):
        scope = _scope()
        from agenttic.passport.scope import ScopeIncompleteError
        scope.suite_provenance = {}  # blank a required section
        with pytest.raises(ScopeIncompleteError):
            scope.require_complete()

    def test_a_complete_scope_validates(self):
        _scope().require_complete()  # must not raise
