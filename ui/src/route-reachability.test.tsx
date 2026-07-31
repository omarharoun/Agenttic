/* Every internal destination a page names must exist.
 *
 * This file exists because of a real defect: /engine and /app/scenarios were
 * built, styled and tested, and neither was ever added to a route table. Both
 * shipped zero bytes. Worse for a verification product, EnginePage's own copy
 * asserted the opposite in two places — a button reading "Open the scenario
 * console" and the sentence "The console screen at /app/scenarios renders the
 * same stored run in full". A page that argues evidence must be checkable was
 * making a false statement about the product it shipped in.
 *
 * A "does the route exist" unit test would have caught only the instance. What
 * is asserted here is the RULE: crawl the links a page actually renders and
 * resolve each one against the real route tables. Add a link to a destination
 * nobody routed and this fails, whichever page and whichever destination.
 *
 * The matcher is react-router's own `matchRoutes` against the exported `routes`
 * array — not a hand-rolled path comparison — so what is tested is what the
 * router will do. `/app/*` is a splat, and it matches ANY /app/… path including
 * ones that land on AppShell's `<Route path="*" element={<NotFoundPage />} />`.
 * So a match on the splat is not an answer: the remainder is resolved a second
 * time against AppShell's own child paths, read out of its source. Skipping
 * that step would call /app/scenarios reachable while it was rendering "No
 * console page matches /app/scenarios" — the exact bug, passing.
 *
 * House style here is `renderToStaticMarkup`, no jsdom (see engine-page.test).
 */
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter, matchRoutes } from "react-router-dom";
import { describe, expect, it } from "vitest";

import { routes } from "./App";
import { EnginePage } from "./pages/EnginePage";

/* AppShell's console route table lives as JSX inside the component, and the
 * component cannot be rendered here (it reads sessionStorage and guards on
 * /api/me before it ever renders a <Route>). Its source is read instead, the
 * same `?raw` route engine-page.test.tsx uses to check the page's quotes
 * against the Python they came from. */
import APPSHELL_RAW from "./AppShell.tsx?raw";

/** The child paths of `/app`, exactly as declared. `path="*"` is excluded on
 *  purpose: it IS the not-found page, so treating it as a route would make
 *  every possible /app/… string "reachable". */
function consoleChildPaths(src: string): Set<string> {
  const found = new Set<string>();
  for (const m of src.matchAll(/<Route\s+path="([^"]*)"/g)) {
    const p = m[1];
    if (p === "*") continue;
    found.add(p === "/" ? "" : p);
  }
  return found;
}

const CONSOLE_PATHS = consoleChildPaths(APPSHELL_RAW);

/** Does the app actually serve `path`? Top level via the router's own matcher;
 *  anything absorbed by the `/app/*` splat is then checked against AppShell. */
function reachable(path: string): boolean {
  const matches = matchRoutes(routes as never, path);
  if (!matches || matches.length === 0) return false;
  const matched = matches[matches.length - 1].route as { path?: string };
  if (matched.path !== "/app/*") return true;
  // "/app/scenarios" -> "scenarios"; "/app" -> ""
  const rest = path.replace(/^\/app\/?/, "").replace(/[?#].*$/, "");
  return CONSOLE_PATHS.has(rest);
}

/** The internal destinations a rendered page links to. `<Link to>` renders as
 *  an `<a href>`, so this reads what a browser would follow. External links and
 *  in-page anchors are not routes and are skipped. */
function internalLinks(markup: string): string[] {
  const out = new Set<string>();
  for (const m of markup.matchAll(/href="([^"]*)"/g)) {
    const href = m[1];
    if (!href.startsWith("/")) continue;   // http(s):, mailto:, #anchor
    out.add(href.replace(/[?#].*$/, ""));
  }
  return [...out];
}

describe("the route tables were read correctly", () => {
  it("finds AppShell's console pages and not its not-found catch-all", () => {
    // A guard on the source-reading above: if the regex silently matched
    // nothing, every assertion below would pass vacuously.
    expect(CONSOLE_PATHS.size).toBeGreaterThan(10);
    expect(CONSOLE_PATHS.has("results")).toBe(true);
    expect(CONSOLE_PATHS.has("*")).toBe(false);
  });

  it("resolves the routes that exist", () => {
    for (const p of ["/", "/scan", "/methodology", "/certified",
                     "/certified/anything", "/app", "/app/results"]) {
      expect(reachable(p), `${p} should resolve`).toBe(true);
    }
  });

  /* The dominant defect family in this codebase is substring matching. A route
   * check that answered on "starts with" or "contains" would have called
   * /app/scenarios reachable all along, because /app/* matched it. */
  it("does not resolve a path that merely contains a real one", () => {
    for (const p of ["/enginex", "/engin", "/scanner", "/app/resultsx",
                     "/app/scenarios/extra", "/app/nope", "/xengine"]) {
      expect(reachable(p), `${p} must not resolve`).toBe(false);
    }
  });
});

describe("the destinations /engine names", () => {
  const markup = renderToStaticMarkup(
    <MemoryRouter><EnginePage /></MemoryRouter>);

  it("is itself routed — the page cannot ship zero bytes", () => {
    expect(reachable("/engine")).toBe(true);
  });

  it("links only to destinations that exist", () => {
    const links = internalLinks(markup);
    expect(links.length).toBeGreaterThan(2);
    for (const href of links) {
      expect(reachable(href), `/engine links to ${href}, which is not routed`)
        .toBe(true);
    }
  });

  it("keeps the promise it makes about the console screen in prose", () => {
    // The page states, in words, that a console screen at this path renders the
    // stored run. That sentence is a claim about the product; this is the check
    // that it is true. Both the sentence and the button are asserted, because
    // the defect shipped with both.
    const text = markup.replace(/<[^>]+>/g, " ").replace(/\s+/g, " ");
    expect(text).toContain("The console screen at /app/scenarios");
    expect(text).toContain("Open the scenario console");
    expect(reachable("/app/scenarios")).toBe(true);
  });
});
