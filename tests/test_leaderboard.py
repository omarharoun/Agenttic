"""Agenttic Index leaderboard: pure compute_leaderboard ranking + coverage."""

from ascore.leaderboard import compute_leaderboard


def sc(agent, suite, rate, *, cost=0.01, p95=100.0, created="2026-06-15T00:00:00",
       version=1, tier="glass_box", n_errored=0, sid=None):
    return {"scorecard_id": sid or f"{agent}-{suite}-{created}",
            "agent_id": agent, "suite_id": suite, "suite_version": version,
            "task_success_rate": rate, "mean_cost_usd": cost,
            "p95_latency_ms": p95, "visibility_tier": tier,
            "n_errored": n_errored, "created_at": created}


class TestRanking:
    def test_weighted_mean_index_and_order(self):
        cards = [
            sc("a", "s1", 0.8), sc("a", "s2", 1.0),   # index 90
            sc("b", "s1", 0.6), sc("b", "s2", 0.6),   # index 60
        ]
        board = compute_leaderboard(cards)
        assert board["suites"] == ["s1", "s2"]
        rows = board["agents"]
        assert [r["agent_id"] for r in rows] == ["a", "b"]
        assert rows[0]["index"] == 90.0 and rows[0]["rank"] == 1
        assert rows[1]["index"] == 60.0 and rows[1]["rank"] == 2

    def test_latest_scorecard_per_agent_suite_wins(self):
        cards = [
            sc("a", "s1", 0.2, created="2026-06-10T00:00:00"),
            sc("a", "s1", 0.9, created="2026-06-15T00:00:00"),  # newer
        ]
        board = compute_leaderboard(cards)
        assert board["agents"][0]["index"] == 90.0

    def test_weights_change_the_index(self):
        cards = [sc("a", "s1", 1.0), sc("a", "s2", 0.0)]
        assert compute_leaderboard(cards)["agents"][0]["index"] == 50.0
        weighted = compute_leaderboard(cards, weights={"s1": 3.0, "s2": 1.0})
        assert weighted["agents"][0]["index"] == 75.0


class TestCoverage:
    def test_coverage_reflects_partial_suite_runs(self):
        cards = [sc("a", "s1", 1.0), sc("a", "s2", 1.0),
                 sc("b", "s1", 1.0)]  # b only ran s1
        rows = {r["agent_id"]: r for r in compute_leaderboard(cards)["agents"]}
        assert rows["a"]["coverage"] == 2 and rows["a"]["total_suites"] == 2
        assert rows["b"]["coverage"] == 1 and rows["b"]["total_suites"] == 2

    def test_suite_filter_restricts_to_common_set(self):
        cards = [sc("a", "s1", 0.5), sc("a", "s2", 1.0),
                 sc("b", "s1", 1.0)]
        board = compute_leaderboard(cards, suite_filter=["s1"])
        assert board["suites"] == ["s1"]
        rows = {r["agent_id"]: r for r in board["agents"]}
        # now both compared only on s1: b (100) beats a (50)
        assert rows["b"]["index"] == 100.0 and rows["a"]["index"] == 50.0
        assert rows["a"]["coverage"] == 1

    def test_blended_cost_latency_and_mixed_tier(self):
        cards = [sc("a", "s1", 1.0, cost=0.02, p95=200, tier="glass_box"),
                 sc("a", "s2", 1.0, cost=0.04, p95=400, tier="black_box")]
        r = compute_leaderboard(cards)["agents"][0]
        assert r["mean_cost_usd"] == 0.03 and r["p95_latency_ms"] == 300
        assert r["visibility_tier"] == "mixed"

    def test_empty(self):
        assert compute_leaderboard([]) == {"suites": [], "agents": []}
