/* ============================================================================
   Scorecard what-if — a Python-faithful port of `overall_pass` in
   src/agenttic/scoring/engine.py (SPEC-5 23.2).

   The console what-if instrument lets an operator retune criterion weights and
   the pass threshold and watch pass/fail per case, the success rate, and its
   Wilson interval recompute instantly. This is the exact rule the server uses,
   proven identical by the golden parity harness — so "hypothetical" numbers are
   real, just unsaved.
   ========================================================================== */

import { wilsonInterval } from "./stats";

export const DEFAULT_PASS_THRESHOLD = 0.7;

/** Weighted mean of criterion scores vs the pass threshold. Mirrors
 *  `agenttic.scoring.engine.overall_pass`: weighted = Σ(score·w)/Σ(w). Only the
 *  criteria present in `scores` participate (weights are looked up by id). */
export function overallPass(
  scores: Record<string, number>,
  weights: Record<string, number>,
  passThreshold = DEFAULT_PASS_THRESHOLD,
): { weighted: number; passed: boolean } {
  const ids = Object.keys(scores);
  const totalWeight = ids.reduce((acc, cid) => acc + weights[cid], 0);
  const weighted = ids.reduce((acc, cid) => acc + scores[cid] * weights[cid], 0) / totalWeight;
  return { weighted, passed: weighted >= passThreshold };
}

export interface WhatIfRun {
  testId: string;
  scores: Record<string, number>;   // criterion_id -> score
  scoringError?: boolean;           // excluded from the rate, like the server
}

export interface WhatIfResult {
  perCase: { testId: string; weighted: number; passed: boolean }[];
  nPassed: number;
  nScored: number;
  successRate: number;
  wilsonLow: number;
  wilsonHigh: number;
}

/** Recompute a whole scorecard under hypothetical weights + threshold. Mirrors
 *  Scorecard.aggregate's success rate (passed runs / scored runs) and the
 *  Wilson interval over that. */
export function recomputeScorecard(
  runs: WhatIfRun[],
  weights: Record<string, number>,
  passThreshold = DEFAULT_PASS_THRESHOLD,
): WhatIfResult {
  const perCase = runs.map((r) => {
    const { weighted, passed } = overallPass(r.scores, weights, passThreshold);
    return { testId: r.testId, weighted, passed };
  });
  const scored = runs.filter((r) => !r.scoringError);
  const nScored = scored.length;
  const nPassed = scored.filter((r) => overallPass(r.scores, weights, passThreshold).passed).length;
  const successRate = nScored ? nPassed / nScored : 0.0;
  const [wilsonLow, wilsonHigh] = wilsonInterval(nPassed, nScored);
  return { perCase, nPassed, nScored, successRate, wilsonLow, wilsonHigh };
}
