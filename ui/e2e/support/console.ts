import type { Page } from "@playwright/test";
import { readFileSync } from "node:fs";

/* Read rather than `import ... from "*.json"`: Node ESM requires an import
 * attribute (`with { type: "json" }`) whose support differs between the
 * TypeScript transpile and the runtime, and the failure is a hard module error
 * that takes the whole suite down. A file read has neither problem. */
const CAPABILITIES = JSON.parse(readFileSync(
  new URL("./capabilities.fixture.json", import.meta.url), "utf8"));

/* The `coverage` blob a scored run carries — the product's headline disclosure,
 * and until now the one thing this fixture did not have at all.
 *
 * The stub used to declare a headline (`trace_closure`) and nothing under it, so
 * `NeverExercised` — the "what this run never exercised" table — rendered
 * EMPTY on every snapshot, in both themes. A visual gate that photographs the
 * table blank cannot notice what the table says, which is exactly how the cell
 * rendering `Math.round((v.closure ?? 0) * 100)` shipped: `0%` for a coverpoint
 * whose closure is `null` because nothing measures it. The gate was green the
 * whole time because the row was never on the page.
 *
 * All THREE render states are present, because a fixture that shows only the
 * happy path is how the gap happened in the first place:
 *
 *   measured, with gaps   trajectory 89% (`budget_exceeded` unhit), and five more
 *   measured, at zero     action_risk 0% — this MUST still print "0%". The
 *                         correction was never "stop printing 0%"; a suite that
 *                         touched tools and could not place a single one is a
 *                         real finding, and softening it trades one lie for
 *                         another.
 *   NOT MEASURABLE        session_shape — closure null, `unhit` empty, the flag
 *                         and the reason set. Must read "not measurable", never
 *                         "0%": zero is a measurement, and it reads as a gap
 *                         someone can be told to close. No suite can close this.
 *
 * PROVENANCE. Produced by the shipped collector, not transcribed — the rule the
 * capabilities fixture below is under, for the same reason. `collect()` from
 * `coverage/models/conversational_transactional.seed_model()` was run over a
 * 14-case support-triage suite whose deterministic bins are earned from real
 * spans: declared `http.response.status_code` 503/429, an `error.type` timeout,
 * a tool result reading "account not found", a real `escalate_to_agent` call, a
 * `max_steps` attribute, and — for `action_risk` — an agent whose every tool is
 * an opaque `mcp__acme__run`, which post-hardening earns no risk bin at all.
 * The three semantic dimensions are classifier-backed; each case's label was
 * DECLARED and read back by a stand-in, so the numbers are the collector's
 * arithmetic over a stated suite rather than a model's output.
 *
 * Nothing here is hand-tuned, and `ui/src/e2e-coverage-fixture.test.tsx` keeps
 * it that way: it recomputes the headline from the coverpoints and crosses the
 * way `CoverageReport.trace_closure` does, so editing one number without the
 * others fails rather than quietly producing a payload no run can emit. */
const COVERAGE = JSON.parse(readFileSync(
  new URL("./coverage.fixture.json", import.meta.url), "utf8"));

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
  /* Captured whole — see COVERAGE above. It carries its own `model_ref`
   * (`coverage:cov-conversational_transactional@v3`, i.e. CoverageModel.ref()),
   * its own headline and target, and its own assertion roll-up, all from the
   * same 14 traces. They used to be four hand-written literals sitting next to
   * each other with nothing making them agree: the headline said 91% closure
   * while no coverpoint underneath it said anything at all, and the assertion
   * roll-up was a plausible-looking quartet rather than a run's. */
  coverage: COVERAGE,
  criteria: [
    { criterion_id: "refuses_unsafe", mean: 1.0, scorer: "code", calibrated: true },
    { criterion_id: "correct_queue", mean: 0.86, scorer: "judge", calibrated: false },
    { criterion_id: "no_forbidden_tools", mean: 1.0, scorer: "code", calibrated: true },
  ],
};

/* `capabilities.fixture.json` is the REAL response, captured by calling
 * server/routes/capabilities.py directly (it enumerates registries and touches
 * no database, so it is reproducible). Recapture — `indent=2`, no trailing
 * newline, which is byte-exact against the committed file, so the diff is the
 * endpoint's change and nothing else:
 *
 *   uv run python -c "import json, pathlib; \
 *     from agenttic.server.routes.capabilities import capabilities; \
 *     pathlib.Path('ui/e2e/support/capabilities.fixture.json') \
 *       .write_text(json.dumps(capabilities(), indent=2))"
 *
 * A stale capture is worse than no capture: these two screenshots then
 * photograph a page the product can no longer serve, and every field the
 * endpoint added is rendered by no test at all. So the rule is that this file is
 * recaptured in the same change that edits the endpoint, and the resulting
 * movement in capabilities-{dark,light}.png is recorded in
 * ui/src/design/RECONCILIATION.md with the reason.
 *
 * Hand-writing this one failed twice — first `coverage.baseline` was missing,
 * then `supply_chain.mcp_server.checks`. A deeply nested shape guessed from the
 * page's field accesses will keep being wrong in a new place each time, and each
 * wrong guess renders a crash the snapshot would happily photograph. Capture
 * beats transcription.
 *
 * Shapes MUST match the real endpoints. The list endpoints return BARE ARRAYS
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
  "/api/capabilities": CAPABILITIES,
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

  /* Refuse to photograph a crash.
   *
   * A screenshot of React Router's error boundary is a perfectly stable image:
   * it captures, it diffs clean forever, and it asserts nothing except that the
   * page keeps breaking the same way. That is not hypothetical — the first
   * capabilities baseline committed here WAS an error screen, because the stub
   * had the wrong shape and nobody looked at all sixteen images.
   *
   * The stack trace embeds hashed asset filenames, so such a baseline also
   * breaks on every rebuild, which is how it finally surfaced. Failing loudly
   * here is the difference between a suite that proves something and one that
   * merely runs. */
  const crashed = await page.locator("text=Unexpected Application Error").count();
  if (crashed > 0) {
    const detail = await page.locator("pre, h2, h3").first().innerText()
      .catch(() => "(no detail)");
    throw new Error(
      `the page rendered an error boundary, not the screen under test — `
      + `refusing to snapshot it.\n${detail.slice(0, 400)}`);
  }
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
