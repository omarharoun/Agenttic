/* Landing route acceptance tests — the "trust layer" narrative (stage 2).
 *
 * The page was repositioned from the coverage/refusal argument to
 * Record -> Evaluate -> Assert -> Certify. That deliberately removed content,
 * and the tests that pinned it went with it. Each removal is listed at the
 * bottom of this file with the reason, so the record is auditable rather than
 * silent — a test that vanishes without a note is indistinguishable from a test
 * someone deleted to get green.
 *
 * What did NOT change is every honesty guard. Those are retargeted to the new
 * section ids, not dropped: a repositioning is allowed to change what we claim,
 * never whether the claims are true. They are grouped under "must not
 * contradict its own argument" below and are the reason this file exists.
 */
import { describe, it, expect } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import React from "react";
import { LandingPage } from "../pages/LandingPage";

const html = renderToStaticMarkup(
  <MemoryRouter><LandingPage /></MemoryRouter>);

describe("landing route", () => {
  it("renders from the shared ds component library", () => {
    // One component library, one style world. `cw`/`ds-term` are gone with the
    // wheel and the transcript; the rest still pin the principle.
    for (const cls of ["ds-card", "ds-badge", "ds-faq", "ds-btn", "ds-eyebrow"]) {
      expect(html).toContain(cls);
    }
  });

  it("uses the shared ScorecardCard with SAMPLE data, never real or authed", () => {
    // The hero shows a run. It must be visibly sample data — a landing page
    // rendering a real customer's scorecard is the worst version of this bug.
    expect(html).toContain("sample data");
    expect(html).toContain("ds-card__metrics");
    expect(html).toContain("policy_fidelity");     // a sample criterion row
  });

  it("carries no authenticated console chrome or data", () => {
    for (const leak of ["Sign out", "api_key", "Bearer ", "localStorage"]) {
      expect(html).not.toContain(leak);
    }
  });

  it("ships clean with social proof OFF — no fabricated figures", () => {
    // The ORIGINAL assertion, kept verbatim. My first attempt at rewriting it
    // used a crude word list ("customers") that trips on legitimate prose —
    // "before it reaches a customer" is not social proof. A guard that fires on
    // honest copy gets deleted by the next person, which is worse than no guard.
    expect(html).not.toMatch(/GitHub stars|PyPI downloads|In their words/);
    expect(html).not.toMatch(/\[stars\]|\[downloads\]|\[adopter|figures marked/);
  });

  it("ProvenanceBadge is rendered on the landing", () => {
    expect(html).toContain("ds-badge");
    expect(html).toMatch(/deterministic/i);
  });
});

describe("the trust-layer argument is stated", () => {
  it("leads with the repositioned hero", () => {
    expect(html).toContain("The trust layer for agents.");
    expect(html).toMatch(/Evaluate · assert · certify/i);
  });

  it("names the four steps, in order", () => {
    const at = (s: string) => html.indexOf(s);
    expect(at("RECORD")).toBeGreaterThan(-1);
    expect(at("RECORD")).toBeLessThan(at("EVALUATE"));
    expect(at("EVALUATE")).toBeLessThan(at("ASSERT"));
    expect(at("ASSERT")).toBeLessThan(at("CERTIFY"));
  });

  it("states the gap it exists to close", () => {
    expect(html).toMatch(/Software was verified because it was deterministic/i);
    expect(html).toMatch(/no diff to read/i);
  });
});

describe("the landing must not contradict its own argument", () => {
  /* Every test in this block predates the repositioning and survives it. They
   * are about truthfulness, not about which story we tell. */

  it("does NOT lead the product shot with a pass rate", () => {
    // The page argues a rate with no denominator is unscoped. Printing it as
    // the headline of the one product shot would undo that.
    const tried = html.indexOf("Situations tried");
    const rate = html.indexOf("Pass rate");
    expect(tried).toBeGreaterThan(-1);
    expect(tried).toBeLessThan(rate);              // coverage first
    expect(html).toContain("of what was tried");   // and the rate is qualified
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

  it("positions on top of existing tools rather than against them", () => {
    expect(html).toMatch(/We do not replace your testing/i);
    for (const tool of ["LangSmith", "deepeval", "Future AGI"]) {
      expect(html).toContain(tool);
    }
  });

  it("offers something that can actually be delivered today", () => {
    // The offer moved from a coverage audit to opening a live trace. Both are
    // real; the guard is that the CTA points at a route that exists.
    expect(html).toMatch(/Open a live trace/i);
    expect(html).toContain('href="/scan"');
  });

  it("labels every figure as sample data, never as a customer metric", () => {
    expect(html).toMatch(/sample data/i);
    expect(html).toMatch(/not a customer figure/i);
  });

  it("keeps our INTERNAL vocabulary out of the hero", () => {
    // The lede is the first paragraph anybody reads. Our words for our own
    // machinery do not belong in it.
    const hero = html.slice(0, html.indexOf('id="gap"'));
    for (const jargon of ["closure", "coverpoint", "sign-off", "pass^k",
                          "vacuity", "stimulus"]) {
      expect(hero.toLowerCase()).not.toContain(jargon.toLowerCase());
    }
  });
});

/* ---------------------------------------------------------------------------
 * REMOVED WITH THE REPOSITIONING — listed, not silently dropped.
 *
 * These pinned content the page no longer has. They were not weakened to get
 * green; the content they asserted was removed by an explicit product decision
 * (positioning B), and a test for absent content can only fail.
 *
 *   the hero art is the coverage wheel      ) the wheel is gone from the page;
 *   the wheel is described to assistive tech) B leads with the run card instead
 *   shows the wheel in the product shot     )
 *   puts the wheel beside its claim         )
 *   explains the wheel without jargon       )
 *   leads with a certificate that was NOT issued ) the refusal transcript moved
 *   names the reasons for the refusal            ) to /methodology
 *   puts the install on the home page       — install moved to /engine
 *   the picker and command tabs are tabs    — the surface picker is gone
 *   carries the scope line                  ) the engine section moved to
 *   links to the page that shows the whole thing ) /engine, which has its own
 *   names the fault outcomes as three facts      ) tests (engine-page.test.tsx)
 *   names the three capabilities                 )
 *
 * If any of that content returns to the landing, its test must return with it.
 * ------------------------------------------------------------------------- */
