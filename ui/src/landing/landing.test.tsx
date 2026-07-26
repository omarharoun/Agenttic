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
    // "cw" is the coverage wheel, which replaced the decorative escapement as
    // the hero art — same library, and now the art carries the argument.
    for (const cls of ["ds-card", "ds-badge", "ds-cmp", "ds-faq", "ds-btn",
                       "ds-eyebrow", "cw", "ds-term"]) {
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

/* --- the six honesty fixes, pinned ---------------------------------------- *
 * Each of these was a place the page argued against itself, or claimed more
 * than the product does. They regress easily because they are all copy. */

describe("the landing must not contradict its own argument", () => {
  it("does NOT lead the product shot with a pass rate", () => {
    // The page spends its length arguing a rate with no denominator is unscoped.
    // Printing it as the headline of the one product shot undoes that.
    const seeIt = html.slice(html.indexOf('id="see"'), html.indexOf('id="why"'));
    const triedAt = seeIt.indexOf("Situations tried");
    const rateAt = seeIt.indexOf("Pass rate");
    expect(triedAt).toBeGreaterThan(-1);
    expect(triedAt).toBeLessThan(rateAt);            // coverage first
    expect(seeIt).toContain("of what was tried");    // and the rate is qualified
  });

  it("shows the wheel in the product shot, matching what the console renders", () => {
    const seeIt = html.slice(html.indexOf('id="see"'), html.indexOf('id="why"'));
    expect(seeIt).toContain('class="cw');
  });

  it("puts the wheel beside the claim it exists to make", () => {
    const cover = html.slice(html.indexOf('id="cover"'), html.indexOf('id="doors"'));
    expect(cover).toContain("lp-cover-lead");
    expect(cover).toContain("can never fail");
  });

  it("does not claim there is no server at all — only none in the eval loop", () => {
    // A hosted console, a public certificate page and billing all exist. The
    // unqualified claim is the one a sharp reader uses to discount the rest.
    expect(html).not.toMatch(/there is no server in the loop/);
    expect(html).toContain("no server in the evaluation loop");
  });

  it("does not promise a suite fitted to every agent", () => {
    // One authored archetype plus a generic baseline is not a bespoke library.
    expect(html).not.toContain("A test built for your agent");
    expect(html).toContain("baseline that applies to any agent");
  });

  it("offers the thing that can actually be delivered today", () => {
    // The audit runs over traces the customer already has, deterministically,
    // with zero model calls — unlike a certificate, which currently cannot issue.
    expect(html).toContain("Book a coverage audit");
    expect(html).toMatch(/traces you already have/i);
  });
});

describe("the refusal is the pitch", () => {
  it("leads with a certificate that was NOT issued", () => {
    const hero = html.slice(0, html.indexOf('id="why-refused"'));
    expect(hero).toContain("Refused");
    expect(hero).toContain("rn__stamp");
    expect(hero).toMatch(/we are the ones who say no/i);
  });

  it("names the reasons for the refusal, including the hard stop", () => {
    expect(html).toMatch(/cannot undo, without asking/i);
    expect(html).toContain("is-critical");
    expect(html).toMatch(/never came up at all/i);
  });

  it("positions on top of existing tools rather than against them", () => {
    expect(html).toMatch(/We do not replace your testing/i);
    for (const tool of ["LangSmith", "deepeval", "Future AGI"]) {
      expect(html).toContain(tool);
    }
  });

  it("puts the install on the home page, for the hook and the connector", () => {
    const inst = html.slice(html.indexOf('id="install"'));
    expect(inst).toContain("agenttic hook install");
    expect(inst).toContain("agenttic hook verify");
    expect(inst).toMatch(/args.*mcp/);      // quotes are html-escaped
  });

  it("explains the wheel without jargon", () => {
    const why = html.slice(html.indexOf('id="why-refused"'), html.indexOf('id="ontop"'));
    expect(why).toMatch(/that whole range is the circle/i);
    expect(why).not.toMatch(/closure|coverpoint|assertion|archetype/i);
  });
});

describe("no jargon reaches the reader", () => {
  it("keeps the technical vocabulary out of the visible copy", () => {
    // Strip tags/attrs; what remains is what a visitor actually reads.
    const text = html.replace(/<[^>]+>/g, " ");
    for (const word of ["coverpoint", "pass^k", "archetype", "OTLP",
                        "vacuity", "provenance", "deterministic check"]) {
      expect(text.toLowerCase()).not.toContain(word.toLowerCase());
    }
  });
});
