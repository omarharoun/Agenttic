// @vitest-environment jsdom
/* SPEC-5 23.2 — the scorecard what-if panel renders live, parity-proven
   recomputation and stays hypothetical (never mutates). */
import { afterEach, describe, expect, it } from "vitest";
import { createRoot, type Root } from "react-dom/client";
import React, { act } from "react";
import type { RunScore } from "./api";
import { WhatIfPanel } from "./components/WhatIfPanel";

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean })
  .IS_REACT_ACT_ENVIRONMENT = true;

function run(testId: string, a: number, b: number, err = false): RunScore {
  return {
    trace_id: `tr-${testId}`, test_id: testId,
    criterion_scores: [
      { criterion_id: "a", score: a, scorer: "judge", calibrated: true, judge_rationale: null, cost_usd: 0 },
      { criterion_id: "b", score: b, scorer: "judge", calibrated: true, judge_rationale: null, cost_usd: 0 },
    ],
    passed: (a + b) / 2 >= 0.7, cost_usd: 0, scoring_cost_usd: 0,
    latency_ms: 0, steps: 0, scoring_error: err ? "boom" : null,
  };
}

let container: HTMLDivElement, root: Root;
afterEach(() => act(() => root.unmount()));

function mount(runs: RunScore[]) {
  container = document.createElement("div");
  root = createRoot(container);
  act(() => root.render(<WhatIfPanel runs={runs} criteria={["a", "b"]} />));
}

describe("WhatIfPanel", () => {
  it("shows the recomputed rate (equal weights, 0.7 threshold) and is labelled hypothetical", () => {
    mount([run("t1", 1, 1), run("t2", 0, 0), run("t3", 1, 1)]);
    // t1,t3 pass (weighted 1.0), t2 fails (0.0) -> 2/3
    expect(container.textContent).toContain("2/3 pass");
    expect(container.textContent).toContain("Hypothetical — not saved");
  });

  it("excludes errored runs from the rate, like the server", () => {
    mount([run("t1", 1, 1), run("t2", 0, 0, /* err */ true)]);
    expect(container.textContent).toContain("1/1 pass"); // t2 excluded
  });

  it("proposing a rubric surfaces a non-mutating config block", () => {
    mount([run("t1", 1, 1)]);
    const btn = [...container.querySelectorAll("button")]
      .find((b) => /propose as rubric/i.test(b.textContent ?? ""))!;
    act(() => btn.dispatchEvent(new MouseEvent("click", { bubbles: true })));
    expect(container.textContent).toContain("pass_threshold");
    expect(container.textContent).toContain("never mutates");
  });
});
