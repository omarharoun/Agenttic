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

/* Stored scenario runs — the evidence /app/scenarios and /engine read back.
 *
 * PROVENANCE, the same rule `capabilities.fixture.json` is under below: this is
 * the shipped code's own output, not a description of it. Six REAL runs, each
 * one `agenttic scenario run` driven in-process against a throwaway registry —
 * `realize()` -> `scenario_runner()` / `multi_turn_scenario_runner()` ->
 * `Registry.save_scenario_run()`, carrying cli.py's own exhibited-bins and
 * `divergence` computation — and then dumped through the SAME three reads the
 * routes make: `list_scenario_runs()`, `get_scenario_run(id)`, and
 * `get_trace(run.trace_id)`. Offline throughout: no API key, the DUT is the
 * deterministic scripted stand-in and NOT a model, and the capture stubs
 * `socket` out before the first run so a run that reached the network would
 * fail rather than pass quietly.
 *
 * THE SET IS THE POINT. This screen's whole design is keeping absences apart —
 * a fault that fired, one that reached its call and could not happen, one staged
 * on a call the agent never made, a divergence list empty because nothing
 * diverged rather than because nobody looked — and a fixture holding one happy
 * run photographs none of it. Every fate below was
 * OBSERVED and printed by the capture, not chosen for it (the seeds are the
 * NEVER_REACHED / SKIPPED / FIRED constants in tests/test_cli_scenario.py):
 *
 *   seed 3  refund / timeout             fault FIRED
 *   seed 3  account_change --multi-turn  5 transcript turns, `order_id` ELICITED
 *                                        (the counterparty withheld it until it
 *                                        was asked for), and a fault NEVER
 *                                        REACHED in the same run
 *   seed 6  refund / malformed_response  fault SKIPPED — it reached its call and
 *                                        could not fire, with the reason stored
 *   seed 7  out_of_scope / timeout       fault NEVER REACHED — staged on a
 *                                        lookup the agent never made
 *   seed 2  exchange / all_ok            nothing staged; `divergence: []` — the
 *                                        run WAS asked and nothing diverged,
 *                                        which is not "nobody asked" (`null`)
 *   seed 4  status / all_ok              nothing staged, world unchanged
 *
 * Four of the six changed the world (non-empty `state_diff`); two did not, so
 * "unchanged" is on screen as a rendered fact rather than as an untested branch.
 *
 * WHAT IS NOT HERE, and was not invented to fill the hole: every run carries
 * `n_blocked: 0`. A sweep of 48 offline runs (24 seeds x single-shot and
 * multi-turn) produced no gateway refusal at all — the scripted stand-in only
 * ever calls tools the world declares — so "the calls the gateway refused" is
 * photographed in its empty state. Of the four enforcement verdicts the call
 * table draws, the captured spans carry `executed` (13 calls) and `faulted` (1,
 * where the staged fault replaced what the tool would have returned); `blocked`
 * and `unrecorded` are rendered by nothing here. Writing a refusal by hand would
 * make the one panel on this screen that nothing verified look like the ones
 * that were.
 *
 * Two more of the page's states are likewise unexercised, and for a reason worth
 * knowing: every run here has `faults.recorded: true` and `coverage.measured:
 * true`, because `agenttic scenario run` always writes a fault report and always
 * collects bins. "Nobody wrote it down" and "nobody measured" are rows a
 * DIFFERENT writer produces, and no real run this command can drive will emit
 * one — so they stay unphotographed rather than forged.
 *
 * RECAPTURE — the same rule `capabilities.fixture.json` is under: recapture in
 * the change that alters what a run stores, and record the movement in
 * ui/src/design/RECONCILIATION.md. Six `agenttic scenario run` invocations
 * against a throwaway registry, in the order they were STORED (the list is
 * newest-first, so this is the table read bottom-up):
 *
 *   --agent support-triage --seed 4 --intent status       --tool-condition all_ok
 *   --agent support-triage --seed 2 --intent exchange     --tool-condition all_ok
 *   --agent support-triage --seed 7 --intent out_of_scope --tool-condition timeout
 *   --agent support-triage --seed 6 --intent refund --tool-condition malformed_response
 *   --agent support-triage --seed 3 --intent account_change --multi-turn
 *   --agent support-triage --seed 3 --intent refund       --tool-condition timeout
 *
 * then `{list: {runs, count}, details: {<run_id>: ...}, traces: {<trace_id>: ...}}`
 * written with `indent=2` and no trailing newline — `list` is
 * `Registry.list_scenario_runs()` under the route's own `{"runs": ..., "count":
 * len(runs)}` envelope, `details` is `get_scenario_run(run_id)` per row, and
 * `traces` is `get_trace(row.trace_id).model_dump(mode="json")` per row.
 *
 * Two clocks were pinned during the capture so a RECAPTURE diffs on what a run
 * STORES rather than on when it ran: the registry's `_now()` (created_at lands
 * the evening before the frozen NOW below) and `uuid.uuid4()` (run, trace and
 * span ids). Neither changes what a run does. Recapturing then reproduces this
 * file byte for byte with ONE exception, checked rather than assumed: each
 * trace's `total_latency_ms` is a measured duration and moves with the machine.
 * It reaches no pixel — the page takes `.spans` off the trace and nothing else —
 * so it is left as captured rather than rounded into a number no run emitted.
 *
 * Nothing on the screen is read against the wall clock either: `formatCreated()`
 * spaces out the stored ISO string and computes no relative time. */
const SCENARIO_RUNS = JSON.parse(readFileSync(
  new URL("./scenario-runs.fixture.json", import.meta.url), "utf8"));

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

/* --- routes whose body depends on the request ----------------------------- *
 *
 * `ROUTES` above is a table of STATIC bodies, matched by exact path and then by
 * prefix. The prefix fallback is what lets one key answer a whole family, and it
 * is a trap the moment a route has a detail form: `/api/scenario-runs/<id>`
 * startsWith the list key `/api/scenario-runs`, so the DETAIL request would be
 * answered with `{runs, count}` — which `getScenarioRun` hands to the page as a
 * run with no run_id, no transcript, no fault report and no coverage. It does
 * not throw. It renders a screenful of absences that are all artefacts of the
 * stub, and the gate photographs them as the feature.
 *
 * So these three are resolved HERE, ahead of both lookups and anchored at both
 * ends. They are not `ROUTES` keys because their bodies depend on the id and the
 * query; a `ROUTES` entry naming the same path would be a second answer to one
 * question, which is the failure the `no-dupe-keys` note in eslint.config.js was
 * written for.
 */

/** Just enough of a row to filter on. */
type ScenarioRunLike = { scenario_id: string; agent_id: string };

/** `GET /api/scenario-runs`, with the real endpoint's contract applied to the
 *  captured rows so the stub answers the request that was actually made:
 *
 *  * exact match on a NON-EMPTY `scenario_id`/`agent_id`. `?agent_id=` and an
 *    absent `agent_id` are the same request — routes/scenarios.py `_filter()`,
 *    where an empty string arriving as a real filter answered "no runs" for a
 *    query that named no agent;
 *  * `limit` clamped to 1..500, defaulting to 50, as the route clamps it;
 *  * `count` is `len(runs)` — the size of the PAGE, never of the store.
 *
 *  /engine asks for 5 and /app/scenarios for 100. A stub that returned all six
 *  rows to both would put a row on the engine page that the deployment it is
 *  meant to be reading would not have sent. */
function scenarioRunList(q: URLSearchParams): unknown {
  const scenarioId = q.get("scenario_id") ?? "";
  const agentId = q.get("agent_id") ?? "";
  const asked = Number(q.get("limit") ?? 50);
  const limit = Math.min(Math.max(1, Number.isFinite(asked) ? asked : 50), 500);
  const runs = (SCENARIO_RUNS.list.runs as ScenarioRunLike[])
    .filter((r) => (!scenarioId || r.scenario_id === scenarioId)
                && (!agentId || r.agent_id === agentId))
    .slice(0, limit);
  return { runs, count: runs.length };
}

/** One stubbed reply. The STATUS is part of the fixture: an id the capture does
 *  not carry gets the 404 its route gives it, because a stale 200 would show one
 *  run's evidence under a different run's id — and the page has a rendered state
 *  for a detail it could not load, which a 200 would keep it out of. */
type Reply = { status: number; body: unknown };

/** Own-property lookup, never the prototype chain.
 *
 *  A bare `table[id]` answers `__proto__`, `constructor` and `toString` with an
 *  inherited value — for `constructor` that is a FUNCTION, which
 *  `JSON.stringify` renders as `undefined`, so the stub would reply 200 with a
 *  zero-length body and api.ts would raise a parse error instead of the page
 *  showing its not-found state. A stub that fails differently from the real
 *  endpoint photographs a state the product cannot produce.
 *
 *  This is the same guard `EnginePage.evidenceFor()` already uses, for the same
 *  reason; a fixture resolver is not exempt from it. */
function own<T>(table: Record<string, T>, id: string): T | undefined {
  return Object.prototype.hasOwnProperty.call(table, id) ? table[id] : undefined;
}

/** Which fixture answers this request. The order is load-bearing; see above. */
function reply(url: URL): Reply {
  const path = url.pathname;

  if (path === "/api/scenario-runs") {
    return { status: 200, body: scenarioRunList(url.searchParams) };
  }
  const run = /^\/api\/scenario-runs\/([^/]+)$/.exec(path);
  if (run) {
    const id = decodeURIComponent(run[1]);
    const detail = own(SCENARIO_RUNS.details, id);
    return detail
      ? { status: 200, body: detail }
      : { status: 404, body: { detail: `scenario run ${id} not found` } };
  }
  /* The page reads the tool calls and the gateway's verdict on each off the
   * TRACE, not off the run record. Left unstubbed it falls through to `[]`,
   * `t?.spans ?? []` reads no spans, and the screen reports that the trace could
   * not be read — a finding about a request rather than a picture of the
   * feature. */
  const trace = /^\/api\/traces\/([^/]+)$/.exec(path);
  if (trace) {
    const id = decodeURIComponent(trace[1]);
    const stored = own(SCENARIO_RUNS.traces, id);
    return stored
      ? { status: 200, body: stored }
      : { status: 404, body: { detail: `trace ${id} not found` } };
  }

  const exact = ROUTES[path];
  if (exact !== undefined) return { status: 200, body: exact };
  const prefixed = Object.entries(ROUTES).find(([k]) => path.startsWith(k));
  return { status: 200, body: prefixed ? prefixed[1] : [] };
}

/** Serve every /api call from the fixtures above; never touch the network. */
export async function stubApi(page: Page) {
  await page.route("**/api/**", async (route) => {
    const { status, body } = reply(new URL(route.request().url()));
    await route.fulfill({
      status, contentType: "application/json", body: JSON.stringify(body),
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
