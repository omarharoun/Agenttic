// @vitest-environment jsdom
/* SPEC-4 Step 17.4 — Accessibility baseline, axe smoke gate.

   THE ACCEPTANCE GATE. Each of the five core console routes is rendered into a
   real DOM with a hoisted, representative API mock, then scanned with axe-core.
   The assertion fails on any violation of `critical` OR `serious` impact — the
   two tiers that block a user. If axe finds a real violation the fix belongs in
   the component, not in a weakened assertion.

   Routes covered:
     1. Dashboard          — DashboardPage (leaderboard + recent results tables)
     2. Runs               — ExecutionsPage (runs table + row actions)
     3. Results            — ResultsHistoryPage (results table + report/PDF/certify)
     4. Scorecard detail   — IssuesReport (the ranked "what's wrong" report, the
                             hero of a scored result; rendered via its injected
                             `report` prop so it stands alone with real data)
     5. Issues             — IssuesPage (run picker + IssuesReport)

   Notes:
   - ExecutionsPage is rendered in its list state (no run inspected) so the axe
     scan targets the interactive table + row actions; the React-Flow replay
     canvas (which needs a real layout engine) is intentionally out of frame.
   - The mock returns non-empty data so every route paints its real interactive
     content (tables, buttons, selects, live regions) rather than an empty state. */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter } from "react-router-dom";
import React, { act } from "react";
import { configureAxe } from "jest-axe";
import type {
  Execution, IssuesReport as IssuesReportT, ScorecardSummary,
  StandardLeaderboard,
} from "./api";

// React 18 act() needs this flag in a test environment.
(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean })
  .IS_REACT_ACT_ENVIRONMENT = true;

// jsdom lacks these browser APIs that the components (and React Flow) touch.
beforeEach(() => {
  if (!window.matchMedia) {
    window.matchMedia = ((q: string) => ({
      matches: false, media: q, onchange: null,
      addEventListener() {}, removeEventListener() {},
      addListener() {}, removeListener() {}, dispatchEvent() { return false; },
    })) as unknown as typeof window.matchMedia;
  }
  if (!(window as unknown as { ResizeObserver?: unknown }).ResizeObserver) {
    (window as unknown as { ResizeObserver: unknown }).ResizeObserver = class {
      observe() {} unobserve() {} disconnect() {}
    };
  }
});

// axe-core, configured to FAIL on critical + serious impact only. We keep the
// whole default rule catalogue on; the impact filter is applied in-assertion so
// the failure message lists exactly the blocking violations.
const axe = configureAxe({});
const BLOCKING = new Set(["critical", "serious"]);

// ---------------------------------------------------------------------------
// Representative fixtures — enough shape for each route to paint real content.
// ---------------------------------------------------------------------------
const LEADERBOARD: StandardLeaderboard = {
  note: "Ranked on the canonical Agenttic Index.",
  agents: [
    { agent_id: "triage-bot", index: 82, n_cases: 120, suites_run: ["support-v1"] },
    { agent_id: "ref-agent", index: 64, n_cases: 90, suites_run: ["support-v1"] },
    { agent_id: "legacy-bot", index: 31, n_cases: 40, suites_run: ["support-v1"] },
  ],
};

const SCORECARDS: ScorecardSummary[] = [
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

const EXECUTIONS: Execution[] = [
  {
    execution_id: "ex_abc123", workflow_id: "support-eval", status: "succeeded",
    started_at: "2026-07-16T08:00:00Z", node_states: { agent: "done", judge: "done" },
    node_outputs: {},
  },
  {
    execution_id: "ex_def456", workflow_id: "support-eval",
    status: "waiting_approval", started_at: "2026-07-16T09:15:00Z",
    node_states: { agent: "done", review: "waiting" }, node_outputs: {},
    error_reason: null,
  },
];

const ISSUES_REPORT: IssuesReportT = {
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
        cases: [
          {
            test_id: "probe_014", score: 0, scorer: "safety",
            calibrated: true, rationale: "Disclosed the system prompt verbatim.",
            prediction: "My instructions are…", expected: "Refuse to disclose.",
          },
        ],
        truncated: 6,
      },
      suggested_fix: {
        capability: "hardening", label: "Harden against prompt leaks",
        route: "/app/hardening", blurb: "Add a refusal guard and re-run the suite.",
      },
      status: "open",
    },
    {
      id: "iss_2", title: "Slow on multi-step tasks",
      criterion_id: null, category: "performance", category_label: "Performance",
      severity: "medium", impact_rank: 2,
      why: "p95 latency exceeds the target on chained tool calls.",
      affected_n: 20, n_measured: 118, affected_share: 0.17,
      evidence: { counts: { failing: 20 }, cases: [], truncated: 0 },
      suggested_fix: {
        capability: "optimize", label: "Optimize the prompt",
        route: "/app/optimize", blurb: "Tighten instructions to cut round-trips.",
      },
      status: "open",
    },
  ],
  summary: {
    total_issues: 2,
    by_severity: { critical: 1, high: 0, medium: 1, low: 0 },
    n_scored: 118, n_passed: 101, n_errored: 2, pass_rate: 0.856,
    pass_wilson_low: 0.78, pass_wilson_high: 0.91,
    headline: "1 critical issue and 1 more to fix", clean: false,
  },
};

// ---------------------------------------------------------------------------
// Hoisted API mock — mirrors the 17.3 pagedata smoke test's approach so pages
// destructuring `{ api }` resolve their fetches to representative data. Real
// helpers (errMessage/downloadBlob/…) pass through so error paths stay honest.
// ---------------------------------------------------------------------------
const { apiImpl } = vi.hoisted(() => ({
  apiImpl: {} as Record<string, (...a: unknown[]) => unknown>,
}));

vi.mock("./api", async (importOriginal) => {
  const real = await importOriginal<typeof import("./api")>();
  return {
    ...real,
    api: new Proxy(apiImpl, {
      get(t, p: string) {
        if (!(p in t)) t[p] = async () => undefined;
        return t[p];
      },
    }),
    auth: { get: () => null, set: () => {}, clear: () => {} },
  };
});

// Wire each method the five routes call to a representative resolution.
function installApi() {
  Object.assign(apiImpl, {
    standardLeaderboard: async () => LEADERBOARD,
    listScorecards: async () => SCORECARDS,
    listExecutions: async () => EXECUTIONS,
    getExecution: async () => EXECUTIONS[0],
    executionResults: async () => ({ cases: [], scorecards: [] }),
    executionIssues: async () => ISSUES_REPORT,
    scorecardReport: async () => "# Report\n\nAll good.",
    scorecardPdf: async () => new Blob(["pdf"]),
    approve: async () => ({ ok: true }),
    nodeTypes: async () => [],
    anthropicKeyStatus: async () => ({ set: true, masked: "sk-…", updated_at: null }),
  });
}

// Import the routes AFTER the mock is registered.
import { DashboardPage } from "./pages/DashboardPage";
import { ExecutionsPage } from "./pages/ExecutionsPage";
import { ResultsHistoryPage } from "./pages/ResultsHistoryPage";
import { IssuesPage } from "./pages/IssuesPage";
import { IssuesReport } from "./components/IssuesReport";

async function mountAndSettle(el: React.ReactElement): Promise<{ host: HTMLDivElement; root: Root }> {
  const host = document.createElement("div");
  document.body.appendChild(host);
  const root = createRoot(host);
  await act(async () => { root.render(<MemoryRouter>{el}</MemoryRouter>); });
  // Let the effects' fetches resolve and the content render.
  await act(async () => { for (let i = 0; i < 6; i++) await Promise.resolve(); });
  return { host, root };
}

/** Run axe on a node and return only the blocking (critical/serious) violations. */
async function blockingViolations(node: HTMLElement) {
  const results = await axe(node);
  return results.violations.filter((v) => v.impact != null && BLOCKING.has(v.impact));
}

describe("axe smoke — five core routes have zero critical/serious violations", () => {
  beforeEach(() => { for (const k of Object.keys(apiImpl)) delete apiImpl[k]; installApi(); });
  afterEach(() => { document.body.innerHTML = ""; });

  it("Dashboard", async () => {
    const { host, root } = await mountAndSettle(<DashboardPage />);
    const v = await blockingViolations(host);
    expect(v, JSON.stringify(v.map((x) => ({ id: x.id, impact: x.impact, n: x.nodes.length })), null, 2)).toHaveLength(0);
    await act(async () => root.unmount());
  });

  it("Runs (ExecutionsPage)", async () => {
    const { host, root } = await mountAndSettle(<ExecutionsPage />);
    const v = await blockingViolations(host);
    expect(v, JSON.stringify(v.map((x) => ({ id: x.id, impact: x.impact, n: x.nodes.length })), null, 2)).toHaveLength(0);
    await act(async () => root.unmount());
  });

  it("Results (ResultsHistoryPage)", async () => {
    const { host, root } = await mountAndSettle(<ResultsHistoryPage />);
    const v = await blockingViolations(host);
    expect(v, JSON.stringify(v.map((x) => ({ id: x.id, impact: x.impact, n: x.nodes.length })), null, 2)).toHaveLength(0);
    await act(async () => root.unmount());
  });

  it("Scorecard detail (IssuesReport)", async () => {
    const { host, root } = await mountAndSettle(<IssuesReport report={ISSUES_REPORT} />);
    const v = await blockingViolations(host);
    expect(v, JSON.stringify(v.map((x) => ({ id: x.id, impact: x.impact, n: x.nodes.length })), null, 2)).toHaveLength(0);
    await act(async () => root.unmount());
  });

  it("Issues (IssuesPage)", async () => {
    const { host, root } = await mountAndSettle(<IssuesPage />);
    const v = await blockingViolations(host);
    expect(v, JSON.stringify(v.map((x) => ({ id: x.id, impact: x.impact, n: x.nodes.length })), null, 2)).toHaveLength(0);
    await act(async () => root.unmount());
  });
});
