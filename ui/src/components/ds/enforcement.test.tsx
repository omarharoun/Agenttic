/* CONSOLE-DESIGN §5 — the two enforcement rules, tested so they cannot be
 * merely followed. Each test asserts the SPECIFIC thing the rule forbids, not
 * that the component renders something.
 *
 *   §5.1/§5.2  Provisional is a distinct TYPE, derived from record presence, not
 *              from a self-asserted `calibrated` boolean (the F1 fail-open).
 *   §5.3       A verdict colour cannot be emitted without its scope fence.
 */
import { describe, it, expect } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import React from "react";
import * as ds from "./index";
import {
  criterionStatus, ProvenanceBadge, ScorecardCard, VerdictWithScope,
} from "./index";

const html = (el: React.ReactElement) => renderToStaticMarkup(el);
const NO_HEX = /#[0-9a-fA-F]{3,8}\b/;

// ---- §5.1/§5.2 — provisional is derived fail-closed, never self-certified ---

describe("criterionStatus — fail-closed, mirrors backend criterion_status()", () => {
  it("code scorer is deterministic", () => {
    expect(criterionStatus({ scorer: "code" })).toBe("deterministic");
  });
  it("judge WITH a stored record (alpha present) is calibrated", () => {
    expect(criterionStatus({ scorer: "judge", alpha: 0.89 })).toBe("calibrated");
  });
  it("a calibrated badge shows both alpha and the ceiling when present", () => {
    const out = html(<ProvenanceBadge scorer="judge" calibrated alpha={0.89} ceiling={0.94} />);
    expect(out).toContain("α=0.89");
    expect(out).toContain("ceiling 0.94");
    expect(out).toContain("ds-badge--cal");
  });
  it("judge WITHOUT a record is provisional", () => {
    expect(criterionStatus({ scorer: "judge" })).toBe("provisional");
  });
  it("fi WITHOUT a record is provisional — not silently 'measured'", () => {
    expect(criterionStatus({ scorer: "fi" })).toBe("provisional");
  });
});

describe("a payload cannot self-certify calibration (the exact F1 fail-open)", () => {
  it("calibrated:true with NO stored record still renders provisional", () => {
    // The payload claims calibrated but ships no calibration record (no alpha).
    // Trusting the flag is the bug F1 fixed on the server; the client must not
    // reintroduce it. Record presence, never the flag.
    const out = html(<ProvenanceBadge scorer="judge" calibrated alpha={undefined} />);
    expect(out).toContain("ds-badge--prov");
    expect(out).not.toContain("ds-badge--cal");
  });

  it("a provisional criterion scoring 0.92 renders provisional tone, NOT pass", () => {
    // 0.92 is a pass by value alone. But with no record it is provisional, and a
    // provisional score must never sit on the pass→fail ramp — the status wins.
    const out = html(
      <ScorecardCard rows={[
        { name: "tone", scorer: "judge", calibrated: true, score: 0.92 },
      ]} />,
    );
    expect(out).toContain("ds-score--provisional");
    expect(out).not.toContain("ds-score--pass");
  });
});

// ---- §5.3 — the verdict colour cannot be emitted without its scope fence -----

describe("VerdictWithScope — verdict colour ⟹ scope fence, structurally", () => {
  const clean: ds.VerdictScope = {
    status: "PASS", scoped: true, coverageHoles: 0, notMeasured: 0,
    assertionsUnexercised: 0, provisionalCriteria: 0,
    closurePct: 100, closureTarget: 95,
  };

  it("always renders the scope fence, even when every qualifier is zero", () => {
    const out = html(<VerdictWithScope scope={clean} />);
    expect(out).toContain("vws-fence");         // the fence is unconditional
    expect(out).toContain("vws-verdict--pass"); // and the verdict is there too
  });

  it("surfaces every non-zero qualifier in the fence", () => {
    const out = html(<VerdictWithScope scope={{
      ...clean, coverageHoles: 3, notMeasured: 2, assertionsUnexercised: 1,
      provisionalCriteria: 4,
    }} />);
    for (const n of ["3", "2", "1", "4"]) expect(out).toContain(n);
    expect(out).toContain("vws-fence");
  });

  it("an absent coverage model reads 'unscoped', never a false all-clear", () => {
    const out = html(<VerdictWithScope scope={{ ...clean, scoped: false }} />);
    expect(out.toLowerCase()).toContain("unscoped");
    expect(out).not.toContain("full scope");
  });

  it("a null verdict never renders a pass/fail colour", () => {
    const out = html(<VerdictWithScope scope={{ ...clean, status: null }} />);
    expect(out).not.toContain("vws-verdict--pass");
    expect(out).toContain("vws-verdict--none");
    expect(out).toContain("vws-fence");         // fence still present
  });

  it("emits no raw hex (token-driven)", () => {
    expect(html(<VerdictWithScope scope={clean} />)).not.toMatch(NO_HEX);
  });
});

describe("structural: the verdict colour is emitted from exactly one place", () => {
  // Same Vite ?raw glob the Hard-Rule-48 tests use. The colour class that paints
  // a verdict must exist in ONE source file — the one that always renders the
  // fence — so no other component can paint a verdict without the fence.
  const SOURCES = import.meta.glob("../../**/*.{ts,tsx}", {
    query: "?raw", import: "default", eager: true,
  }) as Record<string, string>;

  it("the `vws-verdict--` colour class literal lives in exactly one source file", () => {
    const files = Object.entries(SOURCES).filter(
      ([p, c]) => !p.includes(".test.") && /vws-verdict--/.test(c));
    expect(files.length).toBe(1);
  });

  it("exposes VerdictWithScope as the ONLY verdict entry point (no bare pill)", () => {
    expect(typeof ds.VerdictWithScope).toBe("function");
    const verdictish = Object.keys(ds).filter((k) => /verdict|pill/i.test(k));
    expect(verdictish).toEqual(["VerdictWithScope"]);
  });
});
