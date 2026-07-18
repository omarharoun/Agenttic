import { describe, it, expect } from "vitest";
import {
  runDriftSim, paramsToState as driftParams, stateToParams as driftToParams, THRESHOLD, BASELINE,
} from "./driftSim";
import {
  runDeferralSim, paramsToState as defParams, stateToParams as defToParams, SHOULD_ESCALATE_AT,
} from "./deferralSim";

describe("Drift Watch — real sim-core drift", () => {
  it("absorbs mild degradation without firing", () => {
    const r = runDriftSim({ degradation: 0.1 }); // mean 0.90, still >= baseline-threshold
    expect(r.fired).toBe(false);
    expect(r.reason).toBeNull();
  });

  it("fires a re-eval once the window mean falls past the threshold", () => {
    const r = runDriftSim({ degradation: 0.4 }); // mean ~0.60, base-mean 0.30 > 0.15
    expect(r.fired).toBe(true);
    expect(r.reason).toContain("batch re-evaluation recommended");
    expect(BASELINE - r.mean).toBeGreaterThan(THRESHOLD);
  });

  it("URL state round-trips", () => {
    expect(driftParams(driftToParams({ degradation: 0.37 })).degradation).toBeCloseTo(0.37, 6);
  });
});

describe("The Deferral — real should_escalate scorer", () => {
  it("rewards answering a clear ticket", () => {
    const r = runDeferralSim({ ambiguity: 0.2, agentThreshold: 0.5 });
    expect(r.shouldEscalate).toBe(false);
    expect(r.stance).toBe("answers");
    expect(r.score).toBe(1.0);
  });

  it("rewards escalating a genuinely ambiguous ticket", () => {
    const r = runDeferralSim({ ambiguity: 0.8, agentThreshold: 0.5 });
    expect(r.shouldEscalate).toBe(true);
    expect(r.agentEscalates).toBe(true);
    expect(r.stance).toBe("escalates");
    expect(r.score).toBe(1.0);
  });

  it("penalises acting confidently when it should have deferred", () => {
    // ambiguous case (>= 0.5), but the agent's threshold is too high to defer
    const r = runDeferralSim({ ambiguity: 0.6, agentThreshold: 0.9 });
    expect(r.shouldEscalate).toBe(true);
    expect(r.agentEscalates).toBe(false);
    expect(r.score).toBe(0.0);
    expect(r.correct).toBe(false);
  });

  it("SHOULD_ESCALATE_AT is the ground-truth boundary; URL round-trips", () => {
    expect(runDeferralSim({ ambiguity: SHOULD_ESCALATE_AT, agentThreshold: 0.5 }).shouldEscalate).toBe(true);
    const s = defParams(defToParams({ ambiguity: 0.7, agentThreshold: 0.4 }));
    expect(s.ambiguity).toBeCloseTo(0.7, 6);
    expect(s.agentThreshold).toBeCloseTo(0.4, 6);
  });
});
