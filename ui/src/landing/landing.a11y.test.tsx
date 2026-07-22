/* SPEC-11 Step 53 — landing production-bar structural checks.
 *
 * These cover the parts of the bar that don't need a browser: a single H1, the
 * decorative dial hidden from AT, keyboard-operable controls (native
 * button/details), a reduced-motion rule, and a horizontal-overflow guard (clean
 * to 360px). Full axe + visual-regression are browser-runner gates (Playwright),
 * noted in the M35 report, not run here.
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

  it("the decorative escapement dial is hidden from assistive tech", () => {
    expect(html).toMatch(/class="ds-escape[^"]*"[^>]*aria-hidden="true"|aria-hidden="true"[^>]*class="ds-escape/);
  });

  it("interactive controls are keyboard-operable native elements", () => {
    expect(html).toContain("<button");    // picker + tabs + copy are real buttons
    expect(html).toContain("<details");   // faq is a native disclosure
    // the copy control carries an accessible name
    expect(html).toMatch(/aria-label="copy commands"/);
  });

});
