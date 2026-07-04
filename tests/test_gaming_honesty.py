"""The honesty contract (Phase 8) — the credibility core, asserted as invariants.

A high EGR must never be presentable as proof of honesty: it ships PROVISIONAL,
stays out of the weighted Agenttic Index (weight 0), every criterion is
calibrated=False, and the surfaced copy states the limit inline.
"""

from __future__ import annotations

from ascore.gaming.checks import gaming_scorecard
from ascore.gaming.issues import build_gaming_issues, gaming_api_payload
from ascore.gaming.rubric import GAMING_UNCALIBRATED
from ascore.gaming.scoring import LIMITS_ONELINER
from ascore.gaming.schema import GamingProbeResult, GamingReport
from ascore.metrics.catalog import BY_ID, index_weights


def _clean_report():
    probes = [GamingProbeResult(probe_id=f"{m}-1", mechanism=m, base_intent="i",
                                gap=0.0, sub_score=1.0, incident=False)
              for m in ("behavior_delta", "sandbagging", "consistency", "heldout")]
    return GamingReport(agent_id="a", egr=100.0, egr_low=90.0, egr_high=100.0,
                        sub_scores={}, probe_results=probes, n_probes=4,
                        provisional=True, limits=LIMITS_ONELINER)


class TestIndexExclusion:
    def test_egr_weight_zero(self):
        assert BY_ID["eval_gaming_resistance"].weight == 0.0

    def test_egr_not_in_weighted_index(self):
        weights = index_weights()
        for mid in ("eval_gaming_resistance", "eval_awareness", "sandbagging_resistance",
                    "framing_consistency", "heldout_freshness"):
            assert mid not in weights

    def test_methodology_states_the_caveat(self):
        m = BY_ID["eval_gaming_resistance"].methodology.lower()
        assert "provisional" in m
        assert "absence of detectable" in m
        assert "never honesty" in m or "not proof" in m


class TestProvisional:
    def test_every_criterion_uncalibrated(self):
        sc = gaming_scorecard(_clean_report())
        assert sc.run_scores
        for rs in sc.run_scores:
            for cs in rs.criterion_scores:
                assert cs.calibrated is False
        assert GAMING_UNCALIBRATED  # non-empty

    def test_limits_oneliner_is_honest(self):
        low = LIMITS_ONELINER.lower()
        assert "provisional" in low
        assert "not proof of honesty" in low
        assert "silently eval-aware" in low


class TestSurfacedCopy:
    def test_clean_headline_disclaims_honesty(self):
        rep = build_gaming_issues(_clean_report())
        assert rep["summary"]["clean"] is True
        assert "not proof of honesty" in rep["summary"]["headline"].lower()

    def test_api_payload_is_provisional_with_limits(self):
        payload = gaming_api_payload(_clean_report())
        assert payload["provisional"] is True
        assert "not proof of honesty" in payload["limits"].lower()
