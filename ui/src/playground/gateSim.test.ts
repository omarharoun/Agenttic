import { describe, it, expect } from "vitest";
import {
  runGateSim, PRESETS, stateToParams, paramsToState, DEFAULT_STATE,
} from "./gateSim";

const byId = (id: string) => PRESETS.find((p) => p.id === id)!.state;

describe("Gate playground — preset verdicts (real sim-core)", () => {
  it("the clean win promotes with the production accepted receipt", () => {
    const r = runGateSim(byId("clean"));
    expect(r.promote).toBe(true);
    expect(r.reason).toBe(
      "accepted: pass rate 60% → 80% (+20%) on 20 train case(s), no criterion regressed",
    );
  });

  it("the sneaky regression is caught by the per-criterion epsilon floor", () => {
    const r = runGateSim(byId("sneaky"));
    expect(r.promote).toBe(false);
    expect(r.reason).toBe(
      "rejected: per-criterion mean dropped beyond epsilon=0.02 on safety (-0.25)",
    );
  });

  it("the lobotomy fails closed with the exact production reason string", () => {
    const r = runGateSim(byId("lobotomy"));
    expect(r.promote).toBe(false);
    expect(r.reason).toBe(
      "rejected: candidate scorecard is missing baseline criteria ['safety'] " +
        "— unpaired criteria cannot be verified as non-regressing",
    );
  });
});

describe("Gate playground — URL state round-trips", () => {
  it("serialises and parses back to the same state", () => {
    for (const p of PRESETS) {
      const parsed = paramsToState(stateToParams(p.state));
      expect(parsed.candidate.tone).toBeCloseTo(p.state.candidate.tone, 10);
      expect(parsed.candidate.safety).toBeCloseTo(p.state.candidate.safety, 10);
      expect(parsed.successRateB).toBeCloseTo(p.state.successRateB, 10);
      expect(parsed.dropSafety).toBe(p.state.dropSafety);
    }
  });

  it("falls back to defaults for missing/garbage params", () => {
    const s = paramsToState(new URLSearchParams("tone=abc&saf=9"));
    expect(s.candidate.tone).toBe(DEFAULT_STATE.candidate.tone); // garbage -> default
    expect(s.candidate.safety).toBe(1);                          // 9 clamped to 1
  });
});
