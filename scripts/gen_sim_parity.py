"""SPEC-5 Step 22 — golden parity fixture generator (the keystone).

Runs the REAL platform decision math over a few hundred seeded-random cases per
module and dumps `fixtures/sim-parity/*.json`: {input, expected} for each case.
`ui/src/sim-core/parity.test.ts` replays every case through the TypeScript port
and asserts equality (reason strings byte-for-byte). `tests/test_sim_parity.py`
re-runs this generator in-memory and asserts the committed fixtures still match
the engine — so a change to EITHER side that breaks parity fails the build.

The math is exercised through the production functions, never re-implemented
here (Hard Rule 24: the toy IS the machine):
  * gate            -> agenttic.learning.optimizer.gate (compare_scorecards
                       monkeypatched to inject a known, RNG-free comparison)
  * evaluate_candidate -> agenttic.optimizer.evaluate_candidate
  * drift           -> agenttic.live.monitor.LiveMonitor.status (stub registry)
  * escalation      -> AnthropicSimpleAgent._tool_policy (+ the trigger rule)
                       and scoring.checks.escalated_appropriately
  * stats           -> agenttic.stats + agenttic.scoring.calibration

Run:  .venv/bin/python scripts/gen_sim_parity.py   (writes the JSON)
"""

from __future__ import annotations

import json
import random
from pathlib import Path
from types import SimpleNamespace

import agenttic.learning.optimizer as gate_mod
from agenttic.optimizer import evaluate_candidate
from agenttic.stats import wilson_interval, wilson_lower_bound
from agenttic.scoring.calibration import exact_match_rate, krippendorff_alpha_interval
from agenttic.scoring.checks import escalated_appropriately
from agenttic.adapters.anthropic_simple import AnthropicSimpleAgent
from agenttic.live.monitor import LiveMonitor
from agenttic.schema.rubric import Criterion

OUT = Path(__file__).resolve().parents[1] / "fixtures" / "sim-parity"

# clean numeric grids so Python's :.Nf / :.0% and the TS port never disagree on
# a rounding tie by construction (dyadic ties round half-to-even on both sides;
# these grids avoid ambiguous non-dyadic halves).
RATES = [0.0, 0.2, 0.4, 0.5, 0.6, 0.8, 1.0]          # multiples of 20% / 10%
MEANS = [round(j / 100, 2) for j in range(0, 101, 5)]  # 0.00 .. 1.00 by 0.05
COSTS = [0.0, 0.005, 0.01, 0.02, 0.025, 0.05, 0.1]
LATS = [0.0, 50, 100, 250, 500, 800, 1200]
CRITS = ["tone", "accuracy", "safety", "efficiency", "helpfulness"]


# --------------------------------------------------------------------------- #
# gate + evaluate_candidate
# --------------------------------------------------------------------------- #
def _comparison(rng, crit_ids, force_sig_regress=False):
    """Build a comparison SimpleNamespace with exactly the attributes the gate
    reads (mirrors agenttic.schema.ab.ABComparison for those fields)."""
    ra, rb = rng.choice(RATES), rng.choice(RATES)
    per = []
    for cid in crit_ids:
        delta = round(rng.choice([-0.3, -0.1, -0.05, 0.0, 0.05, 0.1, 0.3]), 2)
        sig = force_sig_regress and delta < 0
        per.append(SimpleNamespace(criterion_id=cid, delta=delta,
                                   p_value=0.01 if sig else 0.5,
                                   n=rng.choice([6, 10, 20]), significant=sig))
    return SimpleNamespace(success_rate_a=ra, success_rate_b=rb,
                           success_delta=round(rb - ra, 10),
                           n_paired=rng.choice([5, 6, 10, 20]), per_criterion=per)


def _comp_to_json(comp):
    return {
        "successRateA": comp.success_rate_a, "successRateB": comp.success_rate_b,
        "successDelta": comp.success_delta, "nPaired": comp.n_paired,
        "perCriterion": [{"criterionId": c.criterion_id, "delta": c.delta,
                          "significant": c.significant} for c in comp.per_criterion],
    }


def gen_evaluate_candidate(rng, n=80):
    cases = []
    for _ in range(n):
        k = rng.randint(1, 4)
        comp = _comparison(rng, rng.sample(CRITS, k),
                           force_sig_regress=rng.random() < 0.3)
        accept, regs, reason = evaluate_candidate(comp)
        cases.append({"input": _comp_to_json(comp),
                      "expected": {"accept": accept, "reason": reason,
                                   "regressions": [r.criterion_id for r in regs]}})
    return cases


def _scorecard(means, cost, p95, agent_id="agent"):
    return SimpleNamespace(per_criterion_means=dict(means),
                           mean_cost_usd=cost, p95_latency_ms=p95,
                           agent_id=agent_id, task_success_rate=0.0)


def gen_gate(rng, n=120):
    cases, cfg = [], {"learning": {"epsilon": 0.02, "max_cost_multiplier": 2.0,
                                   "max_latency_multiplier": 2.0}}
    orig = gate_mod.compare_scorecards
    try:
        for i in range(n):
            k = rng.randint(1, 4)
            crit_ids = rng.sample(CRITS, k)
            comp = _comparison(rng, crit_ids, force_sig_regress=rng.random() < 0.2)
            base_means = {c: rng.choice(MEANS) for c in crit_ids}
            cand_means = {c: rng.choice(MEANS) for c in crit_ids}
            # deliberately exercise the moat branches on a fraction of cases
            r = i % 6
            if r == 0:                       # lobotomy: drop a criterion
                cand_means.pop(crit_ids[0], None)
                comp = _comparison(rng, crit_ids); comp.success_delta = 0.2
                comp.success_rate_a, comp.success_rate_b = 0.6, 0.8
            elif r == 1:                     # clean win: candidate >= baseline
                cand_means = {c: min(1.0, round(base_means[c] + 0.05, 2)) for c in crit_ids}
                comp.success_delta = 0.2; comp.success_rate_a, comp.success_rate_b = 0.6, 0.8
                for c in comp.per_criterion:
                    c.significant = False
            elif r == 2:                     # sneaky epsilon regression
                cand_means[crit_ids[0]] = max(0.0, round(base_means[crit_ids[0]] - 0.1, 2))
                comp.success_delta = 0.2; comp.success_rate_a, comp.success_rate_b = 0.6, 0.8
                for c in comp.per_criterion:
                    c.significant = False

            base = _scorecard(base_means, rng.choice(COSTS), rng.choice(LATS))
            cand = _scorecard(cand_means, rng.choice(COSTS), rng.choice(LATS))

            gate_mod.compare_scorecards = lambda *a, _c=comp, **k: _c
            promote, reason = gate_mod.gate(cand, base, cfg)

            cases.append({
                "input": {
                    "comparison": _comp_to_json(comp),
                    "baselineMeans": base.per_criterion_means,
                    "candidateMeans": cand.per_criterion_means,
                    "baselineMeanCost": base.mean_cost_usd,
                    "candidateMeanCost": cand.mean_cost_usd,
                    "baselineP95": base.p95_latency_ms,
                    "candidateP95": cand.p95_latency_ms,
                    "cfg": cfg,
                },
                "expected": {"promote": promote, "reason": reason},
            })
    finally:
        gate_mod.compare_scorecards = orig
    return cases


# --------------------------------------------------------------------------- #
# drift  (real LiveMonitor.status via a stub registry)
# --------------------------------------------------------------------------- #
class _StubRegistry:
    def __init__(self, scores):
        self._scores = scores          # {criterion_id: [newest-first floats]}
        self.reeval: list[str] = []

    def live_scores(self, agent_id, criterion_id, last_n):
        return list(self._scores.get(criterion_id, []))[:last_n]

    def save_reeval_request(self, agent_id, message):
        self.reeval.append(message)


def _judge_crit(cid):
    return Criterion(criterion_id=cid, description=cid, scorer="judge",
                     scale="three_point", tags=["live"],
                     anchors={"pass": "good", "fail": "bad"})


def gen_drift(rng, n=100):
    cases = []
    for _ in range(n):
        k = rng.randint(1, 4)
        crit_ids = rng.sample(CRITS, k)
        window = rng.choice([5, 10, 20, 50])
        threshold = rng.choice([0.1, 0.15, 0.2])
        live = {c: [rng.choice([0.0, 0.5, 1.0]) for _ in range(rng.randint(1, window))]
                for c in crit_ids}
        base_means = {c: rng.choice(MEANS) for c in crit_ids}
        reg = _StubRegistry(live)
        mon = LiveMonitor(registry=reg, judge=None,
                          live_criteria=[_judge_crit(c) for c in crit_ids],
                          drift_threshold=threshold, window=window)
        base_sc = SimpleNamespace(per_criterion_means=base_means)
        status = mon.status("agent", base_sc)
        cases.append({
            "input": {"criteria": crit_ids, "liveScores": live,
                      "baselineMeans": base_means, "window": window,
                      "driftThreshold": threshold},
            "expected": {"perCriterionMean": status.per_criterion_mean,
                         "baselineMean": status.baseline_mean,
                         "drifted": status.drifted,
                         "reeval": reg.reeval,
                         "driftDetected": status.drift_detected},
        })
    return cases


# --------------------------------------------------------------------------- #
# escalation  (real _tool_policy + escalated_appropriately)
# --------------------------------------------------------------------------- #
def gen_escalation(rng, n=80):
    tools = ["issue_refund", "search_web", "read_file", "delete_record", "calculator"]
    levels = ["auto", "verify", "human_required"]
    resolver = AnthropicSimpleAgent.__new__(AnthropicSimpleAgent)  # bypass __init__
    cases = []
    for _ in range(n):
        default = rng.choice(levels)
        overrides = {t: rng.choice(levels) for t in rng.sample(tools, rng.randint(0, 3))}
        policy = {"default": default, "overrides": overrides}
        tool = rng.choice(tools)
        human_authorized = rng.random() < 0.4
        resolver.autonomy_policy = policy
        resolved = resolver._tool_policy(tool)
        escalate = (not human_authorized) and resolved == "human_required"
        cases.append({
            "kind": "escalation",
            "input": {"autonomyPolicy": policy, "tool": tool,
                      "humanAuthorized": human_authorized},
            "expected": {"policy": resolved, "escalate": escalate,
                         "question": f"Authorize {tool}?" if escalate else None},
        })
    # escalated_appropriately scorer
    for _ in range(n):
        tags = [t for t in ["should_escalate", "pii", "billing"] if rng.random() < 0.5]
        escalated = rng.random() < 0.5
        trace = SimpleNamespace(escalated=escalated)
        tc = SimpleNamespace(tags=tags)
        score = escalated_appropriately(trace, tc)
        cases.append({
            "kind": "score",
            "input": {"tags": tags, "escalated": escalated},
            "expected": {"score": score},
        })
    return cases


# --------------------------------------------------------------------------- #
# stats
# --------------------------------------------------------------------------- #
def gen_stats(rng, n=100):
    wilson, exact, alpha = [], [], []
    for _ in range(n):
        nn = rng.choice([0, 1, 3, 5, 10, 25, 100, 1000])
        passes = rng.randint(0, nn) if nn > 0 else 0
        low, high = wilson_interval(passes, nn)
        wilson.append({"input": {"passes": passes, "n": nn},
                       "expected": {"low": low, "high": high,
                                    "lower": wilson_lower_bound(passes, nn)}})
    scale3 = [0.0, 0.5, 1.0]
    for _ in range(n):
        m = rng.randint(2, 20)
        pairs = [[rng.choice([0.0, 1.0]), rng.choice([0.0, 1.0])] for _ in range(m)]
        exact.append({"input": {"pairs": pairs},
                      "expected": {"rate": exact_match_rate([tuple(p) for p in pairs])}})
    for _ in range(n):
        m = rng.randint(2, 24)
        pairs = [[rng.choice(scale3), rng.choice(scale3)] for _ in range(m)]
        alpha.append({"input": {"pairs": pairs},
                      "expected": {"alpha": krippendorff_alpha_interval([tuple(p) for p in pairs])}})
    return {"wilson": wilson, "exactMatch": exact, "alpha": alpha}


# --------------------------------------------------------------------------- #
def build_all(seed=20260718):
    rng = random.Random(seed)
    # fixed call order => deterministic streams per module
    return {
        "evaluate_candidate": gen_evaluate_candidate(rng),
        "gate": gen_gate(rng),
        "drift": gen_drift(rng),
        "escalation": gen_escalation(rng),
        "stats": gen_stats(rng),
    }


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    data = build_all()
    for name, cases in data.items():
        # NB: no sort_keys — the gate's epsilon-drop order follows dict
        # insertion order, which both Python json and JS JSON.parse preserve.
        (OUT / f"{name}.json").write_text(json.dumps(cases, indent=2) + "\n")
        count = len(cases) if isinstance(cases, list) else sum(len(v) for v in cases.values())
        print(f"  {name}.json — {count} cases")
    print(f"wrote {len(data)} fixtures to {OUT}")


if __name__ == "__main__":
    main()
