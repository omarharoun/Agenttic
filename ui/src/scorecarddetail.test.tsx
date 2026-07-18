// @vitest-environment jsdom
/* SPEC-4 Step 19.3 — the Scorecard detail screen.

   We prove the certificate-grade claims a client reads over the operator's
   shoulder are really rendered:
     1. the executive header (success rate + Wilson interval) and the criterion
        badges (calibrated AND a PROVISIONAL one from an uncalibrated judge);
     2. expanding a per-case row reveals its per-criterion judge rationale;
     3. the loading state shows a layout-matched skeleton (not a void).

   Same hoisted-API-mock harness as moat.render.test.tsx. */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import React, { act } from "react";
import type { Scorecard, ScorecardSummary } from "./api";

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean })
  .IS_REACT_ACT_ENVIRONMENT = true;

const { apiStub, apiProxy } = vi.hoisted(() => {
  const stub: Record<string, ReturnType<typeof vi.fn>> = {};
  const proxy = new Proxy(stub, {
    get(target, prop: string) {
      if (!(prop in target)) target[prop] = vi.fn(() => new Promise(() => {}));
      return target[prop];
    },
  });
  return { apiStub: stub, apiProxy: proxy };
});

vi.mock("./api", async (importOriginal) => {
  const real = await importOriginal<typeof import("./api")>();
  return {
    ...real,
    api: apiProxy,
    auth: { get: () => null, set: () => {}, clear: () => {} },
  };
});

import { ScorecardDetailPage } from "./pages/ScorecardDetailPage";

function mount(scorecardId: string): { host: HTMLDivElement; root: Root } {
  const host = document.createElement("div");
  document.body.appendChild(host);
  const root = createRoot(host);
  act(() => {
    root.render(
      <MemoryRouter initialEntries={[`/app/scorecards/${scorecardId}`]}>
        <Routes>
          <Route path="/app/scorecards/:id" element={<ScorecardDetailPage />} />
        </Routes>
      </MemoryRouter>,
    );
  });
  return { host, root };
}

async function flush() {
  await act(async () => {
    await Promise.resolve(); await Promise.resolve();
    await Promise.resolve(); await Promise.resolve();
  });
}

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------
const SCORECARD: Scorecard = {
  scorecard_id: "sc-42",
  agent_id: "support-triage-agent",
  suite_id: "support-v1",
  suite_version: 3,
  rubric_id: "rub-1",
  rubric_version: 2,
  task_success_rate: 0.75,
  mean_cost_usd: 0.012,
  total_cost_usd: 0.096,
  total_scoring_cost_usd: 0.02,
  p95_latency_ms: 1800,
  per_criterion_means: {
    resolves_issue: 0.9,   // code-scored → calibrated
    tone: 0.6,             // judge, uncalibrated → PROVISIONAL, failing
  },
  errored_test_ids: [],
  visibility_tier: "glass_box",
  created_at: "2026-03-05T00:00:00Z",
  n_scored: 8,
  n_passed: 6,
  success_wilson_low: 0.4,
  success_wilson_high: 0.94,
  run_scores: [
    {
      trace_id: "tr-1", test_id: "case-1", passed: true,
      cost_usd: 0.011, scoring_cost_usd: 0.002, latency_ms: 1500, steps: 3,
      scoring_error: null,
      criterion_scores: [
        {
          criterion_id: "resolves_issue", score: 1, scorer: "code",
          calibrated: true, judge_rationale: null, cost_usd: 0,
        },
        {
          criterion_id: "tone", score: 0, scorer: "judge",
          calibrated: false, cost_usd: 0.002,
          judge_rationale: "Reply was curt and dismissive to the customer.",
        },
      ],
    },
    {
      trace_id: "tr-2", test_id: "case-2", passed: false,
      cost_usd: 0.013, scoring_cost_usd: 0.003, latency_ms: 2100, steps: 5,
      scoring_error: null,
      criterion_scores: [
        {
          criterion_id: "resolves_issue", score: 1, scorer: "code",
          calibrated: true, judge_rationale: null, cost_usd: 0,
        },
      ],
    },
  ],
};

const PREVIOUS: ScorecardSummary = {
  scorecard_id: "sc-40",
  agent_id: "support-triage-agent",
  suite_id: "support-v1",
  suite_version: 3,
  task_success_rate: 0.6,
  n_scored: 8,
  n_passed: 5,
  created_at: "2026-03-01T00:00:00Z",
};

// ---------------------------------------------------------------------------
describe("ScorecardDetailPage", () => {
  beforeEach(() => { for (const k of Object.keys(apiStub)) delete apiStub[k]; });
  afterEach(() => { document.body.innerHTML = ""; });

  it("renders the executive header, criterion badges (a PROVISIONAL one), and the regression delta", async () => {
    apiStub.getScorecard = vi.fn(async () => SCORECARD);
    apiStub.listScorecards = vi.fn(async () => [PREVIOUS, {
      ...SCORECARD, scorecard_id: "sc-42",
    } as ScorecardSummary]);

    const { host } = mount("sc-42");
    await flush();

    // executive header: the headline rate + a Wilson interval (n= …) hedge
    expect(host.querySelector(".sd-exec")).toBeTruthy();
    expect(host.textContent).toContain("75%");
    expect(host.querySelector(".uncertainty")).toBeTruthy();
    expect(host.textContent).toContain("n=8");

    // visibility banner
    expect(host.textContent).toContain("Glass box");

    // criterion breakdown: BOTH a calibrated and a provisional standing
    expect(host.querySelector(".sd-badge-calibrated")).toBeTruthy();
    const prov = host.querySelector(".sd-badge-provisional");
    expect(prov).toBeTruthy();
    expect(prov?.textContent).toContain("PROVISIONAL");

    // failing criterion links into Issues with the criterion filter
    const issueLink = host.querySelector<HTMLAnchorElement>("a.sd-issuelink");
    expect(issueLink?.getAttribute("href")).toContain("/app/issues?criterion=tone");

    // regression diff vs the prior scorecard for the same (agent, suite)
    expect(host.textContent).toContain("Comparing to");
    expect(host.querySelector(".sd-delta-up")).toBeTruthy(); // 0.60 → 0.75

    // called with the route param
    expect(apiStub.getScorecard).toHaveBeenCalledWith("sc-42");
  });

  it("expands a per-case row to reveal its judge rationale, keyboard-operable", async () => {
    apiStub.getScorecard = vi.fn(async () => SCORECARD);
    apiStub.listScorecards = vi.fn(async () => []);

    const { host } = mount("sc-42");
    await flush();

    // the rationale is hidden until the row is expanded
    expect(host.textContent).not.toContain("curt and dismissive");

    const row = host.querySelector<HTMLElement>(".sd-case-row");
    expect(row).toBeTruthy();
    expect(row?.getAttribute("aria-expanded")).toBe("false");

    // keyboard: Enter toggles it open
    act(() => {
      row!.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
    });
    await flush();

    expect(row?.getAttribute("aria-expanded")).toBe("true");
    expect(host.textContent).toContain("curt and dismissive");
    // rationale row links out to the trace
    const trace = host.querySelector<HTMLAnchorElement>("a.sd-tracelink[href*='/app/traces/']");
    expect(trace?.getAttribute("href")).toContain("/app/traces/tr-1");
  });

  it("honestly reports 'no prior run' when there is no earlier scorecard", async () => {
    apiStub.getScorecard = vi.fn(async () => SCORECARD);
    apiStub.listScorecards = vi.fn(async () => []);

    const { host } = mount("sc-42");
    await flush();

    expect(host.textContent).toContain("No prior scorecard");
  });

  it("shows a layout-matched skeleton while loading (never a void)", () => {
    apiStub.getScorecard = vi.fn(() => new Promise(() => {})); // never resolves
    const { host } = mount("sc-42");
    // no flush → still loading
    expect(host.querySelector(".sd-skel")).toBeTruthy();
    expect(host.querySelector('[aria-busy="true"]')).toBeTruthy();
  });
});
