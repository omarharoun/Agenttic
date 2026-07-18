/* SPEC-4 Step 21.1 — the responsive contract for the READ surfaces, asserted at
   the source level (jsdom has no layout engine, so documentElement.scrollWidth
   is always 0 there — a true overflow measurement belongs in the Playwright
   pass; here we lock in the CSS rules and the honest editor notice that make the
   ≤360px surfaces render without page-level horizontal scroll). */
import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));
const read = (p: string) => readFileSync(join(here, p), "utf8");

describe("responsive read surfaces (≤360px)", () => {
  const theme = read("theme.css");

  it("collapses every multi-column read-surface grid to one column at ≤360px", () => {
    const block = theme.slice(theme.indexOf("@media (max-width: 360px)"));
    expect(block).toContain("@media (max-width: 360px)");
    // Dashboard stats + cards go 1-col in theme.css.
    for (const sel of [".dash-stats", ".dash-grid"]) {
      expect(block).toMatch(new RegExp(`\\${sel}\\s*\\{[^}]*grid-template-columns:\\s*1fr`));
    }
    // The scorecard executive header collapses in its own stylesheet, where the
    // override reliably wins the cascade over the base .sd-exec rule.
    const sd = read("pages/ScorecardDetail.css");
    const sdBlock = sd.slice(sd.indexOf("@media (max-width: 360px)"));
    expect(sdBlock).toMatch(/\.sd-exec\s*\{\s*grid-template-columns:\s*1fr/);
  });

  it("keeps wide tables scrolling INSIDE their box, not the page", () => {
    // .table-wrap owns the horizontal overflow so the page never scrolls.
    expect(theme).toMatch(/\.table-wrap\s*\{[^}]*overflow-x:\s*auto/);
  });

  it("gives the node-canvas editor an honest small-screen notice, not a broken canvas", () => {
    // The notice is hidden by default and shown ≤768px, where the canvas is hidden.
    expect(theme).toMatch(/\.editor-smallnote\s*\{\s*display:\s*none/);
    const tablet = theme.slice(theme.indexOf("@media (max-width: 768px)"));
    expect(tablet).toMatch(/\.editor-body\s*\{\s*display:\s*none/);

    const editor = read("pages/EditorPage.tsx");
    expect(editor).toContain("editor-smallnote");
    expect(editor).toContain("The workflow editor needs a wider screen");
  });

  it("stills the status page's live pip under reduced motion", () => {
    const status = read("pages/StatusPage.css");
    const reduced = status.slice(status.indexOf("prefers-reduced-motion"));
    expect(reduced).toMatch(/\.live-pip\s*\{\s*animation:\s*none/);
  });
});
