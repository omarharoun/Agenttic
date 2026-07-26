/* The coverage wheel — the product's one picture.
 *
 * What these pin is the honesty of the encoding, not the geometry:
 *   - an unmeasured dimension is DRAWN (hatched), never omitted and never zero
 *   - the gap out to the rim is rendered as its own mark, so "never exercised"
 *     has area rather than being background
 *   - nothing rests on colour alone
 *   - no raw hex, so both themes follow design/tokens.css
 */
import { describe, it, expect } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import React from "react";
import {
  CoverageWheel, CoverageWheelLegend, dimsFromCoverage, DECLARED_COVERPOINTS,
} from "./components/ds";

const html = (el: React.ReactElement) => renderToStaticMarkup(el);
const NO_HEX = /#[0-9a-fA-F]{3,8}\b/;

const DIMS = [
  { id: "trajectory", value: 0.111 },
  { id: "action_risk", value: 0.5 },
  { id: "intent", value: null },
];

describe("CoverageWheel", () => {
  it("draws a wedge and a gap for every measured dimension", () => {
    const out = html(<CoverageWheel dims={DIMS} closure={0.222} />);
    expect(out.match(/cw-hit/g)?.length).toBe(2);
    expect(out.match(/cw-gap/g)?.length).toBe(2);   // the untested remainder
  });

  it("draws an unmeasured dimension rather than omitting or zeroing it", () => {
    const out = html(<CoverageWheel dims={DIMS} closure={0.222} />);
    expect(out).toContain("cw-unmeasured");
    expect(out).toContain("NOT MEASURED");
    expect(out).toContain("intent");
  });

  it("says a gap is untested situations, not a failure", () => {
    const out = html(<CoverageWheel dims={DIMS} closure={0.222} />);
    expect(out).toMatch(/never exercised/i);
  });

  it("puts the closure number in the hub", () => {
    expect(html(<CoverageWheel dims={DIMS} closure={0.222} />)).toContain("22%");
  });

  it("omits the hub number rather than inventing one when closure is unknown", () => {
    const out = html(<CoverageWheel dims={DIMS} closure={null} />);
    expect(out).not.toContain("cw-ctr-v");
  });

  it("carries an accessible summary naming the unmeasured dimensions", () => {
    const out = html(<CoverageWheel dims={DIMS} closure={0.222} />);
    expect(out).toMatch(/aria-label="[^"]*not measured at all/);
  });

  it("labels each spoke, so identity is never colour-alone", () => {
    const out = html(<CoverageWheel dims={DIMS} closure={0.222} />);
    expect(out).toContain("action risk");
    expect(out).toContain("not measured");
  });

  it("renders nothing for an empty model rather than an empty circle", () => {
    expect(html(<CoverageWheel dims={[]} closure={0.5} />)).toBe("");
  });

  it("emits no raw hex — both themes follow the tokens", () => {
    expect(html(<CoverageWheel dims={DIMS} closure={0.222} />)).not.toMatch(NO_HEX);
    expect(html(<CoverageWheelLegend />)).not.toMatch(NO_HEX);
  });

  it("clamps a dimension at the rim instead of overflowing it", () => {
    const out = html(<CoverageWheel dims={[{ id: "x", value: 1 }]} closure={1} target={0.95} />);
    expect(out).toContain("cw-hit");
    expect(out).not.toMatch(/NaN/);
  });
});

describe("dimsFromCoverage", () => {
  it("gives a declared-but-unmeasured coverpoint a null value, not 0", () => {
    const dims = dimsFromCoverage({ trajectory: { closure: 0.11 } }, DECLARED_COVERPOINTS);
    expect(dims.find((d) => d.id === "trajectory")?.value).toBeCloseTo(0.11);
    expect(dims.find((d) => d.id === "intent")?.value).toBeNull();
  });

  it("keeps a measured coverpoint the archetype never declared", () => {
    const dims = dimsFromCoverage({ surprise_dim: { closure: 0.4 } }, DECLARED_COVERPOINTS);
    expect(dims.find((d) => d.id === "surprise_dim")?.value).toBeCloseTo(0.4);
  });

  it("survives a run with no coverage at all", () => {
    expect(dimsFromCoverage(null, ["a"])).toEqual([{ id: "a", value: null }]);
  });
});

describe("the legend", () => {
  it("names all three states in words", () => {
    const out = html(<CoverageWheelLegend />);
    expect(out).toContain("exercised");
    expect(out).toMatch(/never exercised/);
    expect(out).toMatch(/not measured/);
  });
});
