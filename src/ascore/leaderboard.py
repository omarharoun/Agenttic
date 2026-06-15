"""Agenttic Index — a leaderboard across agents, in the spirit of
artificialanalysis.ai's Intelligence Index.

Each suite is one benchmark; the composite Index is the weighted mean of an
agent's per-suite task success rate (0..100). Only the *latest* scorecard per
(agent, suite) counts. Comparison is honest about coverage: an agent is ranked
on the suites it actually ran, with a coverage figure, and callers can restrict
to a common suite set for an apples-to-apples board.

Pure function (no I/O) so it unit-tests without a server or DB.
"""

from __future__ import annotations

from typing import Iterable


def compute_leaderboard(
    scorecards: Iterable[dict],
    *,
    weights: dict[str, float] | None = None,
    suite_filter: Iterable[str] | None = None,
    declared_types: dict[str, str] | None = None,
) -> dict:
    """Roll scorecard summaries into a ranked leaderboard.

    ``scorecards`` are summary dicts (as from ``UIStore.list_scorecards``):
    ``agent_id, suite_id, suite_version, task_success_rate, mean_cost_usd,
    p95_latency_ms, visibility_tier, created_at`` (ISO string, sortable).

    ``declared_types`` maps agent_id → its catalog variant (reference/blackbox/
    managed); each row gets an ``agent_type`` from it. Agents not in the catalog
    are ``"discovered"`` — honest about what is registered vs merely observed.
    """
    weights = weights or {}
    declared_types = declared_types or {}
    allow = set(suite_filter) if suite_filter is not None else None

    # latest scorecard per (agent, suite) by created_at (ISO strings sort)
    latest: dict[tuple[str, str], dict] = {}
    for sc in scorecards:
        if allow is not None and sc["suite_id"] not in allow:
            continue
        key = (sc["agent_id"], sc["suite_id"])
        cur = latest.get(key)
        if cur is None or sc["created_at"] > cur["created_at"]:
            latest[key] = sc

    suites = sorted({s for _, s in latest})
    by_agent: dict[str, dict[str, dict]] = {}
    for (agent_id, suite_id), sc in latest.items():
        by_agent.setdefault(agent_id, {})[suite_id] = sc

    rows: list[dict] = []
    for agent_id, per in by_agent.items():
        agent_suites = sorted(per)
        wsum = sum(weights.get(s, 1.0) for s in agent_suites)
        index = (100.0 * sum(weights.get(s, 1.0) * (per[s]["task_success_rate"] or 0.0)
                             for s in agent_suites) / wsum) if wsum else 0.0
        costs = [per[s].get("mean_cost_usd") or 0.0 for s in agent_suites]
        lats = [per[s].get("p95_latency_ms") or 0.0 for s in agent_suites]
        tiers = {per[s].get("visibility_tier") for s in agent_suites}
        rows.append({
            "agent_id": agent_id,
            "index": round(index, 1),
            "mean_cost_usd": sum(costs) / len(costs),
            "p95_latency_ms": sum(lats) / len(lats),
            "coverage": len(agent_suites),
            "total_suites": len(suites),
            "agent_type": declared_types.get(agent_id, "discovered"),
            "visibility_tier": tiers.pop() if len(tiers) == 1 else "mixed",
            "n_errored": sum(per[s].get("n_errored", 0) for s in agent_suites),
            "per_suite": {
                s: {
                    "success_rate": per[s]["task_success_rate"],
                    "suite_version": per[s].get("suite_version"),
                    "scorecard_id": per[s].get("scorecard_id"),
                    "mean_cost_usd": per[s].get("mean_cost_usd"),
                } for s in agent_suites
            },
        })

    # rank by Index desc, then cheaper, then broader coverage as tiebreakers
    rows.sort(key=lambda r: (-r["index"], r["mean_cost_usd"], -r["coverage"]))
    for i, r in enumerate(rows):
        r["rank"] = i + 1
    return {"suites": suites, "agents": rows}
