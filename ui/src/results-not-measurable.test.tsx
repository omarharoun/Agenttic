/* The run panel's coverage table must not print 0% for a dimension nothing measured.
 *
 * `per_coverpoint[x].closure` is `null` when nothing in the system emits the
 * evidence that dimension reads. The cell rendered `Math.round((v.closure ?? 0) *
 * 100)` — `0%`, which is the one sentence the backend was corrected to stop
 * saying: zero is a measurement, it reads as "the suite never got there", and it
 * is a gap someone can be told to close. No suite can close this one.
 *
 * It was unreachable, but only by accident: the row filter tested
 * `(v.unhit || []).length`, and a not-measurable coverpoint reports `unhit: []`.
 * Two unrelated decisions in two files lining up is not a guarantee — and the
 * side effect was that the panel hid the single most important thing this product
 * claims to tell you. So the row is now listed on its own terms and these tests
 * pin both halves: the cell says it in words, and the row is there to say it.
 *
 * The state test is `typeof v.closure === "number"`, the same one
 * `dimsFromCoverage` uses to hatch a wheel sector — the wheel sits directly above
 * this table on the same screen.
 */
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { NeverExercised } from "./panels/ResultsPanel";

const html = (el: React.ReactElement) => renderToStaticMarkup(el);
const NO_HEX = /#[0-9a-fA-F]{3,8}\b/;   // tokens only, so both themes follow

/* Shaped exactly like ops.verify_op's per_coverpoint entries: `session_shape` is
 * what the shipped baseline model actually emits on every run — closure null,
 * unhit empty, the flag set and a reason attached. A fixture that cannot occur
 * would not be evidence the screen handles what will occur. */
const BASELINE_RUN = {
  coverage: {
    model_ref: "coverage:cov-baseline-deterministic@v3",
    baseline: true,
    trace_closure: 0.208,
    closure_target: 0.95,
    closed: false,
    limits: "Baseline model only.",
    per_coverpoint: {
      trajectory: { closure: 0.1111, unhit: ["refused", "timeout"],
                    not_measurable: false, not_measurable_reason: "" },
      session_shape: {
        closure: null, unhit: [], not_measurable: true,
        not_measurable_reason:
          "nothing emits a `user_turn` span, so there is no second human turn "
          + "for a run to exhibit",
      },
      action_risk: { closure: 0, unhit: ["read_only", "mutating_reversible"],
                     not_measurable: false, not_measurable_reason: "" },
    },
  },
};

const cell = (out: string, cp: string) => {
  // The closure <td> of ONE coverpoint's row, so `not.toContain("0%")` cannot be
  // satisfied by a different row further down the table. The platform's own
  // vacuity rule applies to its own tests: a row that stopped being rendered
  // would otherwise return "" and quietly satisfy every negative assertion here,
  // so a miss throws rather than passing.
  const m = out.match(new RegExp(`>${cp}</td><td[^>]*>(.*?)</td>`));
  if (!m) throw new Error(`no row rendered for ${cp} — assertion would be vacuous`);
  return m[1];
};

describe("a not-measurable coverpoint in the run panel", () => {
  it("is listed at all, even though it has no unhit bins", () => {
    const out = html(<NeverExercised sc={BASELINE_RUN} />);
    expect(out).toContain("session_shape");
  });

  it("says 'not measurable' rather than a percentage", () => {
    expect(cell(html(<NeverExercised sc={BASELINE_RUN} />), "session_shape"))
      .toContain("not measurable");
  });

  it("never prints 0% for it", () => {
    expect(cell(html(<NeverExercised sc={BASELINE_RUN} />), "session_shape"))
      .not.toContain("0%");
  });

  it("carries the reason, because the reason IS the disclosure", () => {
    expect(html(<NeverExercised sc={BASELINE_RUN} />)).toContain("user_turn");
  });

  it("agrees with the wheel above it about which dimensions are unmeasured",
     async () => {
    /* The panel draws CoverageWheelFor from the same per_coverpoint blob. A table
     * reading 0% beside a hatched sector would be one panel contradicting
     * itself, so the two are pinned to the same decision. */
    const { dimsFromCoverage } = await import("./components/ds");
    const dims = dimsFromCoverage(BASELINE_RUN.coverage.per_coverpoint as any);
    expect(dims.find((d) => d.id === "session_shape")?.value).toBeNull();
    expect(cell(html(<NeverExercised sc={BASELINE_RUN} />), "session_shape"))
      .toContain("not measurable");
  });
});

describe("a measured zero is still a zero", () => {
  it("keeps printing 0% for a dimension that WAS measured and exhibited nothing", () => {
    /* The correction is not "never print 0%". `action_risk: 0` is a real finding
     * — the run touched none of the risk classes — and softening it would trade
     * one lie for another. */
    expect(cell(html(<NeverExercised sc={BASELINE_RUN} />), "action_risk"))
      .toContain("0%");
  });

  it("still prints a normal closure percentage", () => {
    expect(cell(html(<NeverExercised sc={BASELINE_RUN} />), "trajectory"))
      .toContain("11%");
  });
});

describe("closure absent without the declaration", () => {
  /* A payload with neither a number nor the flag: an older scorecard, or a model
   * that produced no figure. Not zero, and not ours to invent one. */
  const ODD = {
    coverage: {
      model_ref: "coverage:cov-baseline-deterministic@v3",
      per_coverpoint: { mystery: { closure: null, unhit: ["a"] } },
    },
  };

  it("says 'not measured' rather than 0%", () => {
    const c = cell(html(<NeverExercised sc={ODD} />), "mystery");
    expect(c).toContain("not measured");
    expect(c).not.toContain("0%");
  });
});

describe("the panel stays honest about colour", () => {
  it("emits no raw hex", () => {
    expect(html(<NeverExercised sc={BASELINE_RUN} />)).not.toMatch(NO_HEX);
  });

  it("renders nothing when there is nothing to disclose", () => {
    const clean = {
      coverage: {
        model_ref: "coverage:cov-baseline-deterministic@v3",
        per_coverpoint: { trajectory: { closure: 1, unhit: [],
                                        not_measurable: false } },
      },
    };
    expect(html(<NeverExercised sc={clean} />)).toBe("");
  });
});
