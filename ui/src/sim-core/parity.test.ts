/* ============================================================================
   SPEC-5 Step 22 — golden parity harness (TS side).

   Replays every fixture the Python engine produced (fixtures/sim-parity/*.json,
   written by scripts/gen_sim_parity.py) through sim-core and asserts equality:
   reason strings byte-for-byte, numbers to 1e-9. If either implementation
   drifts, this fails. The toy IS the machine (Hard Rule 24).
   ========================================================================== */

import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, it, expect } from "vitest";
import {
  gate, evaluateCandidate, driftStatus, shouldEscalate,
  escalatedAppropriatelyScore, wilsonInterval, wilsonLowerBound,
  exactMatchRate, krippendorffAlphaInterval, recomputeScorecard,
} from "./index";

function load(name: string): any {
  const url = new URL(`../../../fixtures/sim-parity/${name}.json`, import.meta.url);
  return JSON.parse(readFileSync(fileURLToPath(url), "utf-8"));
}

const close = (a: number, b: number) => expect(Math.abs(a - b)).toBeLessThan(1e-9);
const closeMap = (a: Record<string, number>, b: Record<string, number>) => {
  expect(Object.keys(a).sort()).toEqual(Object.keys(b).sort());
  for (const k of Object.keys(a)) close(a[k], b[k]);
};

describe("sim-core parity — evaluate_candidate", () => {
  const cases = load("evaluate_candidate");
  it(`replays ${cases.length} cases`, () => {
    for (const c of cases) {
      const r = evaluateCandidate(c.input);
      expect(r.accept).toBe(c.expected.accept);
      expect(r.reason).toBe(c.expected.reason);      // byte-for-byte
      expect(r.regressions).toEqual(c.expected.regressions);
    }
  });
});

describe("sim-core parity — gate", () => {
  const cases = load("gate");
  it(`replays ${cases.length} cases`, () => {
    for (const c of cases) {
      const { cfg, ...rest } = c.input;
      const r = gate(rest, cfg);
      expect(r.promote).toBe(c.expected.promote);
      expect(r.reason).toBe(c.expected.reason);      // byte-for-byte
    }
  });
});

describe("sim-core parity — drift", () => {
  const cases = load("drift");
  it(`replays ${cases.length} cases`, () => {
    for (const c of cases) {
      const r = driftStatus(c.input);
      closeMap(r.perCriterionMean, c.expected.perCriterionMean);
      closeMap(r.baselineMean, c.expected.baselineMean);
      expect(r.drifted).toEqual(c.expected.drifted);
      expect(r.reeval).toEqual(c.expected.reeval);   // byte-for-byte strings
      expect(r.driftDetected).toBe(c.expected.driftDetected);
    }
  });
});

describe("sim-core parity — escalation", () => {
  const cases = load("escalation");
  it(`replays ${cases.length} cases`, () => {
    for (const c of cases) {
      if (c.kind === "escalation") {
        const r = shouldEscalate(c.input.autonomyPolicy, c.input.tool, c.input.humanAuthorized);
        expect(r.policy).toBe(c.expected.policy);
        expect(r.escalate).toBe(c.expected.escalate);
        expect(r.question).toBe(c.expected.question);
      } else {
        const s = escalatedAppropriatelyScore(c.input.tags, c.input.escalated);
        expect(s).toBe(c.expected.score);
      }
    }
  });
});

describe("sim-core parity — what-if (scorecard recompute)", () => {
  const cases = load("whatif");
  it(`replays ${cases.length} cases`, () => {
    for (const c of cases) {
      const r = recomputeScorecard(c.input.runs, c.input.weights, c.input.passThreshold);
      expect(r.nPassed).toBe(c.expected.nPassed);
      expect(r.nScored).toBe(c.expected.nScored);
      close(r.successRate, c.expected.successRate);
      close(r.wilsonLow, c.expected.wilsonLow);
      close(r.wilsonHigh, c.expected.wilsonHigh);
      expect(r.perCase.length).toBe(c.expected.perCase.length);
      r.perCase.forEach((pc, i) => {
        expect(pc.passed).toBe(c.expected.perCase[i].passed);   // server-identical
        close(pc.weighted, c.expected.perCase[i].weighted);
      });
    }
  });
});

describe("sim-core parity — stats", () => {
  const data = load("stats");
  it(`replays ${data.wilson.length} Wilson cases`, () => {
    for (const c of data.wilson) {
      const [low, high] = wilsonInterval(c.input.passes, c.input.n);
      close(low, c.expected.low);
      close(high, c.expected.high);
      close(wilsonLowerBound(c.input.passes, c.input.n), c.expected.lower);
    }
  });
  it(`replays ${data.exactMatch.length} exact-match cases`, () => {
    for (const c of data.exactMatch) close(exactMatchRate(c.input.pairs), c.expected.rate);
  });
  it(`replays ${data.alpha.length} Krippendorff-alpha cases`, () => {
    for (const c of data.alpha) close(krippendorffAlphaInterval(c.input.pairs), c.expected.alpha);
  });
});
