"""Sample size + Wilson confidence interval — the honest-numbers plumbing.

Covers the shared Wilson helpers (stats.py), the additive n / CI computed fields
on the Scorecard, and the per-suite intervals surfaced on the leaderboard, so the
frontend can render a confidence interval next to every headline pass-rate.
"""

from __future__ import annotations

from agenttic.leaderboard import compute_leaderboard
from agenttic.schema.scorecard import CriterionScore, RunScore, Scorecard
from agenttic.stats import proportion_stats, wilson_interval, wilson_lower_bound


class TestWilsonMath:
    def test_empty_sample_is_maximal_ignorance(self):
        # no data => the whole [0,1], never a fabricated point
        assert wilson_interval(0, 0) == (0.0, 1.0)

    def test_bounds_are_ordered_and_clamped(self):
        low, high = wilson_interval(18, 25)
        assert 0.0 <= low < 0.72 < high <= 1.0

    def test_small_n_is_wider_than_large_n_same_rate(self):
        low_small, high_small = wilson_interval(18, 25)     # 72%
        low_big, high_big = wilson_interval(720, 1000)      # 72%
        assert (high_small - low_small) > (high_big - low_big)

    def test_perfect_score_lower_bound_below_one(self):
        # 5/5 should NOT read as a defensible 100% — Wilson lower bound < 1
        assert wilson_lower_bound(5, 5) < 1.0
        assert wilson_lower_bound(1000, 1000) > wilson_lower_bound(5, 5)

    def test_known_value(self):
        # classic reference: 0 successes in 10 -> lower 0.0, upper ~0.278
        low, high = wilson_interval(0, 10)
        assert low == 0.0
        assert abs(high - 0.2775) < 0.01

    def test_proportion_stats_shape(self):
        s = proportion_stats(7, 10)
        assert s["n"] == 10 and s["passes"] == 7 and s["ci_level"] == 0.95
        assert abs(s["rate"] - 0.7) < 1e-9
        assert s["wilson_low"] < 0.7 < s["wilson_high"]
        # nothing scored -> rate is None, not a fake 0.0
        assert proportion_stats(0, 0)["rate"] is None


def _run(test_id, passed, *, error=None):
    return RunScore(
        trace_id=f"t-{test_id}", test_id=test_id, passed=passed,
        scoring_error=error,
        criterion_scores=[CriterionScore(criterion_id="c",
                                         score=1.0 if passed else 0.0,
                                         scorer="code")])


class TestScorecardComputedFields:
    def _card(self, runs):
        return Scorecard.aggregate(
            scorecard_id="sc", agent_id="a", suite_id="s", suite_version=1,
            rubric_id="r", rubric_version=1, run_scores=runs,
            visibility_tier="black_box")

    def test_n_and_wilson_exclude_scoring_errors(self):
        sc = self._card([_run("1", True), _run("2", True), _run("3", False),
                         _run("4", True, error="judge outage")])
        assert sc.n_scored == 3        # errored run excluded
        assert sc.n_passed == 2
        assert 0.0 < sc.success_wilson_low < sc.task_success_rate
        assert sc.task_success_rate < sc.success_wilson_high <= 1.0

    def test_computed_fields_serialize(self):
        sc = self._card([_run("1", True), _run("2", False)])
        dumped = sc.model_dump(mode="json")
        for k in ("n_scored", "n_passed", "success_wilson_low",
                  "success_wilson_high"):
            assert k in dumped


class TestLeaderboardIntervals:
    def _summary(self, agent, suite, passes, n, **kw):
        low, high = wilson_interval(passes, n)
        return {"scorecard_id": f"{agent}-{suite}", "agent_id": agent,
                "suite_id": suite, "suite_version": 1,
                "task_success_rate": passes / n, "mean_cost_usd": 0.01,
                "p95_latency_ms": 100.0, "visibility_tier": "glass_box",
                "n_errored": 0, "created_at": "2026-06-15T00:00:00",
                "n_scored": n, "n_passed": passes,
                "success_wilson_low": round(low, 4),
                "success_wilson_high": round(high, 4), **kw}

    def test_per_suite_carries_n_and_wilson(self):
        board = compute_leaderboard([self._summary("a", "s1", 18, 25)])
        row = board["agents"][0]
        assert row["n_scored"] == 25
        ps = row["per_suite"]["s1"]
        assert ps["n_scored"] == 25 and ps["n_passed"] == 18
        assert ps["success_wilson_low"] < 0.72 < ps["success_wilson_high"]
