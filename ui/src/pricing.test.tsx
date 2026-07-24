/* The public /pricing page after the closed-source reposition.
 *
 * The landing was repositioned to sell an engagement rather than a download; a
 * pricing page still advertising $29/$99 self-serve plans would contradict it on
 * the very next click. These tests pin the position so it cannot drift back:
 * no price list, no open-source vocabulary, and the metered console preview
 * disclosed rather than hidden — it is a real cost, just not the thing sold.
 */
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";

import { PricingPage } from "./pages/PricingPage";

const html = renderToStaticMarkup(
  <MemoryRouter><PricingPage /></MemoryRouter>);
const text = html.replace(/<[^>]+>/g, " ").replace(/\s+/g, " ");

describe("/pricing — closed-source position", () => {
  it("has exactly one <h1>", () => {
    expect((html.match(/<h1\b/g) || []).length).toBe(1);
  });

  it("publishes no price list", () => {
    // The free-credit disclosure is the ONLY money figure on the page; any other
    // dollar amount would be a published price.
    const prices = text.match(/\$\d[\d,]*(\.\d+)?/g) || [];
    expect(prices.length).toBeLessThanOrEqual(1);
    // no subscription furniture (a plan card's price/interval and its CTAs)
    expect(text).not.toMatch(/\$\d+\s*\/\s*(month|year)/i);
    expect(text).not.toMatch(/Most popular|Choose Starter|Choose Pro|Start free/i);
    expect(text).not.toMatch(/\d+\s+credits every month/i);
  });

  it("says what it is sold as", () => {
    expect(text).toMatch(/engagement/i);
    expect(text).toMatch(/Request a briefing/i);
  });

  it("carries no open-source or download vocabulary", () => {
    for (const banned of [/\bMIT\b/, /open[- ]source/i, /github/i,
                          /\bpip install\b/i, /\bnpm install\b/i, /\$0\b/]) {
      expect(text).not.toMatch(banned);
    }
  });

  it("explains what sets the price instead of naming one", () => {
    expect(text).toMatch(/What sets the price/i);
    expect(text).toMatch(/no price list|no public price|There is no price list/i);
  });

  it("discloses the metered console preview rather than hiding it", () => {
    expect(text).toMatch(/credit/i);
    expect(text).toMatch(/metered/i);
    // and is explicit that the preview is not the thing being sold
    expect(text).toMatch(/not the verification engagement/i);
  });

  it("does not promise signed evidence from the free preview", () => {
    expect(text).toMatch(/does not produce signed evidence/i);
  });

  it("makes no unbounded safety claim", () => {
    for (const claim of ["is safe", "proven safe", "guaranteed safe",
                         "certified secure", "risk-free", "fully secure"]) {
      expect(text.toLowerCase()).not.toContain(claim);
    }
  });
});
