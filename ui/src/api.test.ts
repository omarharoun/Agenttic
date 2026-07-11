import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "./api";

/** These exercise the scorecard "report" + "PDF" client wiring — the exact
 *  actions behind the per-row buttons on the Resources → Scorecards table. The
 *  report button was silently failing because the fetch never checked
 *  `response.ok`; a non-2xx body was handed to the UI as if it were a report. */

// api.ts touches localStorage + document.cookie via authHeaders(); stub them so
// the module runs under vitest's node environment.
beforeEach(() => {
  vi.stubGlobal("localStorage", {
    getItem: () => "",
    setItem: () => {},
    removeItem: () => {},
  });
  vi.stubGlobal("document", { cookie: "" });
});
afterEach(() => vi.unstubAllGlobals());

/** Record every fetch call while returning a canned response. */
function stubFetch(response: () => Response) {
  const calls: Array<[string, RequestInit | undefined]> = [];
  vi.stubGlobal("fetch", (url: string, init?: RequestInit) => {
    calls.push([url, init]);
    return Promise.resolve(response());
  });
  return calls;
}

describe("scorecardReport", () => {
  it("GETs the scorecard report route and returns its text", async () => {
    const calls = stubFetch(() =>
      new Response("# Agent Evaluation Scorecard", { status: 200 }));

    const text = await api.scorecardReport("sc-123");

    expect(calls).toHaveLength(1);
    expect(calls[0][0]).toBe("/api/scorecards/sc-123/report");
    expect(calls[0][1]?.method ?? "GET").toBe("GET");
    expect(text).toContain("Agent Evaluation Scorecard");
  });

  it("rejects on a non-ok response instead of surfacing the error body", async () => {
    stubFetch(() => new Response("Internal Server Error", { status: 500 }));

    await expect(api.scorecardReport("sc-500")).rejects.toThrow();
  });
});

describe("scorecardPdf", () => {
  it("targets a distinct report.pdf route and returns a blob", async () => {
    const calls = stubFetch(() =>
      new Response(new Blob(["%PDF-1.7"]), { status: 200 }));

    const blob = await api.scorecardPdf("sc-123");

    expect(calls[0][0]).toBe("/api/scorecards/sc-123/report.pdf");
    expect(blob).toBeInstanceOf(Blob);
  });
});
