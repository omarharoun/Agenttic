/* ============================================================================
   SPEC-5 Step 24 — "Drift Watch" playground logic (synthetic).

   A degradation dial thins the passing responses in a rolling window; the REAL,
   parity-proven sim-core `driftStatus` decides whether the window mean has
   fallen far enough below the batch baseline to fire a re-evaluation. Teaches
   why single bad responses don't page anyone but a trend does.
   ========================================================================== */

import { driftStatus, type DriftStatus } from "../sim-core";

export const CRITERION = "helpfulness";
export const BASELINE = 0.9;
export const THRESHOLD = 0.15;
export const WINDOW = 20;

export interface DriftSimState {
  degradation: number; // 0 = healthy, 1 = fully degraded
}

export const DEFAULT_STATE: DriftSimState = { degradation: 0.15 };

/** Build the window of {0,1} responses for the current degradation. Higher
 *  degradation => fewer passes => lower rolling mean. Deterministic so the strip
 *  and the verdict are reproducible from the slider (and the URL). */
export function windowFor(state: DriftSimState): number[] {
  const passes = Math.round((1 - state.degradation) * WINDOW);
  return [
    ...Array<number>(passes).fill(1),
    ...Array<number>(WINDOW - passes).fill(0),
  ];
}

export interface DriftSimResult extends DriftStatus {
  window: number;
  mean: number;
  fired: boolean;
  reason: string | null;
}

/** Run the real sim-core drift decision over the synthetic window. */
export function runDriftSim(state: DriftSimState): DriftSimResult {
  const scores = windowFor(state);
  const status = driftStatus({
    criteria: [CRITERION],
    liveScores: { [CRITERION]: scores },
    baselineMeans: { [CRITERION]: BASELINE },
    window: WINDOW,
    driftThreshold: THRESHOLD,
  });
  const mean = status.perCriterionMean[CRITERION] ?? 0;
  return {
    ...status,
    mean,
    fired: status.driftDetected,
    reason: status.reeval[0] ?? null,
  };
}

/* ---- shareable URL state ---- */
export function stateToParams(s: DriftSimState): URLSearchParams {
  const p = new URLSearchParams();
  p.set("deg", s.degradation.toFixed(2));
  return p;
}
export function paramsToState(p: URLSearchParams): DriftSimState {
  const v = parseFloat(p.get("deg") ?? "");
  return { degradation: Number.isFinite(v) ? Math.max(0, Math.min(1, v)) : DEFAULT_STATE.degradation };
}
