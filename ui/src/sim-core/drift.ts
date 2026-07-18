/* ============================================================================
   Drift detection — a Python-faithful port of `LiveMonitor.status` in
   src/agenttic/live/monitor.py (Step 9).

   Rolling live means vs the batch baseline: a criterion drifts when its live
   mean drops MORE than the threshold below its baseline. The re-eval reason
   string is reproduced byte-for-byte (Hard Rule 24).
   ========================================================================== */

import { pyFixed, pyRepr, pyStr } from "./pyfmt";

/** Defaults from config.yaml `live.*` (monitor.py __init__). */
export const DEFAULT_DRIFT_THRESHOLD = 0.15;
export const DEFAULT_DRIFT_WINDOW = 50;

export interface DriftInput {
  /** Ordered live criteria (matches LiveMonitor.live_criteria order). */
  criteria: string[];
  /** Per-criterion live scores already limited to the window (newest-first,
   *  as the registry returns them). Mean is over whatever is present. */
  liveScores: Record<string, number[]>;
  /** Batch baseline per-criterion means (Scorecard.per_criterion_means). */
  baselineMeans: Record<string, number>;
  window?: number;
  driftThreshold?: number;
}

export interface DriftStatus {
  window: number;
  perCriterionMean: Record<string, number>;
  baselineMean: Record<string, number>;
  drifted: string[];
  /** One re-eval request string per drifted criterion, in drifted order. */
  reeval: string[];
  driftDetected: boolean;
}

/** Compare rolling live means against the batch baseline; produce a re-eval
 *  request for every drifted criterion. Mirrors `LiveMonitor.status`. */
export function driftStatus(input: DriftInput): DriftStatus {
  const window = input.window ?? DEFAULT_DRIFT_WINDOW;
  const threshold = input.driftThreshold ?? DEFAULT_DRIFT_THRESHOLD;
  const base = input.baselineMeans;

  const means: Record<string, number> = {};
  const baselineMean: Record<string, number> = {};
  const drifted: string[] = [];

  for (const cid of input.criteria) {
    const scores = input.liveScores[cid];
    if (!scores || scores.length === 0 || !(cid in base)) continue;
    const mean = scores.reduce((a, b) => a + b, 0) / scores.length;
    means[cid] = mean;
    baselineMean[cid] = base[cid];
    if (base[cid] - mean > threshold) drifted.push(cid);
  }

  const reeval = drifted.map(
    (cid) =>
      `criterion ${pyRepr(cid)} live mean ${pyFixed(means[cid], 2)} dropped more than ` +
      `${pyStr(threshold)} below batch baseline ${pyFixed(base[cid], 2)} ` +
      `(window=${window}) — batch re-evaluation recommended`,
  );

  return {
    window,
    perCriterionMean: means,
    baselineMean,
    drifted,
    reeval,
    driftDetected: drifted.length > 0,
  };
}
