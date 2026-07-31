/* The visual gate's fixture must contain the thing the visual gate exists to watch.
 *
 * `ui/e2e/__screenshots__/results-{dark,light}.png` are the evidence that the
 * console's coverage disclosure still looks the way it looked. They were taken
 * against a stub whose `coverage` blob had a headline and NOTHING underneath it,
 * so `NeverExercised` — "what this run never exercised", the product's headline
 * claim — rendered as the empty string in both themes. The gate photographed a
 * blank space and diffed it clean forever.
 *
 * That is how a `0%` cell for an UNMEASURED dimension shipped past it. The cell
 * was wrong; the row it lived in was never on the page.
 *
 * `results-not-measurable.test.tsx` pins the COMPONENT: given these three
 * states, render them correctly. This file pins the FIXTURE: the stub the
 * screenshots are taken from still HAS all three states, and still produces a
 * table with rows in it. Both are needed — the component was right in the second
 * round and the screen was still blank, because nothing connected them.
 *
 * It also recomputes the headline. A fixture is only evidence if it is a payload
 * a run could actually emit, and the previous one was not: 91% closure over
 * coverpoints that did not exist. Hand-editing one number now fails here rather
 * than quietly producing an impossible run for the camera.
 */
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

/* The fixture file itself, not a copy of it. `console.ts` reads these same bytes
 * with `readFileSync` and serves them as `/api/scorecards`; it is imported here
 * rather than the module because that module also imports `node:fs`, which this
 * tsconfig's program has no types for. What matters is that both readers open
 * the one file — a second transcription of the payload would be a fixture for
 * the fixture, and could drift from the thing the camera sees. */
import COVERAGE from "../e2e/support/coverage.fixture.json";
import { NeverExercised } from "./panels/ResultsPanel";
import { DECLARED_COVERPOINTS, dimsFromCoverage } from "./components/ds";

const html = (el: React.ReactElement) => renderToStaticMarkup(el);
/** What `stubApi` hangs the blob on: NeverExercised reads `sc.coverage`. */
const scorecard = { scorecard_id: "sc-demo-1", coverage: COVERAGE };
/** One `per_coverpoint` entry, as `ops.verify_op` serializes it. Spelled out
 *  rather than inferred from the JSON: TypeScript narrows `session_shape.closure`
 *  to the literal type `null` and `unhit` to `never[]`, which would let an
 *  assertion about the not-measurable state typecheck against the one fixture
 *  that happens to be shaped that way instead of against the contract. */
interface Entry {
  closure: number | null;
  unhit: string[];
  other_hits: number;
  not_measurable: boolean;
  not_measurable_reason: string;
}
const PER: Record<string, Entry> = COVERAGE.per_coverpoint;

describe("the stub the console screenshots are taken from", () => {
  it("carries per-coverpoint detail at all", () => {
    /* The whole defect in one assertion. Without this key `NeverExercised`
     * returns null and the run screen photographs blank. */
    expect(Object.keys(PER).length).toBeGreaterThan(0);
  });

  it("holds a coverpoint that was MEASURED and has gaps", () => {
    const gapped = Object.entries(PER).filter(
      ([, v]) => !v.not_measurable && typeof v.closure === "number"
        && v.closure > 0 && v.unhit.length);
    expect(gapped.map(([k]) => k)).toContain("trajectory");
  });

  it("holds a coverpoint that was MEASURED and exhibited nothing", () => {
    /* The state a fixture is most tempted to leave out, and the one that makes
     * "don't print 0%" the wrong lesson. */
    const zero = Object.entries(PER).filter(
      ([, v]) => !v.not_measurable && v.closure === 0);
    expect(zero.map(([k]) => k)).toContain("action_risk");
    expect(PER.action_risk.unhit.length).toBeGreaterThan(0);
  });

  it("holds a coverpoint that is NOT MEASURABLE", () => {
    const cp = PER.session_shape;
    expect(cp.not_measurable).toBe(true);
    // null, never 0 — collect.CoverpointCoverage.trace_closure
    expect(cp.closure).toBeNull();
    // you cannot have failed to exercise what nothing observes
    expect(cp.unhit).toEqual([]);
    // an undisclosed exclusion from the denominator is a silent hole
    expect(cp.not_measurable_reason).toContain("user_turn");
  });
});

describe("what the camera will now see", () => {
  const out = html(<NeverExercised sc={scorecard} />);

  it("is a table, not an empty string", () => {
    expect(out).not.toBe("");
    expect(out).toContain("What this run never exercised");
  });

  it("shows the measured zero as 0%", () => {
    expect(cell(out, "action_risk")).toContain("0%");
  });

  it("shows the unmeasured dimension in words, and never as 0%", () => {
    const c = cell(out, "session_shape");
    expect(c).toContain("not measurable");
    expect(c).not.toContain("0%");
  });

  it("shows an ordinary closure as a percentage", () => {
    expect(cell(out, "trajectory")).toContain("89%");
  });

  it("names the bins the run never reached, so the row is actionable", () => {
    expect(out).toContain("budget_exceeded");
  });

  it("keeps a fully-closed dimension out of the table", () => {
    /* `agent_steps` is at 1.0 with nothing unhit. A "never exercised" table
     * listing it would be noise, and its absence here is what proves the row
     * filter is doing something rather than dumping the blob. */
    expect(PER.agent_steps.closure).toBe(1);
    expect(out).not.toContain("agent_steps");
  });
});

/** The closure <td> of ONE coverpoint's row.
 *
 *  A negative assertion over the whole table can be satisfied by a row that
 *  stopped rendering — the platform's own vacuity rule (unexercised is not a
 *  pass) applied to its own tests. A miss throws. */
function cell(out: string, cp: string): string {
  const m = out.match(new RegExp(`>${cp}</td><td[^>]*>(.*?)</td>`));
  if (!m) throw new Error(`no row rendered for ${cp} — assertion would be vacuous`);
  return m[1];
}

describe("the fixture is a payload a run could emit", () => {
  it("agrees with the wheel drawn directly above it", () => {
    /* Same screen, same blob: a table reading 0% beside a hatched sector would
     * be one panel contradicting itself.
     *
     * The cast is a finding, not a convenience. `dimsFromCoverage` declares
     * `closure?: number` — a type in which a not-measurable coverpoint cannot
     * exist — while its body reads `typeof d.closure === "number" ? … : null`,
     * i.e. it is written for the `null` the backend actually sends
     * (`ops.verify_op`'s `per_coverpoint`, `null` whenever nothing measures the
     * dimension). Signature and body disagree, and the signature is the wrong
     * one. Fixing it means editing `components/ds/CoverageWheel.tsx`, which
     * belongs to another workstream, so it is cast here and reported rather
     * than silently widened. */
    const dims = dimsFromCoverage(
      PER as unknown as Parameters<typeof dimsFromCoverage>[0],
      DECLARED_COVERPOINTS);
    expect(dims.find((d) => d.id === "session_shape")?.value).toBeNull();
    expect(dims.find((d) => d.id === "action_risk")?.value).toBe(0);
    // every declared dimension gets a sector, measured or not
    expect(dims.length).toBeGreaterThanOrEqual(DECLARED_COVERPOINTS.length);
  });

  it("has a headline that is the mean of what it reports", () => {
    /* CoverageReport.trace_closure: the mean over measurable coverpoints AND
     * crosses, with not-measurable ones out of both numerator and denominator.
     * Recomputed rather than trusted, because the old fixture's 91% was a
     * literal with nothing under it — the exact over-report this product exists
     * to refuse, sitting in its own test data. */
    const vals = [
      ...Object.values(PER).map((v) => v.closure).filter((v) => v !== null),
      ...Object.values(COVERAGE.crosses as Record<string, number>),
    ];
    const mean = vals.reduce((a, b) => a + b, 0) / vals.length;
    // 1e-3, not exact: per_coverpoint ships rounded to 4dp (ops.py `_round4`).
    expect(Math.abs(mean - COVERAGE.trace_closure)).toBeLessThan(1e-3);
  });

  it("keeps the not-measurable dimension out of the headline entirely", () => {
    /* Not averaged in as a zero and not credited: absent from both sides. If it
     * were counted as 0 the headline would be lower, and that lower number would
     * be a claim about a suite that cannot be improved. */
    const measurable = Object.values(PER)
      .map((v) => v.closure).filter((v) => v !== null);
    const crosses = Object.values(COVERAGE.crosses as Record<string, number>);
    const asZero = [...measurable, 0, ...crosses];
    const asZeroMean = asZero.reduce((a, b) => a + b, 0) / asZero.length;
    expect(asZeroMean).toBeLessThan(COVERAGE.trace_closure);
  });

  it("discloses its own denominator", () => {
    /* A closure figure without the sample count it was measured over is a figure
     * over an undisclosed denominator — the same over-report one layer out, and
     * the reason `samples` / `samples_submitted` / `non_results` travel together
     * (ops.py). They must also add up: a fixture whose submitted count exceeds
     * its measured count with nothing marked a non-result describes a run that
     * silently dropped cases. */
    expect(COVERAGE.samples).toBeGreaterThan(0);
    expect(COVERAGE.samples + COVERAGE.non_results)
      .toBe(COVERAGE.samples_submitted);
    // and the exclusion is never silent: a non-result count with no reasons
    // beside it discloses that cases were dropped without saying which or why
    const reasons = Object.keys(COVERAGE.non_result_reasons).length;
    expect(COVERAGE.non_results === 0 ? reasons === 0 : reasons > 0).toBe(true);
  });

  it("names every bin it removed from the denominator, with a reason", () => {
    const waived = COVERAGE.waived_bins as Record<string, string>;
    expect(Object.keys(waived).length).toBeGreaterThan(0);
    for (const [bin, why] of Object.entries(waived)) {
      expect(bin).toContain(".");            // coverpoint-qualified
      expect(why.trim().length).toBeGreaterThan(0);
    }
  });
});
