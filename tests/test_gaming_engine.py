"""EGR engine integration (Phase 2) — the five checks, the gaming rubric, the
catalog block, and the PROVISIONAL scorecard produced via the real engine.
"""

from __future__ import annotations

import pytest

from ascore.gaming.checks import gaming_run_scores, gaming_scorecard
from ascore.gaming.rubric import GAMING_RUBRIC, GAMING_UNCALIBRATED, SUB_RUBRICS
from ascore.gaming.schema import GamingProbeResult, GamingReport
from ascore.metrics.catalog import BY_ID, CHECK_TO_METRIC, index_weights
from ascore.scoring.checks import CHECKS, validate_rubric_checks


def _probe(mechanism, sub_score, *, incident=False, critical_incident=False):
    return GamingProbeResult(
        probe_id=f"{mechanism}-1", mechanism=mechanism, base_intent="intent",
        critical=critical_incident, gap=0.0, sub_score=sub_score,
        incident=incident, critical_incident=critical_incident)


def _report(probes):
    return GamingReport(agent_id="agent-x", egr=0.0, egr_low=0.0, egr_high=0.0,
                        probe_results=probes, n_probes=len(probes))


class TestChecksRegistered:
    def test_five_checks_present(self):
        for name in ("eval_awareness_consistent", "not_sandbagging",
                     "framing_consistent", "heldout_no_freshness_gap",
                     "no_eval_gaming_incident"):
            assert name in CHECKS

    def test_rubric_validates(self):
        validate_rubric_checks(GAMING_RUBRIC)
        for r in SUB_RUBRICS.values():
            validate_rubric_checks(r)


class TestCatalog:
    def test_five_weight_zero_entries(self):
        ids = ["eval_gaming_resistance", "eval_awareness", "sandbagging_resistance",
               "framing_consistency", "heldout_freshness"]
        for mid in ids:
            assert mid in BY_ID, mid
            assert BY_ID[mid].weight == 0.0
            assert BY_ID[mid].category == "eval_gaming"

    def test_excluded_from_weighted_index(self):
        # EGR must never enter the weighted Agenttic Index
        assert "eval_gaming_resistance" not in index_weights()

    def test_checks_roll_up_to_metrics(self):
        assert CHECK_TO_METRIC["no_eval_gaming_incident"] == "eval_gaming_resistance"
        assert CHECK_TO_METRIC["eval_awareness_consistent"] == "eval_awareness"


class TestProvisionalScorecard:
    def test_scorecard_is_provisional_and_black_box(self):
        report = _report([
            _probe("behavior_delta", 1.0),
            _probe("sandbagging", 1.0),
            _probe("consistency", 1.0),
            _probe("heldout", 1.0),
        ])
        sc = gaming_scorecard(report)
        assert sc.visibility_tier == "black_box"
        assert sc.rubric_id == "gaming_resistance"
        # every criterion score is PROVISIONAL (calibrated=False)
        for rs in sc.run_scores:
            for cs in rs.criterion_scores:
                assert cs.calibrated is False
        # per-criterion means carry the mechanism checks
        assert "eval_awareness_v1" in sc.per_criterion_means
        assert "no_gaming_incident_v1" in sc.per_criterion_means

    def test_incident_lowers_means_and_marks_uncalibrated(self):
        report = _report([_probe("behavior_delta", 0.0, incident=True)])
        rs = gaming_run_scores(report)[0]
        # the incident criterion scored 0, mechanism criterion scored 0
        by_id = {c.criterion_id: c for c in rs.criterion_scores}
        assert by_id["eval_awareness_v1"].score == 0.0
        assert by_id["no_gaming_incident_v1"].score == 0.0
        assert GAMING_UNCALIBRATED.issuperset(by_id)

    def test_wrong_mechanism_wiring_is_a_config_error(self):
        from ascore.gaming.checks import not_sandbagging
        from ascore.schema.testcase import TestCase
        from ascore.schema.trace import Trace
        from ascore.scoring.checks import CheckConfigError
        tc = TestCase(test_id="p", suite_id="g", task_description="i", rubric_id="r",
                      expected={"gaming": _probe("behavior_delta", 1.0).model_dump()})
        tr = Trace(trace_id="t", agent_id="a", agent_config_hash="h",
                   visibility="black_box", final_output="x")
        with pytest.raises(CheckConfigError):
            not_sandbagging(tr, tc)
