"""SPEC-5 23.3 — seed the scorecards the demo lineage is gated on, so the
console gate-replay can re-derive each edge's verdict from real data.

The lineage ledger (seed_moat) records three configs referencing sc_base /
sc_cand2 / sc_cand2b, but those scorecards were never saved. This writes them
with per-criterion means + success rates + cost/latency chosen so sim-core's
gate re-derives EXACTLY the recorded verdicts:

  * cfg_promoted02  (sc_cand2 vs sc_base):  every criterion up or flat, pass
    rate up, cost/latency within budget  -> PROMOTE
  * cfg_rejected02b (sc_cand2b vs sc_base): injection drops 0.90 -> 0.70 (beyond
    epsilon) though the pass rate rose    -> REJECT (fail-closed at the ε floor)

Idempotent: safe to re-run.
"""

from __future__ import annotations

import sys

sys.path.insert(0, "src")
from agenttic.config import load_config
from agenttic.registry.sqlite_store import Registry
from agenttic.schema.scorecard import CriterionScore, RunScore, Scorecard

N = 20
CRITERIA = ["tool_call", "refusal", "injection"]


def crit_scores(mean: float) -> list[float]:
    """A length-N list over {0, 0.5, 1} whose mean is exactly `mean` (valid for
    means in [0.5, 1] on a 0.5 grid — all our targets qualify)."""
    s = round(mean * N, 6)
    ones = round(2 * s - N)
    halves = round(2 * (N - s))
    assert ones >= 0 and halves >= 0 and ones + halves == N, (mean, ones, halves)
    return [1.0] * ones + [0.5] * halves


def build(sid: str, means: dict[str, float], success: float,
          cost: float, latency: float) -> Scorecard:
    cols = {c: crit_scores(means[c]) for c in CRITERIA}
    n_pass = round(success * N)
    runs = []
    for i in range(N):
        runs.append(RunScore(
            trace_id=f"{sid}-tr{i}", test_id=f"case{i}",
            criterion_scores=[
                CriterionScore(criterion_id=c, score=cols[c][i], scorer="judge")
                for c in CRITERIA
            ],
            passed=i < n_pass, cost_usd=cost, scoring_cost_usd=0.0,
            latency_ms=latency, steps=3,
        ))
    return Scorecard.aggregate(
        scorecard_id=sid, agent_id="support-triage-agent",
        suite_id="pilot-support-triage", suite_version=1,
        rubric_id="triage-rubric", rubric_version=1,
        run_scores=runs, visibility_tier="glass_box")


def main():
    reg = Registry(load_config("config.yaml")["paths"]["registry_db"])
    cards = [
        build("sc_base", {"tool_call": 0.80, "refusal": 0.80, "injection": 0.90},
              success=0.75, cost=0.010, latency=800),
        build("sc_cand2", {"tool_call": 0.90, "refusal": 0.85, "injection": 0.90},
              success=0.90, cost=0.014, latency=880),   # +criteria, pass up, budgets ok
        build("sc_cand2b", {"tool_call": 0.85, "refusal": 0.85, "injection": 0.70},
              success=0.80, cost=0.011, latency=820),    # injection regressed beyond eps
    ]
    for sc in cards:
        reg.save_scorecard(sc)
        print(f"  saved {sc.scorecard_id}: success={sc.task_success_rate:.2f} "
              f"means={ {k: round(v, 2) for k, v in sc.per_criterion_means.items()} }")
    print("SEED DONE — lineage replay scorecards")


if __name__ == "__main__":
    main()
