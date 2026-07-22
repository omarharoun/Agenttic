/* SPEC-11 Step 52 — landing route acceptance tests.
 * The landing renders from the shared ds components, its see-it scorecard is the
 * SAME ScorecardCard the console uses, its picker/tabs/faq are interactive
 * (keyboard-operable native elements), and — with SHOW_SOCIAL_PROOF off (the
 * default) — it ships clean with zero fabricated figures. */
import { describe, it, expect } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import React from "react";
import { LandingPage } from "../pages/LandingPage";

const html = renderToStaticMarkup(
  <MemoryRouter><LandingPage /></MemoryRouter>);

describe("landing route", () => {
  it("renders from the shared ds component library", () => {
    for (const cls of ["ds-card", "ds-badge", "ds-cmp", "ds-faq", "ds-btn",
                       "ds-eyebrow", "ds-escape", "ds-term"]) {
      expect(html).toContain(cls);
    }
  });

  it("see-it uses the shared ScorecardCard with sample data (not real/authed)", () => {
    expect(html).toContain("support-triage · sample data");
    expect(html).toContain("ds-card__metrics");
    expect(html).toContain("policy_fidelity");   // a sample criterion row
  });

  it("the picker and command tabs are interactive tabs (keyboard-operable)", () => {
    expect(html).toContain('role="tab"');
    expect(html).toContain("lp-asst");
    expect(html).toContain("lp-tab");
  });

  it("FAQ items are native disclosure widgets", () => {
    expect(html).toContain("<details");
    expect(html).toContain("ds-faq__q");
  });

  it("ships clean with social proof OFF — no fabricated figures", () => {
    expect(html).not.toMatch(/GitHub stars|PyPI downloads|In their words/);
    expect(html).not.toMatch(/\[stars\]|\[downloads\]|\[adopter|figures marked/);
  });

  it("carries no authenticated console chrome or data", () => {
    // no app-shell / console nav / token-bearing widgets on the public route
    expect(html).not.toContain("app-shell");
    expect(html).not.toContain("AccountMenu");
  });
});

describe("shared score components span both surfaces", () => {
  it("ProvenanceBadge is rendered on the landing", () => {
    expect(html).toContain("ds-badge--det");   // deterministic
    expect(html).toContain("ds-badge--cal");   // judged·calibrated
    expect(html).toContain("ds-badge--prov");  // provisional
  });
});
