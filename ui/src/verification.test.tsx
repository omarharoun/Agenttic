/* The verification vocabulary is shared so that no console screen can quietly
 * re-introduce a bare pass rate. These tests pin the three states a record can
 * be in — no coverage model, the baseline model, a fitted model — and check
 * that each screen's rendering says the right thing about each.
 */
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import {
  CoverageCell, ScopeChip, ScopeLine, VerificationStrip, closurePct,
  hasVerification, scopeNote, scopeTag,
} from "./verification";

/* `model_ref` is `coverage:<model_id>@v<version>` (CoverageModel.ref). The two
 * model ids that exist are `cov-baseline-deterministic` and
 * `cov-conversational_transactional`; these fixtures used to carry
 * `coverage:baseline@v1` and `coverage:conversational@v2`, which name no model
 * the backend can produce. Nothing renders the string — it is only tested for
 * presence — which is exactly why the invented values survived. A fixture that
 * cannot occur is not evidence the screen handles what will occur.
 *
 * The version is `v3` because both shipped models are at 3: the `session_shape`
 * split bumped them. Correcting the id to a real one while leaving `@v2` behind
 * still named a ref no run can emit — half a fix reads as a whole one. If a
 * model version moves again, these move with it. */
const UNSCOPED = { task_success_rate: 0.6 };
const BASELINE = {
  task_success_rate: 0.6,
  coverage: {
    model_ref: "coverage:cov-baseline-deterministic@v3", baseline: true,
    trace_closure: 0.42,
    closure_target: 0.95, closed: false, limits: "Baseline model only.",
    assertions: { total: 8, violations: 1, unexercised: 3, verdict: "FAIL" },
  },
};
const FITTED = {
  task_success_rate: 0.9,
  coverage: {
    model_ref: "coverage:cov-conversational_transactional@v3", baseline: false,
    trace_closure: 0.97, closure_target: 0.95, closed: true,
    assertions: { total: 8, violations: 0, unexercised: 0, verdict: "PASS" },
  },
};

const html = (el: React.ReactElement) => renderToStaticMarkup(el);

describe("scope vocabulary", () => {
  it("tags a rate with no coverage model as unscoped", () => {
    expect(scopeTag(UNSCOPED)).toBe(" (unscoped)");
    expect(scopeNote(UNSCOPED)).toMatch(/never exercised/);
  });

  it("tags a baseline-only rate as baseline scope, not as unscoped", () => {
    expect(scopeTag(BASELINE)).toBe(" (baseline scope)");
    expect(scopeNote(BASELINE)).toBe("Baseline model only.");
  });

  it("adds no caveat to a fitted model — it needs none", () => {
    expect(scopeTag(FITTED)).toBe("");
    expect(html(<ScopeChip sc={FITTED} />)).toBe("");
  });

  it("renders a visible chip for anything less than a fitted model", () => {
    expect(html(<ScopeChip sc={UNSCOPED} />)).toContain("unscoped");
    expect(html(<ScopeChip sc={BASELINE} />)).toContain("baseline");
  });

  it("reports closure only when a model actually applied", () => {
    expect(closurePct(UNSCOPED)).toBeNull();
    expect(closurePct(BASELINE)).toBe(42);
    expect(closurePct(FITTED)).toBe(97);
  });

  it("knows when a record carries no verification at all", () => {
    expect(hasVerification(UNSCOPED)).toBe(false);
    expect(hasVerification(BASELINE)).toBe(true);
  });
});

describe("CoverageCell — the list form", () => {
  it("says 'not measured' rather than showing a reassuring blank", () => {
    const out = html(<CoverageCell sc={UNSCOPED} />);
    expect(out).toContain("not measured");
    expect(out).not.toContain("%");
  });

  it("leads with closure and names broken properties", () => {
    const out = html(<CoverageCell sc={BASELINE} />);
    expect(out).toContain("42%");
    expect(out).toContain("1 broken");
    expect(out).toContain("3 unexercised");
  });

  it("says properties held when none broke — never 'passed'", () => {
    const out = html(<CoverageCell sc={FITTED} />);
    // strip markup: "broken" legitimately appears in the explanatory tooltip,
    // what must not appear is a broken COUNT in the visible cell.
    const visible = out.replace(/<[^>]+>/g, " ");
    expect(visible).toContain("held");
    expect(visible).not.toMatch(/\d+\s+broken/);
    expect(visible).not.toContain("passed");
  });

  it("still reports unexercised properties on an otherwise clean run", () => {
    const clean = {
      coverage: {
        ...FITTED.coverage,
        assertions: { total: 8, violations: 0, unexercised: 2, verdict: "PASS" },
      },
    };
    expect(html(<CoverageCell sc={clean} />)).toContain("2 unexercised");
  });
});

describe("VerificationStrip — the run headline", () => {
  it("renders nothing when there is nothing verified to report", () => {
    expect(html(<VerificationStrip sc={UNSCOPED} />)).toBe("");
  });

  it("leads with closure against its target", () => {
    const out = html(<VerificationStrip sc={BASELINE} />);
    expect(out).toContain("Coverage closure");
    expect(out).toContain("42%");
    expect(out).toContain("95%");
  });

  it("shows never-exercised properties alongside the verdict", () => {
    const out = html(<VerificationStrip sc={BASELINE} />);
    expect(out).toContain("Never exercised");
    expect(out).toContain("FAIL");
  });
});

describe("no console copy makes an unbounded safety claim", () => {
  /* The platform's guard (schema/attestation.BANNED_CLAIMS) is a blunt substring
   * match applied to Python-rendered artifacts. Console copy is rendered in the
   * browser and never passes through it, so the same rule is enforced here —
   * note "is safe" is a substring of "is safer", which is how the dashboard's
   * old empty-state copy slipped through. */
  const BANNED = ["is safe", "certified safe", "certified secure", "guaranteed safe",
                  "proven safe", "verified safe", "risk-free", "fully secure",
                  "provably safe", "completely safe"];

  // `./landing/*.ts` is in the glob deliberately. It used to scan `./pages/*.tsx`
  // ONLY, which is exactly how the landing's copy constants went unguarded — and
  // the sentence that claimed the baseline covers `session_shape` lived there,
  // in `landing/data.ts`, contradicting the wheel three sections above it. The
  // marketing copy is the surface most exposed to an unbounded claim and was the
  // one surface this guard could not see.
  const modules = import.meta.glob(["./pages/*.tsx", "./landing/*.ts"],
                                   { eager: true, query: "?raw",
                                     import: "default" });

  it("across every console page and landing copy source", () => {
    // The platform's own vacuity rule applies to its own tests: a guard that
    // scanned nothing would pass and prove nothing.
    expect(Object.keys(modules).length).toBeGreaterThan(10);
    expect(Object.keys(modules).some((p) => p.includes("/landing/"))).toBe(true);
    const offenders: string[] = [];
    for (const [path, src] of Object.entries(modules)) {
      const low = String(src).toLowerCase();
      for (const claim of BANNED) {
        if (low.includes(claim)) offenders.push(`${path}: ${claim}`);
      }
    }
    expect(offenders).toEqual([]);
  });
});

describe("ScopeLine", () => {
  it("states plainly that an unscoped rate is not a claim about the untested", () => {
    const out = html(<ScopeLine sc={UNSCOPED} />);
    expect(out).toMatch(/never put through|never exercised|not a statement/);
  });

  it("names the baseline model as archetype-independent", () => {
    expect(html(<ScopeLine sc={BASELINE} />)).toContain("archetype-independent");
  });
});
