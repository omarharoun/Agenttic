/* ============================================================================
   Promotion gate — a Python-faithful port of `gate` in
   src/agenttic/learning/optimizer.py + `evaluate_candidate` in
   src/agenttic/optimizer.py (Step 14).

   The A/B *comparison* (paired-bootstrap deltas + significance) is computed
   server-side and is a statistical, RNG-bearing object — so it is an INPUT
   here, never recomputed. Given the comparison, every branch and every reason
   string is deterministic and reproduced byte-for-byte. The parity harness
   feeds the real Python `gate()` an injected comparison and asserts equality.
   ========================================================================== */

import { pyFixed, pyFixedSigned, pyG, pyListRepr, pyPct, pyPctSigned } from "./pyfmt";

/** Defaults from optimizer.py (config.yaml `learning.*`). */
export const DEFAULT_EPSILON = 0.02;
export const DEFAULT_MAX_COST_MULT = 2.0;
export const DEFAULT_MAX_LATENCY_MULT = 2.0;

/** One per-criterion A-vs-B delta with its paired-bootstrap verdict. Only the
 *  fields `evaluate_candidate` reads are required. */
export interface CriterionComparison {
  criterionId: string;
  delta: number;        // mean_b - mean_a (>0 favours candidate)
  significant: boolean;
}

/** The paired A/B comparison consumed by the gate (computed upstream). */
export interface Comparison {
  successRateA: number;
  successRateB: number;
  successDelta: number; // rate_b - rate_a
  nPaired: number;
  perCriterion: CriterionComparison[];
}

export interface EvaluateResult {
  accept: boolean;
  regressions: string[];   // criterion ids that significantly regressed
  reason: string;
}

export interface LearningConfig {
  epsilon?: number;
  max_cost_multiplier?: number;
  max_latency_multiplier?: number;
}

export interface GateInput {
  comparison: Comparison;
  baselineMeans: Record<string, number>;
  candidateMeans: Record<string, number>;
  baselineMeanCost: number;
  candidateMeanCost: number;
  baselineP95: number;
  candidateP95: number;
}

export interface GateResult {
  promote: boolean;
  reason: string;
}

/** Mirrors `agenttic.optimizer.evaluate_candidate`: strict pass-rate
 *  improvement with a significant-regression veto. */
export function evaluateCandidate(c: Comparison): EvaluateResult {
  const regressions = c.perCriterion
    .filter((x) => x.significant && x.delta < 0)
    .map((x) => x.criterionId);
  const delta = c.successDelta;
  if (regressions.length > 0) {
    const names = regressions.join(", ");
    return {
      accept: false,
      regressions,
      reason: `rejected: would significantly regress ${names} (net pass-rate delta ${pyPctSigned(delta)})`,
    };
  }
  if (delta > 0) {
    return {
      accept: true,
      regressions: [],
      reason:
        `accepted: pass rate ${pyPct(c.successRateA)} → ${pyPct(c.successRateB)} ` +
        `(+${pyPct(delta)}) on ${c.nPaired} train case(s), no criterion regressed`,
    };
  }
  if (delta === 0) {
    return {
      accept: false,
      regressions: [],
      reason: `rejected: no pass-rate improvement (tied at ${pyPct(c.successRateB)} on ${c.nPaired} case(s))`,
    };
  }
  return {
    accept: false,
    regressions: [],
    reason: `rejected: pass rate dropped ${pyPct(delta)} on ${c.nPaired} case(s)`,
  };
}

/** Mirrors `agenttic.learning.optimizer.gate`: the four-rule promotion gate.
 *  Rule 1 is `evaluateCandidate` over the supplied comparison; rules 2–4 are
 *  the deterministic missing-criteria / epsilon / cost / latency floors. */
export function gate(input: GateInput, cfg: { learning?: LearningConfig } = {}): GateResult {
  const lc = cfg.learning ?? {};
  const epsilon = Number(lc.epsilon ?? DEFAULT_EPSILON);
  const maxCostMult = Number(lc.max_cost_multiplier ?? DEFAULT_MAX_COST_MULT);
  const maxLatMult = Number(lc.max_latency_multiplier ?? DEFAULT_MAX_LATENCY_MULT);

  const ev = evaluateCandidate(input.comparison);
  if (!ev.accept) return { promote: false, reason: ev.reason };

  const base = input.baselineMeans;
  const cand = input.candidateMeans;

  // (2.5) missing-criteria fail-closed (sorted list, before epsilon)
  const missing = Object.keys(base).filter((c) => !(c in cand)).sort();
  if (missing.length > 0) {
    return {
      promote: false,
      reason:
        `rejected: candidate scorecard is missing baseline criteria ${pyListRepr(missing)} ` +
        `— unpaired criteria cannot be verified as non-regressing`,
    };
  }

  // (2) epsilon floor
  const epsDrops: [string, number][] = [];
  for (const cid of Object.keys(base)) {
    if (cid in cand && cand[cid] - base[cid] < -epsilon) {
      epsDrops.push([cid, cand[cid] - base[cid]]);
    }
  }
  if (epsDrops.length > 0) {
    const worst = epsDrops.map(([c, d]) => `${c} (${pyFixedSigned(d, 2)})`).join(", ");
    return {
      promote: false,
      reason: `rejected: per-criterion mean dropped beyond epsilon=${pyFixed(epsilon, 2)} on ${worst}`,
    };
  }

  // (3) cost budget
  if (input.baselineMeanCost > 0 && maxCostMult > 0) {
    if (input.candidateMeanCost > input.baselineMeanCost * maxCostMult) {
      return {
        promote: false,
        reason:
          `rejected: mean cost ${pyFixed(input.candidateMeanCost, 4)} exceeds ` +
          `${pyG(maxCostMult)}x baseline ${pyFixed(input.baselineMeanCost, 4)}`,
      };
    }
  }

  // (4) latency budget
  if (input.baselineP95 > 0 && maxLatMult > 0) {
    if (input.candidateP95 > input.baselineP95 * maxLatMult) {
      return {
        promote: false,
        reason:
          `rejected: p95 latency ${pyFixed(input.candidateP95, 0)}ms exceeds ` +
          `${pyG(maxLatMult)}x baseline ${pyFixed(input.baselineP95, 0)}ms`,
      };
    }
  }

  return { promote: true, reason: ev.reason };
}

export interface GateStep {
  key: "improvement" | "missing" | "epsilon" | "cost" | "latency" | "verdict";
  label: string;
  ok: boolean;
  note: string;
}

/** The gate, re-derived as an ordered sequence of conditions — for the console
 *  lineage "replay" (SPEC-5 23.3). Same logic and same short-circuit order as
 *  `gate`; stops at the first failing condition (as production does). The final
 *  `result` is guaranteed equal to `gate(input, cfg)` (asserted in the parity
 *  suite over every gate fixture), so the replay is a re-derivation, not a
 *  recording. */
export function gateSteps(
  input: GateInput,
  cfg: { learning?: LearningConfig } = {},
): { steps: GateStep[]; result: GateResult } {
  const lc = cfg.learning ?? {};
  const epsilon = Number(lc.epsilon ?? DEFAULT_EPSILON);
  const maxCostMult = Number(lc.max_cost_multiplier ?? DEFAULT_MAX_COST_MULT);
  const maxLatMult = Number(lc.max_latency_multiplier ?? DEFAULT_MAX_LATENCY_MULT);
  const steps: GateStep[] = [];

  const ev = evaluateCandidate(input.comparison);
  steps.push({
    key: "improvement", label: "Strict pass-rate improvement",
    ok: ev.accept, note: ev.reason,
  });
  if (!ev.accept) return { steps, result: { promote: false, reason: ev.reason } };

  const base = input.baselineMeans;
  const cand = input.candidateMeans;

  const missing = Object.keys(base).filter((c) => !(c in cand)).sort();
  const missOk = missing.length === 0;
  steps.push({
    key: "missing", label: "Every baseline criterion still scored",
    ok: missOk,
    note: missOk ? "no criteria dropped" : `missing ${pyListRepr(missing)} — fail closed`,
  });
  if (!missOk) {
    return {
      steps,
      result: {
        promote: false,
        reason:
          `rejected: candidate scorecard is missing baseline criteria ${pyListRepr(missing)} ` +
          `— unpaired criteria cannot be verified as non-regressing`,
      },
    };
  }

  const epsDrops: [string, number][] = [];
  for (const cid of Object.keys(base)) {
    if (cid in cand && cand[cid] - base[cid] < -epsilon) epsDrops.push([cid, cand[cid] - base[cid]]);
  }
  const epsOk = epsDrops.length === 0;
  steps.push({
    key: "epsilon", label: `No per-criterion mean drops beyond ε=${pyFixed(epsilon, 2)}`,
    ok: epsOk,
    note: epsOk
      ? "all criteria within tolerance"
      : epsDrops.map(([c, d]) => `${c} (${pyFixedSigned(d, 2)})`).join(", "),
  });
  if (!epsOk) {
    const worst = epsDrops.map(([c, d]) => `${c} (${pyFixedSigned(d, 2)})`).join(", ");
    return {
      steps,
      result: {
        promote: false,
        reason: `rejected: per-criterion mean dropped beyond epsilon=${pyFixed(epsilon, 2)} on ${worst}`,
      },
    };
  }

  const costChecked = input.baselineMeanCost > 0 && maxCostMult > 0;
  const costOk = !costChecked || input.candidateMeanCost <= input.baselineMeanCost * maxCostMult;
  steps.push({
    key: "cost", label: `Mean cost within ${pyG(maxCostMult)}× baseline`,
    ok: costOk,
    note: costChecked
      ? `${pyFixed(input.candidateMeanCost, 4)} vs ${pyFixed(input.baselineMeanCost, 4)}`
      : "no cost baseline",
  });
  if (!costOk) {
    return {
      steps,
      result: {
        promote: false,
        reason:
          `rejected: mean cost ${pyFixed(input.candidateMeanCost, 4)} exceeds ` +
          `${pyG(maxCostMult)}x baseline ${pyFixed(input.baselineMeanCost, 4)}`,
      },
    };
  }

  const latChecked = input.baselineP95 > 0 && maxLatMult > 0;
  const latOk = !latChecked || input.candidateP95 <= input.baselineP95 * maxLatMult;
  steps.push({
    key: "latency", label: `p95 latency within ${pyG(maxLatMult)}× baseline`,
    ok: latOk,
    note: latChecked
      ? `${pyFixed(input.candidateP95, 0)}ms vs ${pyFixed(input.baselineP95, 0)}ms`
      : "no latency baseline",
  });
  if (!latOk) {
    return {
      steps,
      result: {
        promote: false,
        reason:
          `rejected: p95 latency ${pyFixed(input.candidateP95, 0)}ms exceeds ` +
          `${pyG(maxLatMult)}x baseline ${pyFixed(input.baselineP95, 0)}ms`,
      },
    };
  }

  steps.push({ key: "verdict", label: "Promote", ok: true, note: ev.reason });
  return { steps, result: { promote: true, reason: ev.reason } };
}
