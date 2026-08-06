/* Landing production-bar structural checks.
 *
 * The parts of the bar that don't need a browser: a single H1, keyboard-operable
 * native controls, and no ornament claiming to be information. Full axe +
 * visual-regression are Playwright gates, not run here.
 *
 * Updated for the "trust layer" repositioning. The three wheel-specific checks
 * are gone because the wheel is — but the RULE they enforced is kept and
 * generalised below: any SVG that carries meaning must be described, and any
 * SVG that is ornament must be hidden. That rule outlives whichever art the
 * hero happens to use, which is what the old tests got wrong by naming `cw`.
 */
import { describe, it, expect } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import React from "react";
import { LandingPage } from "../pages/LandingPage";

// NB: the CSS-level bar checks (reduced-motion sweep stop, horizontal-overflow
// guard) are asserted in the Node lint (scripts/check-tokens.mjs) — vitest stubs
// .css imports to empty, so the stylesheet can't be read here.
const html = renderToStaticMarkup(
  <MemoryRouter><LandingPage /></MemoryRouter>);

describe("landing a11y / responsive bar", () => {
  it("has exactly one <h1>", () => {
    expect((html.match(/<h1\b/g) || []).length).toBe(1);
  });

  it("carries no decorative ornament posing as content", () => {
    // The escapement dial was removed for exactly this reason and must not
    // creep back: motion encodes state, it is not decoration (Hard Rule).
    expect(html).not.toContain("ds-escape");
  });

  it("every SVG is either described to AT or explicitly hidden from it", () => {
    // The generalised form of the old wheel-specific check. An SVG with neither
    // an accessible name nor aria-hidden is the actual defect — naming one
    // component (`cw`) only caught it in one place.
    const svgs = html.match(/<svg[^>]*>/g) || [];
    expect(svgs.length).toBeGreaterThan(0);
    for (const svg of svgs) {
      const described = /aria-label="[^"]+"|role="img"|<title/.test(svg);
      const hidden = /aria-hidden="true"/.test(svg);
      expect(described || hidden, `undescribed SVG: ${svg.slice(0, 90)}`).toBe(true);
    }
  });

  it("interactive controls are keyboard-operable native elements", () => {
    // Native elements only — no div-with-onclick. `aria-label="copy commands"`
    // is gone with the CodeBlock, so the check is on what the page HAS: a real
    // disclosure for the FAQ, a real button in the nav, and CTAs that are
    // anchors with a destination (they rendered as dead <button>s at one point,
    // which is precisely the bug this catches).
    expect(html).toContain("<details");            // FAQ is a native disclosure
    expect(html).toContain("<button");             // nav burger is a real button
    const ctas = html.match(/<a class="ds-btn[^"]*"[^>]*>/g) || [];
    expect(ctas.length).toBeGreaterThan(0);
    for (const cta of ctas) {
      expect(cta, `CTA without href: ${cta}`).toMatch(/href="\/[^"]*"/);
    }
  });

  it("no interactive control is an unlabelled icon", () => {
    const buttons = html.match(/<button[^>]*>(?:(?!<\/button>).)*<\/button>/gs) || [];
    for (const b of buttons) {
      const hasText = />\s*[A-Za-z0-9]/.test(b.replace(/<[^>]+>/g, ">"));
      const hasLabel = /aria-label="[^"]+"/.test(b);
      expect(hasText || hasLabel, `unlabelled button: ${b.slice(0, 80)}`).toBe(true);
    }
  });
});
