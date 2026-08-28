/* Route reachability: every in-app link goes somewhere.
 *
 * THE DEFECT THIS FILE EXISTS TO CATCH. `ScenariosPage` — 904 lines, its own
 * test file, fully green — was imported by nothing. AppShell's route table had
 * no `scenarios` entry, so `/app/scenarios` fell through to the in-app 404. The
 * public `/engine` explainer closed with a solid-button CTA pointing straight at
 * it, and `/engine` was itself absent from App.tsx's table, so the CTA was a
 * 404 on a page nobody could open. `engine-page.test.tsx` asserted
 * `markup).toContain("/app/scenarios")` and passed the whole time: asserting a
 * link EXISTS is not asserting it GOES anywhere. Nothing else in the suite
 * could tell the difference, because a route table is data no test read.
 *
 * So this file reads the two route tables as text and resolves every routed
 * link in the tree against them. Source text rather than rendered output on
 * purpose: a link inside a branch this suite never renders still ships.
 *
 * WHOLE SEGMENTS, NEVER SUBSTRINGS. `/app/scenario` is not `/app/scenarios` and
 * `/engineering` is not `/engine`; a `startsWith` or an `includes` here would
 * call both of them fine. The negative tests at the bottom are what keep this
 * file from being the next place that mistake hides.
 *
 * SIBLING: `route-reachability.test.tsx` crawls the same rule from the other
 * end — it RENDERS /engine and follows the hrefs a browser would actually
 * receive, and it holds the page's two prose promises about /app/scenarios to
 * the route table. This file crawls every source file in the tree instead, so
 * it also sees links on pages no test renders. The two overlap and are worth
 * merging; neither is redundant as written.
 *
 * WHAT THIS DOES NOT CHECK: that a routed page is LINKED from a navigation.
 * As of this writing neither `/engine` nor `/app/scenarios` is in `SiteNav`'s
 * NAV_ITEMS or AppShell's NAV_GROUPS — /engine is reached by URL and
 * /app/scenarios from /engine's CTA. Both navs are inside committed Playwright
 * visual baselines (e2e/__screenshots__), so adding an entry invalidates them
 * and is owed work, recorded here rather than left to be discovered again.
 */
import { matchRoutes, type RouteObject } from "react-router-dom";
import { describe, expect, it } from "vitest";

import { routes } from "./App";
import APP_TSX from "./App.tsx?raw";
import SHELL_TSX from "./AppShell.tsx?raw";
import SERVER_APP_PY from "../../src/agenttic/server/app.py?raw";

/** Every source file that can hold a link, as text. */
const SOURCES: Record<string, string> = {
  ...(import.meta.glob("./**/*.tsx", {
    query: "?raw", eager: true, import: "default" }) as Record<string, string>),
  ...(import.meta.glob("./**/*.ts", {
    query: "?raw", eager: true, import: "default" }) as Record<string, string>),
};

const PAGES = Object.entries(SOURCES).filter(([f]) => !/\.test\.tsx?$/.test(f));

/* -------------------------------------------------------------------------- */
/* reading the two route tables                                               */
/* -------------------------------------------------------------------------- */

/** The top-level patterns in App.tsx's exported `routes` table. */
export function topRoutes(src: string): string[] {
  return [...src.matchAll(/\bpath:\s*"([^"]+)"/g)].map((m) => m[1]);
}

/** AppShell's console patterns, absolutized. The shell is mounted at `/app/*`,
 *  so its `"/"` is `/app`, `"results"` is `/app/results`, and its `"*"` is the
 *  in-app 404 — which is a route, and is why an unrouted console path renders a
 *  page instead of failing loudly. */
export function consoleRoutes(src: string): string[] {
  return [...src.matchAll(/<Route\s+path="([^"]+)"/g)]
    .map((m) => (m[1] === "/" ? "/app" : m[1] === "*" ? "/app/*" : `/app/${m[1]}`));
}

/* -------------------------------------------------------------------------- */
/* resolution                                                                 */
/* -------------------------------------------------------------------------- */

/** What a `${…}` in a template-literal target becomes. It can hold anything, so
 *  it may satisfy a route PARAM and never a fixed segment name. */
const DYN = ":dyn";

const segments = (p: string) => p.split("/").filter(Boolean);

/** Does `pattern` match `path`, one whole segment at a time? */
export function matches(pattern: string, path: string): boolean {
  const P = segments(pattern);
  const T = segments(path);
  for (let i = 0; i < P.length; i++) {
    if (P[i] === "*") return true;            // a wildcard swallows the tail
    if (i >= T.length) return false;
    if (P[i].startsWith(":")) continue;       // route param: any ONE segment
    if (T[i] === DYN) return false;           // an interpolation is not a promise
    if (P[i] !== T[i]) return false;          // whole-segment equality
  }
  return P.length === T.length;               // no prefix match: /a is not /a/b
}

/** The two link targets served by the API process rather than by the router:
 *  FastAPI's built-in Swagger UI and the raw schema, both matched in
 *  `server/app.py` before its SPA catch-all. Allowlisted here — and the
 *  allowlist is itself checked below, because an allowlist nobody verifies is
 *  just a hole with a comment over it. */
const SERVED_BY_THE_API = new Set(["/docs", "/openapi.json"]);

const ROUTES = [...topRoutes(APP_TSX), ...consoleRoutes(SHELL_TSX)];

export function resolves(path: string): boolean {
  if (SERVED_BY_THE_API.has(path)) return true;
  /* `/app/*` is a MOUNT POINT, not a destination: it hands the rest of the path
     to AppShell's own table, so a /app/… link has to be found THERE. Letting the
     wildcard answer would make every /app/… link resolve — including
     /app/scenarios, for the whole time it rendered the 404. */
  /* The top-level `*` is the public 404 and is excluded for exactly the same
     reason as `/app/*` above: it is where an unrouted path LANDS, not proof
     that the path resolves. Without this it matched first and returned true
     for every string ever passed here, which made every assertion below
     vacuous — including "resolves every one of them against a declared
     route", the one that is supposed to catch a dead link. */
  return ROUTES.some((r) => r !== "/app/*" && r !== "*" && matches(r, path));
}

/* -------------------------------------------------------------------------- */
/* reading the links                                                          */
/* -------------------------------------------------------------------------- */

export interface Target { path: string; where: string }

/** Every routed destination a source file names, in both shapes it is written:
 *  a quoted literal (`to="/pricing"`, `href="/docs"`) and a template literal
 *  (``to={`/certified/${id}`}``), whose interpolations collapse to {@link DYN}.
 *
 *  Two shapes are NOT routed destinations, and are named here rather than
 *  dropped in silence: a target beginning `#` is an in-page anchor, and one that
 *  does not begin `/` is absolute or external (``${origin}/.well-known/…``).
 *  Both fall out of the `startsWith("/")` guard after normalization. */
export function targetsIn(src: string, where: string): Target[] {
  const out: Target[] = [];
  const push = (raw: string) => {
    const path = raw
      .replace(/\$\{[^}]*\}/g, DYN)
      .split("#")[0]
      .split("?")[0];
    if (path.startsWith("/")) out.push({ path, where });
  };
  for (const m of src.matchAll(/\b(?:to|href)=\{?"([^"]*)"/g)) push(m[1]);
  for (const m of src.matchAll(/\b(?:to|href)=\{`([^`]*)`\}/g)) push(m[1]);
  return out;
}

/* ========================================================================== */

describe("the route tables", () => {
  it("routes the scenario console, bound to the page that renders it", () => {
    expect(consoleRoutes(SHELL_TSX)).toContain("/app/scenarios");
    expect(SHELL_TSX).toMatch(
      /<Route\s+path="scenarios"\s+element=\{<ScenariosPage\s*\/>\}\s*\/>/);
    expect(SHELL_TSX).toMatch(
      /import\s*\{\s*ScenariosPage\s*\}\s*from\s*"\.\/pages\/ScenariosPage"/);
  });

  it("routes the public engine explainer", () => {
    expect(topRoutes(APP_TSX)).toContain("/engine");
    expect(APP_TSX).toMatch(/path:\s*"\/engine",\s*element:\s*suspense\(<EnginePage\s*\/>\)/);
    expect(APP_TSX).toMatch(/import\("\.\/pages\/EnginePage"\)/);
  });

  it("declares each path exactly once", () => {
    /* Two <Route>s with the same path score identically and the tie is broken by
       declaration order, so the second can never be reached: dead code that
       reads as a working route. This is not hypothetical — fixing the defect
       above briefly left two `scenarios` rows in the table. */
    for (const table of [topRoutes(APP_TSX), consoleRoutes(SHELL_TSX)]) {
      expect(table.length).toBe(new Set(table).size);
    }
  });
});

describe("every in-app link goes somewhere", () => {
  const all = PAGES.flatMap(([f, src]) => targetsIn(src, f));

  it("found links to check at all", () => {
    /* The guard on the guard. A regex that matched nothing would make the
       assertion below vacuously green, which is the same shape of lie as an
       unexercised requirement reported as a pass. */
    expect(all.length).toBeGreaterThan(30);
    expect(all.map((t) => t.path)).toContain("/app/scenarios");
    expect(Object.keys(SOURCES)).toContain("./pages/ScenariosPage.tsx");
  });

  it("resolves every one of them against a declared route", () => {
    const dead = all.filter((t) => !resolves(t.path));
    expect(dead.map((d) => `${d.path}  <-  ${d.where}`)).toEqual([]);
  });

  it("lands the engine page's CTA on the console rather than the 404", () => {
    const eng = targetsIn(SOURCES["./pages/EnginePage.tsx"], "EnginePage.tsx");
    expect(eng.map((t) => t.path)).toContain("/app/scenarios");
    for (const t of eng) expect(resolves(t.path), `${t.path} is dead`).toBe(true);
  });
});

describe("resolution is whole-segment, never substring", () => {
  it("accepts the exact path and nothing that merely contains it", () => {
    expect(resolves("/app/scenarios")).toBe(true);
    expect(resolves("/engine")).toBe(true);

    expect(resolves("/app/scenario")).toBe(false);     // one short
    expect(resolves("/app/scenarioss")).toBe(false);   // one long
    expect(resolves("/engin")).toBe(false);
    expect(resolves("/engineering")).toBe(false);      // contains "engine"
    expect(resolves("/scenarios")).toBe(false);        // console path, no /app
  });

  it("treats a declared route as a destination, not a prefix", () => {
    expect(resolves("/app/scenarios/42")).toBe(false);
    expect(resolves("/pricing/enterprise")).toBe(false);
  });

  it("does not let the /app/* mount answer for the console table", () => {
    expect(topRoutes(APP_TSX)).toContain("/app/*");
    expect(resolves("/app/definitely-not-a-console-page")).toBe(false);
  });

  it("matches a route param against exactly one segment", () => {
    expect(resolves("/certified")).toBe(true);
    expect(resolves("/certified/abc123")).toBe(true);
    expect(resolves("/certified/abc123/extra")).toBe(false);
  });

  it("lets an interpolated segment satisfy a param but never a fixed name", () => {
    expect(matches("/certified/:id", `/certified/${DYN}`)).toBe(true);
    expect(matches("/certified/directory", `/certified/${DYN}`)).toBe(false);
  });

  it("normalizes both link shapes and skips the two that are not routes", () => {
    const t = targetsIn(
      'to="/pricing" href={"/status"} to={`/certified/${c.id}`} '
      + 'to={`/app/hardening?promote=${x}`} href={`#${topId}`} '
      + 'href={`${origin}/.well-known/agenttic-cert-keys.json`}', "fixture");
    expect(t.map((x) => x.path)).toEqual(
      ["/pricing", "/status", `/certified/${DYN}`, "/app/hardening"]);
  });
});

describe("react-router agrees, on the real exported table", () => {
  /* Everything above reads the tables as TEXT, which is how it can see inside
     AppShell without mounting a console. This block hands the actual exported
     `routes` to react-router's own matcher, so the top-level half of the claim
     is the router's answer and not this file's regex. */
  const match = (path: string) => {
    const m = matchRoutes(routes as RouteObject[], path);
    // Landing on the public 404 is not a match for these purposes — see
    // `resolves` above. Without this the control below ("nothing at all for a
    // path that is not a route") could never fail, because `*` answers for
    // every string and it is a real entry in the exported table.
    return m && m[m.length - 1].route.path !== "*" ? m : null;
  };
  // `.at(-1)` needs lib es2022; the tsconfig targets ES2020. The LAST match is
  // the leaf the router would render.
  const matchedPath = (path: string) => {
    const m = match(path);
    return m ? m[m.length - 1].route.path : undefined;
  };

  it("matches /engine to the engine route", () => {
    expect(matchedPath("/engine")).toBe("/engine");
  });

  it("sends /app/scenarios into the shell that now routes it", () => {
    // The top-level table only gets it as far as the mount; AppShell's own
    // table (checked above, as text) is what makes the last segment land.
    expect(matchedPath("/app/scenarios")).toBe("/app/*");
  });

  it("still has nothing at all for a path that is not a route", () => {
    // The control. Without it, "matched" above could just mean "matches
    // anything" — and /app/* does match anything under /app.
    expect(match("/not-a-route-at-all")).toBeNull();
    expect(match("/engineering")).toBeNull();
  });

  it("answers the same as this file's resolver on every public link", () => {
    const publicTargets = [...new Set(
      PAGES.flatMap(([f, src]) => targetsIn(src, f))
        .map((t) => t.path)
        .filter((p) => !p.startsWith("/app") && !SERVED_BY_THE_API.has(p)))];
    expect(publicTargets.length).toBeGreaterThan(5);
    for (const p of publicTargets) {
      expect(!!match(p.replace(DYN, "x")), `${p}: resolver and router disagree`)
        .toBe(resolves(p));
    }
  });
});

describe("the API-served link targets", () => {
  it("are still enabled on the server they point at", () => {
    /* /docs and /openapi.json are FastAPI's defaults, real only while nobody
       turns them off. An allowlist that outlives the thing it allows is exactly
       where a dead link hides from a test like this one. */
    expect(SERVER_APP_PY).toContain("FastAPI(");
    expect(SERVER_APP_PY).not.toMatch(/docs_url\s*=\s*None/);
    expect(SERVER_APP_PY).not.toMatch(/openapi_url\s*=\s*None/);
  });
});
