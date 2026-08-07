/* The fault-fate graph must not flatten three facts into one.
 *
 * `fired`, `skipped` and `never_reached` are different findings: the fault
 * happened, it reached its call and could not happen, or the agent never got
 * there. "We broke it and the agent never arrived" is a result about the run,
 * not a silence — so the chart draws all three and never reduces them to a
 * count or a ratio.
 */
import { describe, it, expect } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import React from "react";
import { FaultFateBar, type FaultFateRow } from "./FaultFateBar";

const rows: FaultFateRow[] = [
  { runId: "r1", label: "run-1", counts: { fired: 3, skipped: 1, never_reached: 2, planned: 6 },
    worldChanged: true, nBlocked: 2 },
  { runId: "r2", label: "run-2", counts: { fired: 0, skipped: 0, never_reached: 4, planned: 4 } },
  { runId: "r3", label: "run-3", counts: { fired: 0, skipped: 0, never_reached: 0, planned: 0 } },
];

const html = renderToStaticMarkup(<FaultFateBar rows={rows} />);

describe("FaultFateBar", () => {
  it("names all three fates in the legend", () => {
    for (const f of ["fired", "skipped", "never reached"]) {
      expect(html).toContain(f);
    }
  });

  it("identity is never colour alone — every fate is labelled", () => {
    // The legend carries text beside each swatch, so a colourblind or
    // monochrome reader loses nothing.
    expect((html.match(/ffb__swatch/g) || []).length).toBe(3);
    expect((html.match(/ffb__key/g) || []).length).toBe(3);
  });

  it("draws a run whose faults ALL went unreached", () => {
    // The case most easily mistaken for "nothing happened". It must render a
    // bar, not an empty row.
    expect(html).toContain("run-2");
    expect((html.match(/<rect/g) || []).length).toBeGreaterThanOrEqual(4);
  });

  it("distinguishes 'no fault staged' from a zero bar", () => {
    // An unstaged fault and a fault that fired zero times look identical as an
    // empty bar, and they are different facts.
    expect(html).toContain("no fault staged");
  });

  it("carries world-changed and blocked as separate marks", () => {
    expect(html).toContain("world changed");
    expect(html).toContain("2 blocked");
  });

  it("ships a table view of the same numbers", () => {
    // Present for assistive tech and print; the toggle is a real button.
    expect(html).toContain("Show the numbers");
    expect(html).toContain("<button");
  });

  it("uses the VALIDATED categorical tokens, not the dial ramp", () => {
    // --viz-3 has chroma 0.009 (it is grey) and --viz-2/--viz-4 sit 10.8 ΔE
    // apart, below the 15 normal-vision floor. --cat-* clear all six checks.
    for (const t of ["var(--cat-1)", "var(--cat-2)", "var(--cat-3)"]) {
      expect(html).toContain(t);
    }
    expect(html).not.toContain("var(--viz-");
  });

  it("carries an accessible name for the plot", () => {
    expect(html).toMatch(/role="img"/);
    expect(html).toMatch(/aria-label="Fault fates for 3 scenario run\(s\)"/);
  });

  it("renders nothing at all when there are no runs", () => {
    expect(renderToStaticMarkup(<FaultFateBar rows={[]} />)).toBe("");
  });
});
