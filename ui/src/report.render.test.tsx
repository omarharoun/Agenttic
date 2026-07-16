/* Functional render check for the redesigned run report. The report arrives as
   markdown; <Markdown> must render it as a real formatted document — headings
   become <h*>, bold becomes <strong>, pipe tables become bordered <table>s with
   right-aligned numeric columns — and the literal markdown tokens (##, **, the
   `|` table pipes) must NOT survive into the output. Raw HTML in the source must
   be neutralised, never rendered as live markup (untrusted agent output). */
import { describe, it, expect } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import React from "react";
import { Markdown } from "./components/Markdown";

const REPORT = [
  "# Agent Evaluation Scorecard — `demo-agent`",
  "",
  "## Executive summary",
  "",
  "The agent passed **3 of 4** scored cases (task success rate 75%).",
  "",
  "## Results by test case",
  "",
  "| Test case | Result | Cost (USD) | Latency (ms) | Steps |",
  "|---|---|---|---|---|",
  "| `case-1` | PASS | 0.0021 | 1234 | 5 |",
  "| `case-2` | FAIL | 0.0043 | 2210 | 8 |",
  "",
  "## Recommendations",
  "",
  "1. **Improve `no_secret_leak`**: the agent leaked a credential.",
].join("\n");

const render = (md: string) =>
  renderToStaticMarkup(React.createElement(Markdown, null, md));

describe("run report renders markdown as a document", () => {
  const html = render(REPORT);

  it("wraps the document in the styled report surface", () => {
    expect(html).toContain("report-doc");
  });

  it("renders '##' headings as real <h1>/<h2>, not literal text", () => {
    expect(html).toMatch(/<h1[^>]*>/);
    expect(html).toMatch(/<h2[^>]*>Executive summary<\/h2>/);
    // the raw markdown tokens must be gone
    expect(html).not.toContain("## Executive summary");
    expect(html).not.toContain("# Agent Evaluation");
  });

  it("renders '**bold**' as <strong>, not literal asterisks", () => {
    expect(html).toMatch(/<strong>3 of 4<\/strong>/);
    expect(html).not.toContain("**3 of 4**");
  });

  it("renders a pipe table as a bordered <table> with header + rows", () => {
    expect(html).toContain("report-table-wrap");
    expect(html).toMatch(/<table[^>]*>/);
    expect(html).toMatch(/<thead>/);
    expect(html).toMatch(/<th[^>]*>Test case<\/th>/);
    expect(html).toMatch(/<td[^>]*>PASS<\/td>/);
    // literal pipe-table syntax must NOT survive
    expect(html).not.toContain("| Test case |");
    expect(html).not.toContain("|---|");
  });

  it("right-aligns numeric columns via the .num class", () => {
    // the "1234" latency / "0.0021" cost cells are tagged numeric…
    expect(html).toMatch(/<td class="num">1234<\/td>/);
    expect(html).toMatch(/<td class="num">0\.0021<\/td>/);
    // …while a text cell like PASS is not
    expect(html).toMatch(/<td[^>]*>PASS<\/td>/);
    expect(html).not.toMatch(/<td class="num">PASS<\/td>/);
  });

  it("renders inline `code` as <code>, not backticks", () => {
    expect(html).toMatch(/<code>demo-agent<\/code>/);
    expect(html).not.toContain("`demo-agent`");
  });
});

describe("run report is XSS-safe (raw HTML disabled)", () => {
  it("neutralises embedded HTML/script from untrusted agent output", () => {
    const html = render(
      "## Summary\n\nAgent said: <script>alert(1)</script> and <img src=x onerror=alert(2)>.");
    // no LIVE markup: neither a real <script> tag nor a real <img> element
    expect(html).not.toContain("<script>");
    expect(html).not.toContain("<img");
    // the dangerous markup survives only as escaped, inert text
    expect(html).toContain("&lt;script&gt;");
    expect(html).toContain("&lt;img");
  });
});
