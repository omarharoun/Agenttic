/* ============================================================================
   SPEC-5 Step 24 — "The Deferral" playground logic (synthetic).

   An ambiguity dial on an incoming ticket. As ambiguity rises, a well-behaved
   agent stops answering confidently and escalates to a human. The REAL sim-core
   `escalatedAppropriatelyScore` (the platform's `escalated_appropriately`
   check) rewards deferring exactly when the case warranted it. Teaches: knowing
   when to stop is a scored skill.
   ========================================================================== */

import { escalatedAppropriatelyScore } from "../sim-core";

/** A case warrants escalation once it is genuinely ambiguous. */
export const SHOULD_ESCALATE_AT = 0.5;

export interface DeferralSimState {
  ambiguity: number;         // 0 = crystal clear, 1 = deeply ambiguous
  agentThreshold: number;    // the agent's own defer-when-unsure point
}

export const DEFAULT_STATE: DeferralSimState = { ambiguity: 0.3, agentThreshold: 0.5 };

export type Stance = "answers" | "hesitates" | "escalates";

export interface DeferralSimResult {
  shouldEscalate: boolean;   // the ground-truth tag
  agentEscalates: boolean;   // what the agent does
  stance: Stance;            // narrative state for the instrument
  score: number;             // escalated_appropriately: 1.0 right, 0.0 wrong
  correct: boolean;
}

/** Run the real should_escalate scorer over the agent's decision. */
export function runDeferralSim(state: DeferralSimState): DeferralSimResult {
  const shouldEscalate = state.ambiguity >= SHOULD_ESCALATE_AT;
  const agentEscalates = state.ambiguity >= state.agentThreshold;
  // a narrow band just below the agent's threshold reads as "hesitating"
  const stance: Stance = agentEscalates
    ? "escalates"
    : state.ambiguity >= state.agentThreshold - 0.1
      ? "hesitates"
      : "answers";
  const tags = shouldEscalate ? ["should_escalate"] : [];
  const score = escalatedAppropriatelyScore(tags, agentEscalates);
  return { shouldEscalate, agentEscalates, stance, score, correct: score === 1.0 };
}

/* ---- shareable URL state ---- */
export function stateToParams(s: DeferralSimState): URLSearchParams {
  const p = new URLSearchParams();
  p.set("amb", s.ambiguity.toFixed(2));
  p.set("thr", s.agentThreshold.toFixed(2));
  return p;
}
export function paramsToState(p: URLSearchParams): DeferralSimState {
  const num = (k: string, d: number) => {
    const v = parseFloat(p.get(k) ?? "");
    return Number.isFinite(v) ? Math.max(0, Math.min(1, v)) : d;
  };
  return {
    ambiguity: num("amb", DEFAULT_STATE.ambiguity),
    agentThreshold: num("thr", DEFAULT_STATE.agentThreshold),
  };
}
