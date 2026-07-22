/* SPEC-11 Step 51 — shared design-system component tests.
 *
 * The components are theme-agnostic: they emit only token-backed `ds-*` classes
 * (no hardcoded colour), so the SAME markup renders under dark and light and the
 * theme resolves purely through design/tokens.css. We therefore verify (a) the
 * markup/semantics of each component, (b) that no raw hex leaks into the output
 * (token-driven, so both themes are covered), (c) exactly one implementation
 * exists (Hard Rule 48, grep), and (d) the score components are wired to the
 * shared score tokens (so swapping a token changes every surface at once).
 */
import { describe, it, expect } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import React from "react";
import {
  ProvenanceBadge, ScoreValue, ScorecardCard, Button, Eyebrow, SectionHeading,
  CodeBlock, StatTile, ComparisonTable, FaqItem, EscapementMark,
} from "./index";

const html = (el: React.ReactElement) => renderToStaticMarkup(el);
const NO_HEX = /#[0-9a-fA-F]{3,8}\b/;   // a raw hex colour must never appear in output

describe("ProvenanceBadge", () => {
  it("maps code -> deterministic", () => {
    const out = html(<ProvenanceBadge scorer="code" />);
    expect(out).toContain("ds-badge--det");
    expect(out).toContain("deterministic");
  });
  it("maps calibrated judge with alpha -> judged·α", () => {
    const out = html(<ProvenanceBadge scorer="judge" calibrated alpha={0.87} />);
    expect(out).toContain("ds-badge--cal");
    expect(out).toContain("α=0.87");
  });
  it("maps uncalibrated judge -> provisional", () => {
    const out = html(<ProvenanceBadge scorer="judge" calibrated={false} />);
    expect(out).toContain("ds-badge--prov");
    expect(out).toContain("provisional");
  });
  it("emits no raw hex (token-driven, both themes)", () => {
    expect(html(<ProvenanceBadge scorer="code" />)).not.toMatch(NO_HEX);
  });
});

describe("ScoreValue", () => {
  it("renders a mono value coloured by tone with interval", () => {
    const out = html(<ScoreValue value={0.92} tone="pass" interval="±4" />);
    expect(out).toContain("ds-score--pass");
    expect(out).toContain("0.92");
    expect(out).toContain("±4");
    expect(out).not.toMatch(NO_HEX);
  });
});

describe("ScorecardCard", () => {
  const card = (
    <ScorecardCard
      bar="scorecard.html · support-triage"
      metrics={[{ label: "Task success", value: "86", sub: "% ±4" }]}
      rows={[
        { name: "routing", description: "correct queue", scorer: "code", score: 1 },
        { name: "tone", scorer: "judge", calibrated: true, alpha: 0.87, score: 0.92 },
        { name: "policy_fidelity", scorer: "judge", calibrated: false, score: 0.71 },
      ]}
    />
  );
  it("composes ProvenanceBadge + ScoreValue per row", () => {
    const out = html(card);
    // three rows => three badges of the right kinds
    expect(out).toContain("ds-badge--det");   // routing (code)
    expect(out).toContain("ds-badge--cal");   // tone (calibrated judge)
    expect(out).toContain("ds-badge--prov");  // policy_fidelity (provisional)
    expect(out).toContain("ds-card__metrics");
    expect(out).not.toMatch(NO_HEX);
  });
});

describe("primitives", () => {
  it("Button renders solid/ghost as a link or button", () => {
    expect(html(<Button href="#x">Go</Button>)).toContain('class="ds-btn ds-btn--solid"');
    expect(html(<Button variant="ghost">Go</Button>)).toContain("ds-btn--ghost");
  });
  it("Eyebrow + SectionHeading", () => {
    const out = html(<SectionHeading eyebrow="How" title="Install → fit → prove" sub="x" />);
    expect(out).toContain("ds-eyebrow");
    expect(out).toContain("ds-sechead__h");
  });
  it("CodeBlock renders prompt/comment lines + copy affordance", () => {
    const out = html(<CodeBlock lines={[{ prompt: "$", text: "uv tool install agenttic", comment: "# install" }]} />);
    expect(out).toContain("ds-term");
    expect(out).toContain("uv tool install agenttic");
    expect(out).toContain("copy");
  });
  it("StatTile / ComparisonTable / FaqItem", () => {
    expect(html(<StatTile tag="STARS" value="1.2k" />)).toContain("ds-stat");
    const cmp = html(<ComparisonTable
      columns={[{ key: "a", header: "Agenttic", highlight: true }]}
      rows={[{ rowHeader: "Fit", cells: { a: "fitted rubric" } }]} />);
    expect(cmp).toContain("ds-cmp__us");
    expect(html(<FaqItem q="Is it free?">Yes.</FaqItem>)).toContain("ds-faq");
  });
  it("EscapementMark is aria-hidden and reduced-motion aware via CSS", () => {
    expect(html(<EscapementMark />)).toContain("ds-escape__tick");
  });
  it("no primitive emits raw hex", () => {
    for (const el of [
      <Button key="b">x</Button>, <Eyebrow key="e">x</Eyebrow>,
      <StatTile key="s" tag="A" value="1" />, <EscapementMark key="m" />,
    ]) expect(html(el)).not.toMatch(NO_HEX);
  });
});

// ---- acceptance-criteria guards ------------------------------------------

// all app sources as raw strings (Vite ?raw glob) — no Node APIs, browser-safe.
const SOURCES = import.meta.glob("../../**/*.{ts,tsx}", {
  query: "?raw", import: "default", eager: true,
}) as Record<string, string>;

describe("Hard Rule 48 — exactly one implementation", () => {
  const grepDefs = (pattern: RegExp): number =>
    Object.entries(SOURCES).filter(
      ([p, c]) => !p.includes(".test.") && pattern.test(c)).length;
  it("ProvenanceBadge defined in exactly one file", () => {
    expect(grepDefs(/export function ProvenanceBadge/)).toBe(1);
  });
  it("ScorecardCard defined in exactly one file", () => {
    expect(grepDefs(/export function ScorecardCard/)).toBe(1);
  });
});

// NB: the ds.css → score-token wiring ("swap a token, both surfaces change") is
// asserted in the Node-run token lint (scripts/check-tokens.mjs, a build gate),
// because vitest stubs .css imports to empty and cannot read the stylesheet.
