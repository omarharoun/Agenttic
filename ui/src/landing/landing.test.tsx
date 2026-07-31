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
import { LIMITS } from "./data";
// Read the ENGINE's own source, the same technique engine-page.test.tsx uses:
// a claim about the model pinned to a copy of the model is not pinned at all.
import BASELINE_PY_RAW from "../../../src/agenttic/coverage/models/baseline.py?raw";

const html = renderToStaticMarkup(
  <MemoryRouter><LandingPage /></MemoryRouter>);

describe("landing route", () => {
  it("renders from the shared ds component library", () => {
    // "cw" is the coverage wheel, which replaced the decorative escapement as
    // the hero art — same library, and now the art carries the argument.
    // ds-cmp is absent on purpose: the side-by-side ComparisonTable was cut
    // (three of its rows made categorical claims about competitors' products).
    // The component still exists and is covered by ds.test.tsx; this page just
    // no longer uses it. Every remaining class still pins the principle.
    for (const cls of ["ds-card", "ds-badge", "ds-faq", "ds-btn",
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
    expect(hero).toMatch(/a certificate is not a participation award/i);
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
  // Strip tags/attrs; what remains is what a visitor actually reads.
  const text = html.replace(/<[^>]+>/g, " ").toLowerCase();

  it("keeps the technical vocabulary out of the visible copy", () => {
    for (const word of ["coverpoint", "pass^k", "archetype", "OTLP",
                        "vacuity", "provenance", "deterministic check"]) {
      expect(text).not.toContain(word.toLowerCase());
    }
  });

  it("keeps our INTERNAL vocabulary out of the hero, where it was worst", () => {
    /* This list is the reason the hero survived three honesty passes. The
     * jargon rule above it was written for `#why-refused` and applied to the
     * whole page; the rule that DID name `closure` and `assertion` was scoped
     * to that one slice (see "explains the wheel without jargon"). So the lede
     * read "coverage closure, assertions, a bounded formal check ... the
     * sign-off is negative the signing call raises" — five internal terms in
     * the first paragraph anybody reads, one of them a Python word — and no
     * test could see it.
     *
     * Scoped to the hero rather than the page: `closure` and `sign-off` are
     * legitimate further down, where the page has earned them and defines
     * them. It is the FIRST paragraph that has to be readable by someone who
     * has never used the product. */
    const end = text.indexOf("what it prints when it will not");
    // A missing marker makes `slice(0, -1)` the WHOLE page, which would either
    // fail for the wrong reason or pass for one — the vacuity rule applied to
    // this test. Assert the boundary exists before slicing on it.
    expect(end).toBeGreaterThan(0);
    const hero = text.slice(0, end);
    expect(hero.length).toBeGreaterThan(200);      // the slice really is the hero
    expect(hero).toContain("participation award");  // and it IS the hero
    for (const word of ["closure", "assertion", "sign-off", "signing call",
                        "raises", "formal check", "exits 3"]) {
      expect(hero).not.toContain(word);
    }
  });
});

describe("the landing's coverage claim tracks the engine, not a memory of it", () => {
  /* This is the drift that shipped. The wheel withdrew `session_shape` as a
   * figure the product cannot produce, and the sentence three sections away
   * still listed it as covered — so the live page said "the other four are not
   * measured at all" and "the baseline model covers ... session shape" at the
   * same time, on the page whose whole argument is that a number needs a
   * denominator.
   *
   * Pinned against `baseline.py` itself rather than against a copy of its
   * wording, so the page cannot drift from the model again without this failing.
   */
  const BASELINE_PY: string = BASELINE_PY_RAW;
  const limits = LIMITS.map((l) => l.p).join(" ").toLowerCase();

  it("the engine declares session shape measured only on an instrumented run", () => {
    // Guard the guard: if these anchors ever move, the assertions below would
    // pass vacuously against a string that no longer says anything.
    expect(BASELINE_PY).toContain("BASELINE_LIMITS");
    expect(BASELINE_PY).toContain("session_turns_instrumented");
    expect(BASELINE_PY.toLowerCase()).toContain("recorded who spoke");
  });

  it("the page never claims the baseline covers session shape", () => {
    expect(limits).not.toMatch(/covers[^.]*session shape/);
    expect(limits).not.toMatch(/session shape[^.]*(is covered|we cover)/);
  });

  it("the page names the five dimensions that ARE always measured", () => {
    // Plain words, not the internal ids — but all five must be there, or the
    // sentence under-claims the way it used to omit agent steps.
    for (const phrase of ["the path taken", "whether a tool failed",
                          "how many steps it took", "the state of the data",
                          "could be undone"]) {
      expect(limits).toContain(phrase);
    }
  });

  it("it still says what it cannot read", () => {
    expect(limits).toContain("why the customer came");
    expect(limits).toMatch(/only reads turn shape on a run that recorded who spoke/);
  });
});
