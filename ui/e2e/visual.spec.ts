import { expect, test, type Page } from "@playwright/test";
import { freezeClock, loaded, settle, stubApi, chooseTheme } from "./support/console";

/* M45 acceptance: "Visual regression snapshots of existing console screens are
 * unchanged or their diffs are explained in RECONCILIATION.md."
 *
 * The baselines committed alongside this file ARE the record of what the token
 * migration produced. From here, any change to design/tokens.css that moves a
 * pixel on these screens has to be looked at and either accepted (update the
 * baseline, explain it in RECONCILIATION.md) or fixed. That is the whole
 * mechanism: the token layer can no longer be edited invisibly.
 *
 * Both themes, because a token that resolves in one and not the other is
 * exactly the failure this milestone exists to prevent.
 */

/** A screen under the gate.
 *
 *  `ready` is an extra, screen-SPECIFIC proof that the page arrived in the state
 *  its baseline is meant to pin. It runs after `loaded()` and before the
 *  capture, and it exists for the reason `loaded()` refuses to photograph an
 *  error boundary: a stable image of the wrong state passes forever while the
 *  thing it was supposed to watch rots. `loaded()` can only make the generic
 *  checks — the shell mounted, the network went quiet, React did not crash. It
 *  cannot know that a run table is supposed to have rows in it. */
type Screen = {
  name: string;
  path: string;
  ready?: (page: Page) => Promise<void>;
};

/** /app/scenarios is only worth photographing WITH runs in the table.
 *
 *  The screen's entire subject is that table, and `ScenariosPage` renders
 *  `ScenarioEmpty` — a short empty state with no run cells on it at all — when
 *  the list comes back empty. That is also what an absent or reshaped stub
 *  produces, because `api.listScenarioRuns()` reads `r.runs ?? []`: hand it a
 *  payload with no `runs` key and it yields zero rows without erroring. A
 *  baseline captured then would diff clean forever while every cell that draws a
 *  stored run — the fault counts, the world/blocked columns, the four separate
 *  fault states this screen exists to keep apart — went unwatched.
 *
 *  So this fails loudly instead. The rows come from `scenario-runs.fixture.json`
 *  through `stubApi()` in e2e/support/console.ts. */
async function scenariosReady(page: Page) {
  await expect(
    page.locator("table.scn-runs tbody tr"),
    "the scenario-run table has no rows — /api/scenario-runs did not answer with "
    + "runs, and a baseline of ScenarioEmpty pins a screen with no run on it",
  ).not.toHaveCount(0);
}

/** /engine has to be caught with its live sections READ and its stored-run
 *  section in the signed-out state — see the block at the end of the loop for
 *  why that is the state, and `loaded()` in support/console.ts for what it
 *  cannot check on a route like this one.
 *
 *  Four assertions, because four things can go wrong here and only one of them
 *  would look like a failure:
 *
 *    1. the h1. The route is lazy and NOT prerendered, so `#root > *` in
 *       `loaded()` is satisfied by `RouteFallback` — an empty `.route-loading`
 *       div — the instant the router mounts. Waiting on the hero heading is what
 *       proves the EnginePage chunk executed, instead of the SPA shell being
 *       photographed blank.
 *    2. no `.eng-pending` left. That class is rendered only while a read is in
 *       flight ("Reading the coverage model from this deployment…"), so zero of
 *       them means both reads have settled one way or the other.
 *    3. `.eng-dims`, which renders ONLY from a capabilities payload this page
 *       could read. Where the read fails the same sections carry `.eng-absent`
 *       prose, which is just as pixel-stable — that baseline would be a
 *       photograph of the page's could-not-read path, green forever while the
 *       sections it is meant to watch went undrawn.
 *    4. the signed-out copy, which is the state this baseline claims to be. */
async function engineReady(page: Page) {
  await expect(page.getByRole("heading", { level: 1 }))
    .toHaveText("A pass rate cannot tell you what you never tried.");
  await expect(
    page.locator(".eng-pending"),
    "a section is still reading — the capture would catch a loading state",
  ).toHaveCount(0);
  await expect(
    page.locator(".eng-dims .eng-dim").first(),
    "no coverage dimension rendered — /api/capabilities was not read, so this "
    + "would be a baseline of the page's could-not-read state",
  ).toBeVisible();
  await expect(
    page.locator("#runs .eng-absent"),
    "the stored-run section is not in the signed-out state",
  ).toContainText("You are not signed in");
}

/** Answer the PROTECTED read the way a deployment answers a visitor.
 *
 *  `/api/scenario-runs` is mounted with `dependencies=protected`
 *  (server/app.py), so a signed-out browser gets exactly this: a 401 carrying a
 *  `detail` string. Registered AFTER `stubApi()` on purpose — Playwright checks
 *  matching handlers in the reverse of their registration order, so the last one
 *  registered wins. That ordering is not left to trust: `engineReady()` asserts
 *  the signed-out copy is on the page, so if it ever changed this fails instead
 *  of quietly capturing the console's stored runs on a public page. */
async function signedOut(page: Page) {
  await page.route(
    (url) => url.pathname === "/api/scenario-runs",
    (route) => route.fulfill({
      status: 401,
      contentType: "application/json",
      body: JSON.stringify({
        detail: "authentication required (API token or login session)",
      }),
    }));
}

/** Console screens that render meaningfully from stubbed data. */
const CONSOLE: Screen[] = [
  { name: "dashboard", path: "/app" },
  { name: "results", path: "/app/results" },
  { name: "build", path: "/app/build" },
  { name: "capabilities", path: "/app/capabilities" },
  { name: "settings", path: "/app/settings" },
  /* The scenario-run list as it looks on arrival: the filter form, the table of
     stored runs, nothing selected. The per-run detail view is deliberately NOT
     in this shot — it mounts only after `inspect`, and pinning both here would
     make one baseline answerable for two screens' worth of markup, so a diff
     would no longer say which of them moved. */
  { name: "scenarios", path: "/app/scenarios", ready: scenariosReady },
];

/** Public routes — prerendered, no stubbing needed.
 *
 *  Both halves of that sentence are load-bearing, and /engine has neither. It is
 *  therefore not in this list; see the block below. */
const PUBLIC: Screen[] = [
  { name: "landing", path: "/" },
  { name: "pricing", path: "/pricing" },
  { name: "methodology", path: "/methodology" },
];

for (const theme of ["dark", "light"] as const) {
  for (const screen of CONSOLE) {
    test(`console ${screen.name} — ${theme}`, async ({ page }) => {
      await chooseTheme(page, theme);
      await freezeClock(page);
      await stubApi(page);
      await page.goto(screen.path);
      await loaded(page);
      await screen.ready?.(page);
      await settle(page, theme);
      await expect(page).toHaveScreenshot(`${screen.name}-${theme}.png`, {
        fullPage: true,
      });
    });
  }

  for (const screen of PUBLIC) {
    test(`public ${screen.name} — ${theme}`, async ({ page }) => {
      await chooseTheme(page, theme);
      await freezeClock(page);
      await page.goto(screen.path);
      await loaded(page);
      await screen.ready?.(page);
      await settle(page, theme);
      await expect(page).toHaveScreenshot(`${screen.name}-${theme}.png`, {
        fullPage: true,
      });
    });
  }

  /* ---- /engine — public, but neither prerendered nor static ---------------
   *
   * Written out rather than added to PUBLIC because it breaks both of that
   * list's assumptions, and folding it in would have produced a worthless
   * baseline rather than a failure anyone would notice.
   *
   * NOT PRERENDERED. App.tsx routes it lazily and vite.config.ts's PRERENDER set
   * deliberately omits it — it imports `formatCreated` from the CONSOLE's
   * ScenariosPage, and an eager import would pull a console page into the
   * landing's initial chunk. Under `vite preview` the URL therefore falls
   * through to the SPA shell and the page is built client-side, which is why
   * `engineReady()` waits for the h1 rather than trusting `loaded()`.
   *
   * NOT STATIC. The page reads two endpoints on mount and prints what it got.
   * Unstubbed against the static preview server BOTH reads come back as the HTML
   * shell — there is no backend behind `vite preview`, and the `server.proxy` in
   * vite.config.ts applies to the dev server only — so api.ts raises "Expected
   * JSON but the server returned text/html" and every live section renders its
   * could-not-read state. That image is perfectly stable, and it asserts nothing
   * except that a broken deployment stays broken the same way.
   *
   * WHAT THIS PINS is the SIGNED-OUT VISITOR, each endpoint answered the way a
   * real deployment answers one:
   *
   *   /api/capabilities   public — app.py mounts it with no auth dependency —
   *                       so a visitor genuinely receives this payload. It is
   *                       the same captured fixture the /app capabilities
   *                       baseline uses, from the same real endpoint, and the
   *                       coverage dimensions, the fault bins and the whole
   *                       not_covered list on this page are drawn from it.
   *   /api/scenario-runs  protected, so a visitor genuinely receives a 401. The
   *                       page then says it cannot show a stored run, and
   *                       refuses to draw a specimen one.
   *
   * That is a real state of the real page rather than a contrivance: it is what
   * everyone who is not logged in sees. Feeding it the console's stored runs
   * would instead pin a public page rendering evidence no visitor can get, on
   * the one page whose whole argument is that an illustration must never stand
   * where evidence belongs.
   *
   * The signed-in variant — the `.eng-runs` list — is covered by no baseline.
   * Recorded in RECONCILIATION.md rather than papered over. */
  test(`public engine — ${theme}`, async ({ page }) => {
    await chooseTheme(page, theme);
    await freezeClock(page);
    await stubApi(page);
    await signedOut(page);
    await page.goto("/engine");
    await loaded(page);
    await engineReady(page);
    await settle(page, theme);
    await expect(page).toHaveScreenshot(`engine-${theme}.png`, {
      fullPage: true,
    });
  });
}

test("a token change moves the pixels on both surfaces", async ({ page }) => {
  /* The mechanism check. If overriding a token changed nothing, these
   * snapshots would be decorative — passing whether or not the surfaces
   * actually draw from the token layer. Console and landing are asserted
   * together because the point of M45 is that they share one source. */
  const shot = async (path: string, override?: string) => {
    await chooseTheme(page, "dark");
    await freezeClock(page);
    await stubApi(page);
    await page.goto(path);
    await loaded(page);
    await settle(page, "dark");
    if (override) {
      await page.addStyleTag({ content: `:root { ${override} }` });
      await page.evaluate(() => new Promise((r) => requestAnimationFrame(() => r(null))));
    }
    return page.screenshot({ fullPage: false });
  };

  const paintItPink = "--bg: #ff00aa !important; --panel: #ff00aa !important;";

  const consoleBefore = await shot("/app");
  const consoleAfter = await shot("/app", paintItPink);
  expect(Buffer.compare(consoleBefore, consoleAfter),
    "console did not respond to a token override").not.toBe(0);

  const landingBefore = await shot("/");
  const landingAfter = await shot("/", paintItPink);
  expect(Buffer.compare(landingBefore, landingAfter),
    "landing did not respond to a token override").not.toBe(0);
});
