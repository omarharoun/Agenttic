import type { Page } from "@playwright/test";

/* Deterministic console screens for visual regression.
 *
 * A snapshot is only evidence if the same input always produces the same
 * pixels. Pointing these at a live backend would make them a test of whatever
 * happens to be in the database — dates that move, scores that change, an
 * empty state on a fresh machine. So every /api call is answered from a fixed
 * fixture here, and the thing under test is the RENDERING: does this markup,
 * through design/tokens.css, still look the way it looked?
 *
 * That is exactly the M45 question. The milestone changed where values come
 * from, not what they are, and these prove it.
 */

/** Frozen so relative timestamps ("2 hours ago") never move the pixels. */
export const NOW = "2026-07-01T12:00:00Z";

const scorecard = {
  scorecard_id: "sc-demo-1",
  agent_id: "support-triage",
  suite_id: "pilot-support-triage",
  suite_version: 3,
  rubric_id: "triage-rubric",
  rubric_version: 2,
  created_at: NOW,
  composite_score: 88.4,
  grade: "B",
  task_success_rate: 0.86,
  n_cases: 14,
  total_cost_usd: 0.4213,
  mean_latency_ms: 2140,
  errored_test_ids: [],
  coverage: {
    model_ref: "coverage:conversational@v2",
    baseline: false,
    trace_closure: 0.91,
    closure_target: 0.95,
    closed: false,
    assertions: { total: 8, violations: 0, unexercised: 2, verdict: "PASS" },
  },
  criteria: [
    { criterion_id: "refuses_unsafe", mean: 1.0, scorer: "code", calibrated: true },
    { criterion_id: "correct_queue", mean: 0.86, scorer: "judge", calibrated: false },
    { criterion_id: "no_forbidden_tools", mean: 1.0, scorer: "code", calibrated: true },
  ],
};

/* Shapes MUST match the real endpoints. The list endpoints return BARE ARRAYS
 * (see server/routes/resources.py `return rows`), not {items: [...]}. Getting
 * this wrong is not a harmless stub detail: DashboardPage does
 * `(results ?? []).slice(0, 6)`, and `??` only guards null/undefined — handed an
 * object it throws and React replaces the whole page with an error screen. That
 * is exactly what an object-wrapped stub produced here. */
const ROUTES: Record<string, unknown> = {
  "/api/health": { ok: true, version: "2.0.0" },
  "/api/me": { email: "operator@example.com", role: "admin", tenant: "acme" },
  "/api/preview": { demo: { key_set: true } },
  "/api/scorecards": [scorecard],
  "/api/executions": [],
  "/api/workflows": [],
  "/api/catalog": { agents: [] },
  "/api/issues": [],
  "/api/certifications": [],
  "/api/leaderboard": { agents: [] },
  "/api/billing/summary": { plan: "free", spend_usd: 0, quota: {} },
  "/api/settings": { api_keys: [] },
  "/api/capabilities": { dimensions: [], checks: [], models: [] },
};

/** Serve every /api call from the table above; never touch the network. */
export async function stubApi(page: Page) {
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    const body = ROUTES[path]
      ?? Object.entries(ROUTES).find(([k]) => path.startsWith(k))?.[1]
      ?? [];
    await route.fulfill({
      status: 200, contentType: "application/json", body: JSON.stringify(body),
    });
  });
}

/** Choose the theme the way a user does — BEFORE the app boots.
 *
 *  Stamping `data-theme` after load does not work and fails silently: the
 *  no-flash script in index.html reads localStorage before first paint, and
 *  the app re-applies its persisted preference on mount, so the attribute is
 *  overwritten and both "themes" snapshot identically. That is how the first
 *  run produced a byte-identical settings-dark and settings-light.
 *
 *  Not named use* — that prefix makes every React tool treat a plain helper
 *  as a hook, which the lint correctly objected to.
 *
 *  Call before `page.goto`. */
export async function chooseTheme(page: Page, theme: "dark" | "light") {
  await page.addInitScript((t) => {
    localStorage.setItem("agenttic_theme", t);
  }, theme);
}

/** Stop the clock.
 *
 *  Screens render relative times ("3 days ago") against the wall clock, so the
 *  same fixture produces different text as real time advances — a snapshot that
 *  passes today and fails next week, or intermittently within one run. Pinning
 *  the page clock to the same instant the fixtures use makes those strings
 *  constant. Call before `page.goto`. */
export async function freezeClock(page: Page) {
  await page.clock.setFixedTime(new Date(NOW));
}

/** Wait until the screen has its data, not merely its shell.
 *
 *  `waitFor` on a container resolves as soon as the shell mounts, which is
 *  BEFORE the API promises settle — so a screenshot can catch the loading state
 *  on one run and the loaded state on the next. That is not a flaky test, it is
 *  a test racing the app. Waiting for the network to go quiet makes which
 *  render gets captured deterministic. */
export async function loaded(page: Page) {
  await page.locator("main, .page, #root > *").first().waitFor();
  await page.waitForLoadState("networkidle");
}

/** Hold the page still: no in-flight animation, no blinking caret. */
export async function settle(page: Page, theme: "dark" | "light") {
  await page.addStyleTag({
    content: `*, *::before, *::after {
      animation-duration: 0s !important; animation-delay: 0s !important;
      transition-duration: 0s !important; transition-delay: 0s !important;
      caret-color: transparent !important;
    }`,
  });
  // Assert rather than set: chooseTheme() already chose it, and a mismatch here
  // means the preference did not survive boot — which must fail loudly, not
  // quietly produce two identical snapshots.
  const applied = await page.getAttribute("html", "data-theme");
  if (applied !== theme) {
    throw new Error(
      `expected data-theme="${theme}" but the app booted with "${applied}" — ` +
      `call chooseTheme() before page.goto()`);
  }
  await page.evaluate(() => document.fonts.ready);
  await page.evaluate(() => new Promise((r) => requestAnimationFrame(() => r(null))));
}
