/* ============================================================================
   SPEC-5 Step 24 — "The Gate" playground logic (synthetic, clearly labelled).

   Maps the sim's slider state onto a sim-core GateInput and runs the REAL,
   parity-proven gate. The verdict + receipt a visitor sees here is byte-for-byte
   what production would emit — that is the whole point (Hard Rule 24/26).

   Kept pure and console-free so it unit-tests in isolation and the page stays a
   thin view over it.
   ========================================================================== */

import { gate, type Comparison, type GateInput, type GateResult } from "../sim-core";

/** A fixed, synthetic baseline the candidate is judged against. */
export const BASELINE_MEANS: Record<string, number> = { tone: 0.9, accuracy: 0.85, safety: 0.95 };
export const BASELINE_SUCCESS = 0.6;
export const CRITERIA = ["tone", "accuracy", "safety"] as const;
export type CriterionId = (typeof CRITERIA)[number];

export interface GateSimState {
  candidate: Record<string, number>;   // candidate per-criterion means
  successRateB: number;                 // candidate success rate (baseline is fixed)
  dropSafety: boolean;                  // "lobotomy": stop scoring safety entirely
}

const round2 = (x: number) => Math.round(x * 100) / 100;

/** Build the sim-core GateInput from the sim state. Cost/latency are held at
 *  baseline so the sim tells the criterion story, not the budget one. */
export function buildGateInput(state: GateSimState): GateInput {
  const candidate: Record<string, number> = { ...state.candidate };
  if (state.dropSafety) delete candidate.safety;

  const perCriterion = Object.keys(candidate).map((cid) => ({
    criterionId: cid,
    delta: round2(candidate[cid] - BASELINE_MEANS[cid]),
    significant: false,
  }));
  const comparison: Comparison = {
    successRateA: BASELINE_SUCCESS,
    successRateB: state.successRateB,
    successDelta: round2(state.successRateB - BASELINE_SUCCESS),
    nPaired: 20,
    perCriterion,
  };
  return {
    comparison,
    baselineMeans: BASELINE_MEANS,
    candidateMeans: candidate,
    baselineMeanCost: 0.02,
    candidateMeanCost: 0.02,
    baselineP95: 800,
    candidateP95: 800,
  };
}

/** Run the real gate over the sim state. */
export function runGateSim(state: GateSimState): GateResult {
  return gate(buildGateInput(state));
}

export interface Preset {
  id: string;
  label: string;
  blurb: string;
  state: GateSimState;
}

/** The three instructive cases from the spec. Each teaches why its verdict is
 *  the point. */
export const PRESETS: Preset[] = [
  {
    id: "clean",
    label: "The clean win",
    blurb: "Every criterion up, pass rate up. This is what promotion should look like.",
    state: { candidate: { tone: 0.95, accuracy: 0.92, safety: 0.97 }, successRateB: 0.8, dropSafety: false },
  },
  {
    id: "sneaky",
    label: "The sneaky regression",
    blurb: "Aggregate pass rate rises, but safety quietly drops. The per-criterion floor catches what the headline hides.",
    state: { candidate: { tone: 0.95, accuracy: 0.92, safety: 0.7 }, successRateB: 0.8, dropSafety: false },
  },
  {
    id: "lobotomy",
    label: "The lobotomy",
    blurb: "The candidate simply stops being scored on safety. An unpaired criterion can't be verified — so the gate fails closed.",
    state: { candidate: { tone: 0.95, accuracy: 0.92, safety: 0.95 }, successRateB: 0.8, dropSafety: true },
  },
];

export const DEFAULT_STATE: GateSimState = PRESETS[0].state;

/* ---- shareable URL state --------------------------------------------------- */

const KEYS: Record<string, CriterionId> = { tone: "tone", acc: "accuracy", saf: "safety" };

/** Serialise sim state to URLSearchParams (shareable demos). */
export function stateToParams(s: GateSimState): URLSearchParams {
  const p = new URLSearchParams();
  p.set("tone", String(s.candidate.tone));
  p.set("acc", String(s.candidate.accuracy));
  p.set("saf", String(s.candidate.safety));
  p.set("rate", String(s.successRateB));
  if (s.dropSafety) p.set("lob", "1");
  return p;
}

const clamp01 = (x: number) => Math.max(0, Math.min(1, x));

/** Parse sim state from URLSearchParams, falling back to the default preset for
 *  any missing/invalid field. */
export function paramsToState(p: URLSearchParams): GateSimState {
  const num = (k: string, fallback: number) => {
    const v = parseFloat(p.get(k) ?? "");
    return Number.isFinite(v) ? clamp01(v) : fallback;
  };
  const d = DEFAULT_STATE;
  return {
    candidate: {
      tone: num("tone", d.candidate.tone),
      accuracy: num("acc", d.candidate.accuracy),
      safety: num("saf", d.candidate.safety),
    },
    successRateB: num("rate", d.successRateB),
    dropSafety: p.get("lob") === "1",
  };
}

export { KEYS };
