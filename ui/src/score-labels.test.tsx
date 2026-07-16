/* The scan surfaces a COMPOSITE safety score (X/100, weighted across dimensions)
   while the results surfaces a PASS RATE (share of cases passed). Both are
   correct but, side by side and unlabeled, they read as a contradiction. These
   tests pin the disambiguation copy: each number's tooltip must name the OTHER
   number and say they measure different things, so neither looks "wrong". */
import { describe, it, expect } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import React from "react";
import { PASS_MEANING, SCORE_MEANING } from "./workflow/templates";

describe("score vs pass-rate disambiguation copy", () => {
  it("PASS_MEANING labels the pass rate and points at the composite score", () => {
    expect(PASS_MEANING.toLowerCase()).toContain("pass rate");
    expect(PASS_MEANING.toLowerCase()).toContain("composite safety score");
    expect(PASS_MEANING.toLowerCase()).toContain("different");
  });

  it("SCORE_MEANING labels the composite score and points at the pass rate", () => {
    expect(SCORE_MEANING.toLowerCase()).toContain("composite safety score");
    expect(SCORE_MEANING.toLowerCase()).toContain("pass rate");
    expect(SCORE_MEANING.toLowerCase()).toContain("different");
  });

  it("the two blurbs are distinct (they explain the two distinct numbers)", () => {
    expect(SCORE_MEANING).not.toEqual(PASS_MEANING);
  });

  it("a composite-score chip carries the SCORE_MEANING tooltip", () => {
    // mirrors the scan verdict markup in ScanExperience / CertConversation
    const html = renderToStaticMarkup(
      <div className="scan-verdict-sub" title={SCORE_MEANING}>
        Composite safety score {97.6}/100
      </div>,
    );
    expect(html).toContain("Composite safety score");
    expect(html).toContain("title=");
    expect(html).toContain("different things");
  });
});
