import { describe, it, expect } from "vitest";
import type { Scorecard } from "./api";
import { buildGateInputFromScorecards } from "./components/GateReplay";
import { gateSteps } from "./sim-core";

/* SPEC-5 23.3 — the lineage replay re-derives the gate from two stored
   scorecards through sim-core. Proves the re-derivation reaches the right
   verdict and stops at the deciding condition (incl. a rejected sibling). */

function sc(means: Record<string, number>, rate: number, extra: Partial<Scorecard> = {}): Scorecard {
  return {
    scorecard_id: "sc", agent_id: "a", suite_id: "s", suite_version: 1,
    rubric_id: "r", rubric_version: 1, run_scores: [], task_success_rate: rate,
    mean_cost_usd: 0.02, total_cost_usd: 0, total_scoring_cost_usd: 0, p95_latency_ms: 800,
    per_criterion_means: means, errored_test_ids: [], visibility_tier: "glass_box",
    created_at: "", n_scored: 20, n_passed: Math.round(rate * 20),
    success_wilson_low: 0, success_wilson_high: 0, ...extra,
  };
}

const base = sc({ tone: 0.9, accuracy: 0.85, safety: 0.95 }, 0.6);

describe("GateReplay re-derivation", () => {
  it("re-derives a clean-win promotion and ends on the verdict step", () => {
    const cand = sc({ tone: 0.95, accuracy: 0.92, safety: 0.97 }, 0.8);
    const { steps, result } = gateSteps(buildGateInputFromScorecards(cand, base));
    expect(result.promote).toBe(true);
    expect(steps[steps.length - 1].key).toBe("verdict");
    expect(steps.every((s) => s.ok)).toBe(true);
  });

  it("replays a rejected sibling to its exact failing condition and stops (fail-closed)", () => {
    const cand = sc({ tone: 0.95, accuracy: 0.92 /* safety dropped */ }, 0.8);
    const { steps, result } = gateSteps(buildGateInputFromScorecards(cand, base));
    expect(result.promote).toBe(false);
    // stops at the missing-criteria step; nothing after it
    const last = steps[steps.length - 1];
    expect(last.key).toBe("missing");
    expect(last.ok).toBe(false);
  });

  it("catches a sub-significant epsilon regression from the scorecards", () => {
    const cand = sc({ tone: 0.95, accuracy: 0.92, safety: 0.7 }, 0.8);
    const { steps, result } = gateSteps(buildGateInputFromScorecards(cand, base));
    expect(result.promote).toBe(false);
    expect(steps[steps.length - 1].key).toBe("epsilon");
  });
});
