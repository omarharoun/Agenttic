// @vitest-environment jsdom
/* SPEC-4 Step 19.4 — the trace viewer.

   Three proofs, same hoisted-API-mock harness as moat.render / pagedata:
     1. a multi-span GLASS-BOX trace renders the full span tree (N rows on the
        timeline), with the error span highlighted;
     2. a single-span BLACK-BOX trace renders honestly — one row plus the
        "single observed output, no internal spans" tier note, not a broken
        empty tree;
     3. while the fetch is pending the page shows the timeline-shaped skeleton.

   The page reads its trace id from the route, so we mount inside a
   MemoryRouter with a matching :id route. */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import React, { act } from "react";
import type { Trace } from "./api";

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

import { TraceViewerPage } from "./pages/TraceViewerPage";

function mount(traceId: string): { host: HTMLDivElement; root: Root } {
  const host = document.createElement("div");
  document.body.appendChild(host);
  const root = createRoot(host);
  act(() => {
    root.render(
      <MemoryRouter initialEntries={[`/app/traces/${traceId}`]}>
        <Routes>
          <Route path="/app/traces/:id" element={<TraceViewerPage />} />
        </Routes>
      </MemoryRouter>,
    );
  });
  return { host, root };
}

async function flush() {
  await act(async () => {
    await Promise.resolve(); await Promise.resolve(); await Promise.resolve();
  });
}

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------
function span(over: Partial<Trace["spans"][number]>): Trace["spans"][number] {
  return {
    span_id: "s", parent_id: null, kind: "llm_call", name: "span",
    start_time: "2026-01-01T00:00:00.000Z", end_time: "2026-01-01T00:00:01.000Z",
    input: {}, output: {}, error: null, tokens_in: null, tokens_out: null,
    cost_usd: null, attributes: {},
    ...over,
  };
}

const GLASS_BOX: Trace = {
  trace_id: "tr-glass", agent_id: "support-triage-agent", agent_config_hash: "h1",
  test_case_id: "tc-1", visibility: "glass_box",
  final_output: "Refund issued.", total_cost_usd: 0.0123,
  total_latency_ms: 3200, total_steps: 4, source: "batch", escalated: false,
  schema_version: "0.3.0",
  spans: [
    span({
      span_id: "root", parent_id: null, kind: "agent_decision", name: "plan",
      start_time: "2026-01-01T00:00:00.000Z", end_time: "2026-01-01T00:00:03.200Z",
    }),
    span({
      span_id: "llm-1", parent_id: "root", kind: "llm_call", name: "classify intent",
      start_time: "2026-01-01T00:00:00.100Z", end_time: "2026-01-01T00:00:01.000Z",
      tokens_in: 320, tokens_out: 48, cost_usd: 0.004,
      input: { prompt: "classify" }, output: { intent: "refund" },
    }),
    span({
      span_id: "tool-1", parent_id: "root", kind: "tool_call", name: "lookup order",
      start_time: "2026-01-01T00:00:01.100Z", end_time: "2026-01-01T00:00:01.600Z",
      output: { order_id: "o-42" },
    }),
    span({
      span_id: "err-1", parent_id: "root", kind: "error", name: "payment gateway timeout",
      start_time: "2026-01-01T00:00:01.700Z", end_time: "2026-01-01T00:00:02.000Z",
      error: "gateway timed out after 300ms",
    }),
    span({
      span_id: "out-1", parent_id: "root", kind: "final_output", name: "reply",
      start_time: "2026-01-01T00:00:02.100Z", end_time: "2026-01-01T00:00:03.200Z",
      output: { text: "Refund issued." },
    }),
  ],
};

const BLACK_BOX: Trace = {
  trace_id: "tr-black", agent_id: "opaque-vendor-agent", agent_config_hash: "h2",
  test_case_id: null, visibility: "black_box",
  final_output: "The capital of France is Paris.", total_cost_usd: 0,
  total_latency_ms: 900, total_steps: 1, source: "live", escalated: false,
  schema_version: "0.3.0",
  spans: [
    span({
      span_id: "obs", parent_id: null, kind: "final_output", name: "observed output",
      start_time: "2026-01-01T00:00:00.000Z", end_time: "2026-01-01T00:00:00.900Z",
      output: { text: "The capital of France is Paris." },
    }),
  ],
};

// ---------------------------------------------------------------------------
describe("TraceViewerPage", () => {
  beforeEach(() => { for (const k of Object.keys(apiStub)) delete apiStub[k]; });
  afterEach(() => { document.body.innerHTML = ""; });

  it("renders a multi-span glass-box trace as a span tree (N rows) with the error highlighted", async () => {
    apiStub.getTrace = vi.fn(async () => GLASS_BOX);
    const { host } = mount("tr-glass");
    await flush();

    // one row per span
    expect(host.querySelectorAll(".trace-row").length).toBe(GLASS_BOX.spans.length);
    // it's a real tree
    expect(host.querySelector("[role='tree']")).toBeTruthy();
    expect(host.querySelectorAll("[role='treeitem']").length).toBe(GLASS_BOX.spans.length);
    // the error span is highlighted
    expect(host.querySelector(".trace-row.is-error")).toBeTruthy();
    // header totals + tier are present
    expect(host.textContent).toContain("support-triage-agent");
    expect(host.textContent).toContain("glass-box");
    // called with the route id
    expect(apiStub.getTrace).toHaveBeenCalledWith("tr-glass");
  });

  it("expands a span to a JSON view with a copy button and LLM annotations", async () => {
    apiStub.getTrace = vi.fn(async () => GLASS_BOX);
    const { host } = mount("tr-glass");
    await flush();

    // the error span is open by default → its detail is present
    expect(host.textContent).toContain("gateway timed out after 300ms");

    // expand the llm span and check the cost/token annotations + JSON block
    const llmRow = Array.from(host.querySelectorAll(".trace-row"))
      .find((r) => r.textContent?.includes("classify intent")) as HTMLButtonElement;
    expect(llmRow).toBeTruthy();
    act(() => { llmRow.dispatchEvent(new MouseEvent("click", { bubbles: true })); });

    const openDetail = host.querySelector('[id="trace-detail-llm-1"]');
    expect(openDetail?.textContent).toContain("320");   // tokens in
    expect(openDetail?.textContent).toContain("intent"); // output json
    expect(openDetail?.querySelector(".trace-copy-btn")).toBeTruthy();
  });

  it("renders a single-span black-box trace as one honest row plus a tier note", async () => {
    apiStub.getTrace = vi.fn(async () => BLACK_BOX);
    const { host } = mount("tr-black");
    await flush();

    // the tier note is present and honest
    expect(host.textContent).toContain("single observed output, no internal spans");
    expect(host.textContent).toContain("black-box");
    // exactly one row — not a broken empty tree
    expect(host.querySelectorAll(".trace-row").length).toBe(1);
    // and no error styling
    expect(host.querySelector(".trace-row.is-error")).toBeFalsy();
  });

  it("shows a timeline-shaped skeleton while the fetch is pending", () => {
    // default proxy fn returns a never-resolving promise → stays loading
    const { host } = mount("tr-pending");
    expect(host.querySelector(".trace-skel")).toBeTruthy();
    expect(host.querySelectorAll(".trace-skel-row").length).toBeGreaterThan(0);
  });
});
