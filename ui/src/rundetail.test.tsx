// @vitest-environment jsdom
/* SPEC-4 Step 19.2 — the Run detail live view.

   Proof the view is genuinely LIVE, not a one-shot render:
   1. It shows a loading skeleton while the initial execution fetch is pending.
   2. The mocked SSE hook (useExecutionEvents) scripts a running → done sequence
      by pushing events into the real flow store; the per-node progress table
      must reflect those live rows WITHOUT a refetch.
   3. Once the run reaches a terminal status and results carry a scorecard, the
      inline "View scorecard →" handoff appears.

   Both `../api` and `../sse` are mocked with hoisted stubs so the component
   consumes scripted data. The store is real (it's the wiring under test). */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import React, { act } from "react";

import { useFlowStore, emptyExec } from "./store";

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean })
  .IS_REACT_ACT_ENVIRONMENT = true;

// ---------------------------------------------------------------------------
// Hoisted mocks — api methods and the SSE hook controller.
// ---------------------------------------------------------------------------
const { apiMock, sseControl } = vi.hoisted(() => {
  return {
    apiMock: {
      getExecution: vi.fn(),
      executionResults: vi.fn(),
      getTrace: vi.fn(),
      cancel: vi.fn(),
    },
    // A place for a test to install a "script" the mocked hook will run.
    sseControl: {
      script: null as null | (() => void),
      subscribedId: null as string | null,
    },
  };
});

vi.mock("./api", async (importOriginal) => {
  const real = await importOriginal<typeof import("./api")>();
  return { ...real, api: apiMock };
});

// The mocked hook: when mounted, run whatever scripted sequence the test set,
// pushing SSE events into the real flow store (the same store the real hook
// feeds). This drives a running → done progression the component reacts to.
// Mirror the real hook's contract (subscribe by executionId, no return value).
// It records the last subscribed id; the test drives events AFTER the page's
// initial fetch has seeded the store — exactly as a live stream would arrive.
vi.mock("./sse", () => ({
  useExecutionEvents: (executionId: string | null) => {
    sseControl.subscribedId = executionId;
  },
}));

// Push the scripted running→done sequence into the real store, as the live
// hook would once events start arriving.
function runScript() {
  if (sseControl.script) sseControl.script();
}

import { RunDetailPage } from "./pages/RunDetailPage";

// ---------------------------------------------------------------------------
function mount(id: string): { host: HTMLDivElement; root: Root } {
  const host = document.createElement("div");
  document.body.appendChild(host);
  const root = createRoot(host);
  act(() => {
    root.render(
      <MemoryRouter initialEntries={[`/app/runs/${id}`]}>
        <Routes>
          <Route path="/app/runs/:id" element={<RunDetailPage />} />
        </Routes>
      </MemoryRouter>,
    );
  });
  return { host, root };
}

async function flush() {
  await act(async () => { await Promise.resolve(); await Promise.resolve(); await Promise.resolve(); });
}

const EXEC = {
  execution_id: "exec-1",
  workflow_id: "wf-1",
  status: "running",
  started_at: new Date().toISOString(),
  node_states: { run: "running" },
  node_outputs: {},
};

const RESULTS = {
  status: "succeeded",
  scorecards: [{
    node_id: "score",
    scorecard_id: "sc-42",
    agent_id: "agent-x",
    suite_id: "suite-1",
    suite_version: 1,
    task_success_rate: 0.75,
    n_scored: 4,
    n_passed: 3,
    success_wilson_low: 0.3,
    success_wilson_high: 0.95,
    mean_cost_usd: 0.01,
    total_cost_usd: 0.04,
    total_scoring_cost_usd: 0.02,
    cached: false,
    p95_latency_ms: 900,
    per_criterion_means: {},
    errored_test_ids: [],
    visibility_tier: "glass_box",
  }],
  cases: [
    { node_id: "run", test_id: "t1", passed: true, scoring_error: null,
      prediction: "ok", expected: null, cost_usd: 0.01, scoring_cost_usd: 0.005,
      steps: 3, latency_ms: 400, criteria: [], trace_id: "trace-1" },
    { node_id: "run", test_id: "t2", passed: false, scoring_error: null,
      prediction: "no", expected: null, cost_usd: 0.02, scoring_cost_usd: 0.005,
      steps: 5, latency_ms: 600, criteria: [], trace_id: "trace-2" },
  ],
};

describe("RunDetailPage — the live run view", () => {
  let root: Root | null = null;
  let host: HTMLDivElement | null = null;

  beforeEach(() => {
    vi.clearAllMocks();
    sseControl.script = null;
    // reset the shared store between tests
    useFlowStore.getState().setExec(emptyExec());
    apiMock.getTrace.mockImplementation(async () => ({
      trace_id: "trace-1", agent_id: "a", agent_config_hash: "h",
      test_case_id: "t1", spans: [], visibility: "glass_box",
      final_output: "", total_cost_usd: 0, total_latency_ms: 0,
      total_steps: 0, source: "x", escalated: false, schema_version: "0.3.0",
    }));
    apiMock.cancel.mockResolvedValue({ cancelled: "exec-1" });
  });

  afterEach(() => {
    if (root && host) { act(() => root!.unmount()); host.remove(); }
    root = null; host = null;
  });

  it("shows a loading skeleton while the initial fetch is pending", () => {
    apiMock.getExecution.mockReturnValue(new Promise(() => {})); // never resolves
    apiMock.executionResults.mockReturnValue(new Promise(() => {}));
    const m = mount("exec-1");
    root = m.root; host = m.host;
    expect(host.querySelector(".skel-wrap,.pagedata-skel")).toBeTruthy();
  });

  it("renders live progress rows driven by the SSE hook, then the scorecard handoff on completion", async () => {
    // Script the SSE hook: on mount it drives run → done via the real store.
    sseControl.script = () => {
      const push = useFlowStore.getState().pushEvent;
      push({ seq: 1, type: "execution_started", node_id: null, data: {} });
      push({ seq: 2, type: "node_started", node_id: "run", data: {} });
      push({ seq: 3, type: "node_progress", node_id: "run",
        data: { event: "case_finished", index: 1, total: 2, ok: true, test_id: "t2" } });
      push({ seq: 4, type: "node_completed", node_id: "run", data: {} });
      push({ seq: 5, type: "execution_succeeded", node_id: null, data: {} });
    };

    apiMock.getExecution.mockResolvedValue({ ...EXEC });
    apiMock.executionResults.mockResolvedValue({ ...RESULTS });

    const m = mount("exec-1");
    root = m.root; host = m.host;
    await flush(); // initial fetch seeds the store
    // Now the "stream" delivers events — the live region updates without refetch.
    await act(async () => { runScript(); await Promise.resolve(); });

    // The live per-node progress table has a row for the "run" node with live
    // progress from the streamed events (2 / 2 after the scripted completion).
    const nodeTable = host.querySelector(".run-nodes");
    expect(nodeTable).toBeTruthy();
    expect(nodeTable!.textContent).toContain("run");
    expect(nodeTable!.textContent).toContain("2 / 2");

    // The live status region reflects the streamed terminal status.
    const statusChip = host.querySelector('[role="status"][aria-live="polite"]');
    expect(statusChip!.textContent!.toLowerCase()).toContain("succeeded");

    // Per-case rows rendered from results.
    expect(host.querySelector(".run-cases")!.textContent).toContain("t1");
    expect(host.querySelector(".run-cases")!.textContent).toContain("t2");

    // On completion, the "View scorecard →" handoff links to the scorecard.
    const handoff = Array.from(host.querySelectorAll("a"))
      .find((a) => /View scorecard/i.test(a.textContent ?? ""));
    expect(handoff).toBeTruthy();
    expect(handoff!.getAttribute("href")).toContain("/app/scorecards/sc-42");

    // Per-case trace links point at /app/traces/{trace_id}.
    const traceLink = Array.from(host.querySelectorAll("a"))
      .find((a) => a.getAttribute("href")?.includes("/app/traces/trace-1"));
    expect(traceLink).toBeTruthy();
  });

  it("keyboard-selects a case row to load its streaming trace preview", async () => {
    sseControl.script = () => {
      useFlowStore.getState().pushEvent(
        { seq: 1, type: "execution_succeeded", node_id: null, data: {} });
    };
    apiMock.getExecution.mockResolvedValue({ ...EXEC, status: "succeeded" });
    apiMock.executionResults.mockResolvedValue({ ...RESULTS });

    const m = mount("exec-1");
    root = m.root; host = m.host;
    await flush();
    await act(async () => { runScript(); await Promise.resolve(); });

    const row = host.querySelector('.run-cases tr[role="button"]') as HTMLElement;
    expect(row).toBeTruthy();
    act(() => {
      row.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
    });
    await flush();
    expect(apiMock.getTrace).toHaveBeenCalled();
    expect(host.querySelector(".run-trace-panel")).toBeTruthy();
  });
});
