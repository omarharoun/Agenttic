import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { api, ApiError } from "./api";

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

/** Regression: the JSON API layer must never let a raw `JSON.parse` SyntaxError
 *  escape. A non-JSON body — a proxy's 502/504 HTML page, or an `/api/*` request
 *  that fell through to the HTML SPA shell as `200 text/html` — used to crash
 *  the whole app as "Unexpected Application Error! JSON.parse: unexpected
 *  character at line 1 column 1". Now it becomes a typed, catchable ApiError. */
describe("json() error handling", () => {
  const HTML = "<!DOCTYPE html><html><head><title>Agenttic</title></head></html>";

  it("does not throw a JSON.parse SyntaxError on a 200 text/html SPA-shell body", async () => {
    stubFetch(() => new Response(HTML, {
      status: 200, headers: { "content-type": "text/html; charset=utf-8" },
    }));

    const err = await api.me().then(() => null, (e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err.status).toBe(200);
    // the human message must NOT be a raw parser error
    expect(String(err.message)).not.toMatch(/JSON\.parse|unexpected character/i);
    expect(String(err.message)).toMatch(/expected json/i);
  });

  it("carries the HTTP status through on a non-JSON 502 proxy page", async () => {
    stubFetch(() => new Response("<html><body>502 Bad Gateway</body></html>", {
      status: 502, headers: { "content-type": "text/html" },
    }));

    const err = await api.me().then(() => null, (e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err.status).toBe(502);
    expect(String(err.message)).not.toMatch(/JSON\.parse/i);
  });

  it("still surfaces a structured {detail} JSON error with its message", async () => {
    stubFetch(() => new Response(JSON.stringify({ detail: "nope, not you" }), {
      status: 403, headers: { "content-type": "application/json" },
    }));

    const err = await api.me().then(() => null, (e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err.status).toBe(403);
    expect(err.message).toBe("nope, not you");
  });

  it("parses a normal JSON body on the happy path", async () => {
    stubFetch(() => new Response(JSON.stringify({ role: "admin", tenant: "t1", email: null, auth_method: "session" }), {
      status: 200, headers: { "content-type": "application/json" },
    }));

    const me = await api.me();
    expect(me.role).toBe("admin");
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
