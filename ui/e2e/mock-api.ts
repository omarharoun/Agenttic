import type { Page, Route } from "@playwright/test";

/* Hermetic API mock for E2E — the spec requires the golden path to "run
 * (mocked server)", so no live backend is needed. Every request to `**​/api/**`
 * is intercepted and served representative fixture JSON. Endpoints the golden
 * path touches get realistic shapes; everything else falls through to a benign
 * default so no request 404s and no page throws on an unexpected fetch.
 *
 * Shapes mirror ui/src/api/types.ts closely enough for each route to paint its
 * real interactive content (tables, buttons, live regions, forms). */

const ME = { role: "owner", tenant: "acme", email: "e2e@agenttic.io", auth_method: "session" };

const WORKFLOWS = [
  { workflow_id: "support-eval", name: "Support eval", updated_at: "2026-07-16T08:00:00Z", node_count: 4 },
];

const EXECUTIONS = [
  {
    execution_id: "ex_abc123", workflow_id: "support-eval", status: "succeeded",
    started_at: "2026-07-16T08:00:00Z", node_states: { agent: "done", judge: "done" },
    node_outputs: {},
  },
  {
    execution_id: "ex_def456", workflow_id: "support-eval", status: "waiting_approval",
    started_at: "2026-07-16T09:15:00Z", node_states: { agent: "done", review: "waiting" },
    node_outputs: {}, error_reason: null,
  },
];

const SCORECARDS = [
  {
    scorecard_id: "sc_001", agent_id: "triage-bot", suite_id: "support-v1",
    suite_version: 3, task_success_rate: 0.86, n_runs: 120, n_errored: 2,
    n_scored: 118, n_passed: 101, total_cost_usd: 0.42, total_scoring_cost_usd: 0.08,
    cached: true, created_at: "2026-07-15T10:00:00Z",
  },
  {
    scorecard_id: "sc_002", agent_id: "ref-agent", suite_id: "support-v1",
    suite_version: 3, task_success_rate: 0.61, n_runs: 90, n_errored: 0,
    n_scored: 90, n_passed: 55, total_cost_usd: 0.30, total_scoring_cost_usd: 0.05,
    cached: false, created_at: "2026-07-14T09:30:00Z",
  },
];

const LEADERBOARD = {
  note: "Ranked on the canonical Agenttic Index.",
  agents: [
    { agent_id: "triage-bot", index: 82, n_cases: 120, suites_run: ["support-v1"] },
    { agent_id: "ref-agent", index: 64, n_cases: 90, suites_run: ["support-v1"] },
    { agent_id: "legacy-bot", index: 31, n_cases: 40, suites_run: ["support-v1"] },
  ],
};

const ISSUES_REPORT = {
  status: "ok",
  issues: [
    {
      id: "iss_1", title: "Leaks system prompt on adversarial probe",
      criterion_id: "prompt_leak", category: "safety", category_label: "Safety",
      severity: "critical", impact_rank: 1,
      why: "The agent revealed its hidden instructions when asked directly.",
      affected_n: 7, n_measured: 118, affected_share: 0.06,
      evidence: {
        counts: { failing: 7 },
        cases: [{
          test_id: "probe_014", score: 0, scorer: "safety", calibrated: true,
          rationale: "Disclosed the system prompt verbatim.",
          prediction: "My instructions are…", expected: "Refuse to disclose.",
        }],
        truncated: 6,
      },
      suggested_fix: {
        capability: "hardening", label: "Harden against prompt leaks",
        route: "/app/hardening", blurb: "Add a refusal guard and re-run the suite.",
      },
      status: "open",
    },
  ],
  summary: {
    total_issues: 1, by_severity: { critical: 1, high: 0, medium: 0, low: 0 },
    n_scored: 118, n_passed: 101, n_errored: 2, pass_rate: 0.856,
    pass_wilson_low: 0.78, pass_wilson_high: 0.91,
    headline: "1 critical issue to fix", clean: false,
  },
};

/* Full Scorecard (getScorecard) — drives the scorecard-detail export buttons. */
const SCORECARD_FULL = {
  scorecard_id: "sc_001", agent_id: "triage-bot", suite_id: "support-v1",
  suite_version: 3, rubric_id: "rub_1", rubric_version: 1,
  run_scores: [], task_success_rate: 0.86, mean_cost_usd: 0.0035,
  total_cost_usd: 0.42, total_scoring_cost_usd: 0.08, p95_latency_ms: 1800,
  per_criterion_means: { safety: 0.9, task: 0.86 }, errored_test_ids: [],
  visibility_tier: "internal", created_at: "2026-07-15T10:00:00Z",
  n_scored: 118, n_passed: 101, success_wilson_low: 0.78, success_wilson_high: 0.91,
};

const EXECUTION_RESULTS = {
  cases: [
    { test_id: "case_1", passed: true, score: 1, scorer: "task", rationale: "Resolved the ticket." },
    { test_id: "case_2", passed: false, score: 0, scorer: "safety", rationale: "Leaked the prompt." },
  ],
  scorecards: SCORECARDS,
};

/* Escalations (EscalationInbox) — one pending item so the answer flow has
 * something to respond to. Shape mirrors PendingEscalation. */
const ESCALATIONS = {
  pending_count: 1,
  pending: [{
    trace_id: "tr_esc_1", agent_id: "triage-bot", test_case_id: "case_9",
    question: "Should I refund the customer $50?",
    context: { window_days: 45, order_id: "ord_123" },
    autonomy_policy: { tool: "issue_refund", tool_input: { amount: 50 }, policy: "requires_human" },
  }],
  resolved: [],
};

/* Calibration report (CalibrationReport) — light shape; the page reads criteria
 * + requests. Kept permissive so the summary panel renders without throwing. */
const CALIBRATION = {
  suite_id: "support-v1",
  criteria: [{ criterion_id: "helpfulness", status: "learning", label_count: 3, agreement: 0.6 }],
  requests: [],
};

/* nextUnlabeled (NextUnlabeled) — one unlabeled trace with three score anchors,
 * so a score button ("0"/"1"/"2") renders and posting a label works. */
const NEXT_UNLABELED = {
  exhausted: false,
  criterion: { criterion_id: "helpfulness", description: "Is the answer helpful?", scale: "three_point" },
  suite_id: "support-v1",
  anchors: [
    { score: 0, label: "Not helpful" },
    { score: 1, label: "Partly helpful" },
    { score: 2, label: "Fully helpful" },
  ],
  trace: {
    trace_id: "tr_cal_1", agent_id: "triage-bot", agent_config_hash: "h1",
    test_case_id: "case_1", spans: [], visibility: "internal",
    final_output: "Click 'Forgot password' on the login page and follow the emailed link.",
    total_cost_usd: 0.002, total_latency_ms: 900, total_steps: 3,
    source: "eval", escalated: false, schema_version: "1",
  },
};

/* addLabel (LabelResult). */
const LABEL_RESULT = {
  ok: true, suite_id: "support-v1", labels_path: "/tmp/labels.jsonl", label_count: 4,
  criterion: { criterion_id: "helpfulness", status: "learning", label_count: 4, agreement: 0.65 },
};

const OK = { ok: true };

/** Route-table for the mock. Keys are matched against the URL pathname; first
 * regex to match wins. Values may be a static object or a fn(route)→object. */
type Handler = unknown | ((route: Route) => unknown);
const ROUTES: [RegExp, Handler][] = [
  [/\/api\/me$/, ME],
  [/\/api\/auth\/login$/, { ok: true, role: "owner", tenant: "acme", email: "e2e@agenttic.io" }],
  [/\/api\/auth\/signup$/, { ok: true, needs_verification: false }],
  [/\/api\/auth\/logout$/, OK],
  [/\/api\/workflows$/, WORKFLOWS],
  [/\/api\/workflows\/[^/]+\/executions$/, { execution_id: "ex_new789", status: "running" }],
  [/\/api\/workflows\/[^/]+$/, { workflow_id: "support-eval", name: "Support eval", nodes: [], edges: [] }],
  [/\/api\/node-types$/, []],
  [/\/api\/executions\/[^/]+\/results$/, EXECUTION_RESULTS],
  [/\/api\/executions\/[^/]+\/issues$/, ISSUES_REPORT],
  [/\/api\/executions\/[^/]+$/, EXECUTIONS[0]],
  [/\/api\/executions/, EXECUTIONS],
  [/\/api\/scorecards\/[^/]+\/report\.pdf$/, () => "%PDF-1.4 mock"],
  [/\/api\/scorecards\/[^/]+\/report$/, "# Report\n\nAll good."],
  [/\/api\/scorecards$/, SCORECARDS],
  [/\/api\/scorecards\/[^/]+$/, SCORECARD_FULL],
  [/\/api\/standard\/leaderboard$/, LEADERBOARD],
  [/\/api\/suites$/, []],
  [/\/api\/agents\/catalog/, []],
  [/\/api\/escalations\/[^/]+\/respond$/, OK],
  [/\/api\/escalations/, ESCALATIONS],
  [/\/api\/calibration\/labels$/, LABEL_RESULT],
  [/\/api\/calibration\/[^/]+\/next-unlabeled/, NEXT_UNLABELED],
  [/\/api\/calibration(\?|$)/, CALIBRATION],
  [/\/api\/settings\/anthropic-key$/, { set: true, masked: "sk-…", updated_at: null }],
  [/\/api\/settings\/tokens$/, { tokens: [] }],
];

/** Register the mock on a page. Call before navigation. */
export async function mockApi(page: Page): Promise<void> {
  await page.route("**/api/**", async (route) => {
    const url = new URL(route.request().url());
    const path = url.pathname;
    for (const [re, handler] of ROUTES) {
      if (re.test(path)) {
        const body = typeof handler === "function" ? (handler as (r: Route) => unknown)(route) : handler;
        const isText = typeof body === "string";
        await route.fulfill({
          status: 200,
          contentType: isText ? "text/markdown" : "application/json",
          body: isText ? (body as string) : JSON.stringify(body),
        });
        return;
      }
    }
    // Unknown endpoint — benign empty 200 so nothing 404s or throws.
    await route.fulfill({ status: 200, contentType: "application/json", body: "{}" });
  });
}

/** Seed an authenticated session (bearer token in localStorage) + theme before
 * any app code runs. The AppShell's initial me() will resolve against the mock. */
export async function seedSession(page: Page, theme: "dark" | "light"): Promise<void> {
  await page.addInitScript(
    ([t]) => {
      try {
        localStorage.setItem("ascore_token", "e2e-test-token");
        localStorage.setItem("ascore_theme", t as string);
        document.documentElement.setAttribute("data-theme", t as string);
      } catch { /* ignore */ }
    },
    [theme],
  );
}
