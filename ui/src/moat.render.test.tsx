// @vitest-environment jsdom
/* SPEC-4 Step 20 — the three "moat" console screens.

   For each page (Lineage, Calibration, Escalations) we prove:
     1. it renders its real data (the differentiator is visible, not a spinner);
     2. it shows a one-thing empty invitation when there's nothing yet;
     3. (where relevant) a label / respond action calls the right api method
        with the right arguments — the write path is really wired.

   Same hoisted-API-mock harness as pagedata.test.tsx: pages call `api.<method>`,
   so a proxy of vi.fns is enough; tests set returns per case. */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter } from "react-router-dom";
import React, { act } from "react";
import type {
  AgentLineage, CalibrationReport, NextUnlabeled, EscalationInbox,
} from "./api";

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

import { LineagePage } from "./pages/LineagePage";
import { CalibrationPage } from "./pages/CalibrationPage";
import { EscalationsPage } from "./pages/EscalationsPage";

function mount(el: React.ReactElement): { host: HTMLDivElement; root: Root } {
  const host = document.createElement("div");
  document.body.appendChild(host);
  const root = createRoot(host);
  act(() => { root.render(<MemoryRouter>{el}</MemoryRouter>); });
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
const LINEAGE: AgentLineage = {
  agent_id: "support-triage-agent",
  nodes: [
    {
      hash: "baseline00abc", parent_hash: null, status: "promoted",
      created_at: "2026-01-01T00:00:00Z", diff_summary: "seed",
      scorecard_ids: ["sc-0"], approved_by: null, task_success_rate: 0.6,
      gate_receipt: { reason: "seed baseline", diff_summary: "", payload: {} },
    },
    {
      hash: "childpromo11", parent_hash: "baseline00abc", status: "promoted",
      created_at: "2026-01-02T00:00:00Z", diff_summary: "tighten refusal",
      scorecard_ids: ["sc-1"], approved_by: "alice", task_success_rate: 0.78,
      gate_receipt: {
        reason: "PROMOTE: accuracy +0.18 (eps=0.02); cost ok; latency ok",
        diff_summary: "tightened the refusal wording", payload: { epsilon: 0.02 },
      },
    },
    {
      hash: "childreject22", parent_hash: "baseline00abc", status: "rejected",
      created_at: "2026-01-02T01:00:00Z", diff_summary: "verbose variant",
      scorecard_ids: ["sc-2"], approved_by: null, task_success_rate: 0.55,
      gate_receipt: {
        reason: "REJECT: accuracy -0.05 below baseline; latency +40% veto",
        diff_summary: "", payload: {},
      },
    },
  ],
  edges: [
    { from: "baseline00abc", to: "childpromo11" },
    { from: "baseline00abc", to: "childreject22" },
  ],
};

const CALIBRATION: CalibrationReport = {
  criteria: [
    {
      criterion_id: "resolves_issue", suite_id: "support-v1", agreement: 0.92,
      label_count: 40, paired_count: 38, threshold: 0.8, min_labels: 20,
      status: "calibrated",
    },
    {
      criterion_id: "tone", suite_id: "support-v1", agreement: 0.71,
      label_count: 25, paired_count: 22, threshold: 0.8, min_labels: 20,
      status: "PROVISIONAL",
    },
  ],
  open_requests: [
    {
      request_id: "req-1", criterion_id: "tone", suite_id: "support-v1",
      reason: "agreement slipped below threshold", status: "open",
      created_at: "2026-02-01T00:00:00Z",
    },
  ],
};

const NEXT: NextUnlabeled = {
  exhausted: false,
  criterion: { criterion_id: "tone", description: "polite + professional", scale: "three_point" },
  suite_id: "support-v1",
  trace: {
    trace_id: "tr-9", agent_id: "support-triage-agent", agent_config_hash: "h",
    test_case_id: "tc-1", spans: [], visibility: "glass_box",
    final_output: "Sorry, I can't help with that.", total_cost_usd: 0.01,
    total_latency_ms: 500, total_steps: 2, source: "batch", escalated: false,
    schema_version: "0.3.0",
  },
  anchors: [
    { score: 0, label: "fails the criterion" },
    { score: 0.5, label: "partially meets the criterion" },
    { score: 1, label: "fully meets the criterion" },
  ],
};

const INBOX: EscalationInbox = {
  pending: [
    {
      trace_id: "esc-1", agent_id: "support-triage-agent", test_case_id: "tc-3",
      question: "Should I issue a refund over $100?",
      context: { order_id: "o-42" },
      autonomy_policy: { tool: "issue_refund", tool_input: { amount: 150 }, policy: "require_human_over_100" },
    },
  ],
  pending_count: 1,
  resolved: [
    {
      feedback_id: "fb-1", trace_id: "esc-0", agent_id: "support-triage-agent",
      response: "Approved", created_at: "2026-03-01T00:00:00Z",
    },
  ],
};

// ---------------------------------------------------------------------------
describe("LineagePage", () => {
  beforeEach(() => { for (const k of Object.keys(apiStub)) delete apiStub[k]; });
  afterEach(() => { document.body.innerHTML = ""; });

  it("renders the config family tree with the gate receipt and greys rejected nodes", async () => {
    apiStub.agentLineage = vi.fn(async () => LINEAGE);
    const { host } = mount(<LineagePage />);
    await flush();
    // the tree nodes render
    expect(host.querySelectorAll(".lineage-node").length).toBe(3);
    // a rejected node is greyed
    expect(host.querySelector(".lineage-node.is-rejected")).toBeTruthy();
    // the promoted head's verbatim verdict shows in the receipt panel
    expect(host.textContent).toContain("Gate receipt");
    expect(host.textContent).toContain("accuracy +0.18");
    // called with the seeded default agent
    expect(apiStub.agentLineage).toHaveBeenCalledWith("support-triage-agent");
  });

  it("invites viewing the seeded agent when a lineage is empty", async () => {
    apiStub.agentLineage = vi.fn(async () => ({ agent_id: "x", nodes: [], edges: [] }));
    const { host } = mount(<LineagePage />);
    await flush();
    expect(host.querySelector(".empty-state")).toBeTruthy();
    expect(host.querySelectorAll(".empty-action .btn-primary").length).toBe(1);
  });
});

describe("CalibrationPage", () => {
  beforeEach(() => { for (const k of Object.keys(apiStub)) delete apiStub[k]; });
  afterEach(() => { document.body.innerHTML = ""; });

  it("renders per-criterion status and open optimization requests", async () => {
    apiStub.calibration = vi.fn(async () => CALIBRATION);
    const { host } = mount(<CalibrationPage />);
    await flush();
    expect(host.textContent).toContain("resolves_issue");
    expect(host.textContent).toContain("calibrated");
    expect(host.textContent).toContain("provisional");
    expect(host.textContent).toContain("Open judge-optimization requests");
  });

  it("shows an empty invitation when there is nothing to calibrate", async () => {
    apiStub.calibration = vi.fn(async () => ({ criteria: [], open_requests: [] }));
    const { host } = mount(<CalibrationPage />);
    await flush();
    expect(host.querySelector(".empty-state")).toBeTruthy();
  });

  it("labeling a trace POSTs the human score on the {0,0.5,1} scale", async () => {
    apiStub.calibration = vi.fn(async () => CALIBRATION);
    apiStub.nextUnlabeled = vi.fn(async () => NEXT);
    apiStub.addLabel = vi.fn(async () => ({
      ok: true, suite_id: "support-v1", labels_path: "/x.csv", label_count: 26,
      criterion: { ...CALIBRATION.criteria[1], label_count: 26 },
    }));
    const { host } = mount(<CalibrationPage />);
    await flush();

    // start the workspace on a criterion
    const input = host.querySelector("#cal-criterion") as HTMLInputElement;
    const setVal = Object.getOwnPropertyDescriptor(
      window.HTMLInputElement.prototype, "value")!.set!;
    act(() => { setVal.call(input, "tone"); input.dispatchEvent(new Event("input", { bubbles: true })); });
    const startBtn = Array.from(host.querySelectorAll("button"))
      .find((b) => b.textContent?.includes("Start labeling")) as HTMLButtonElement;
    act(() => { startBtn.dispatchEvent(new MouseEvent("click", { bubbles: true })); });
    await flush();

    // the trace + anchors render; click the 0.5 anchor
    expect(host.textContent).toContain("tr-9");
    const half = Array.from(host.querySelectorAll("button"))
      .find((b) => b.textContent?.includes("0.5")) as HTMLButtonElement;
    expect(half).toBeTruthy();
    act(() => { half.dispatchEvent(new MouseEvent("click", { bubbles: true })); });
    await flush();

    expect(apiStub.addLabel).toHaveBeenCalledWith({
      trace_id: "tr-9", criterion_id: "tone", score: 0.5,
    });
  });
});

describe("EscalationsPage", () => {
  beforeEach(() => { for (const k of Object.keys(apiStub)) delete apiStub[k]; });
  afterEach(() => { document.body.innerHTML = ""; });

  it("renders pending questions with policy + a prominent pending count, and resolved history", async () => {
    apiStub.escalations = vi.fn(async () => INBOX);
    const { host } = mount(<EscalationsPage />);
    await flush();
    expect(host.textContent).toContain("Should I issue a refund over $100?");
    expect(host.textContent).toContain("require_human_over_100");
    expect(host.textContent).toContain("1 awaiting a decision");
    expect(host.textContent).toContain("Resolved");
    expect(host.textContent).toContain("Approved");
  });

  it("shows an empty state when the inbox is entirely empty", async () => {
    apiStub.escalations = vi.fn(async () => ({ pending: [], pending_count: 0, resolved: [] }));
    const { host } = mount(<EscalationsPage />);
    await flush();
    expect(host.querySelector(".empty-state")).toBeTruthy();
  });

  it("responding to an escalation POSTs the decision for that trace", async () => {
    apiStub.escalations = vi.fn(async () => INBOX);
    apiStub.respondEscalation = vi.fn(async () => ({
      resolved: { feedback_id: "fb-2", trace_id: "esc-1", agent_id: "a", response: "Deny", created_at: "2026-03-02T00:00:00Z" },
      pending_count: 0,
    }));
    const { host } = mount(<EscalationsPage />);
    await flush();

    const box = host.querySelector("#resp-esc-1") as HTMLTextAreaElement;
    const setVal = Object.getOwnPropertyDescriptor(
      window.HTMLTextAreaElement.prototype, "value")!.set!;
    act(() => { setVal.call(box, "Deny — over policy limit"); box.dispatchEvent(new Event("input", { bubbles: true })); });
    const btn = Array.from(host.querySelectorAll("button"))
      .find((b) => b.textContent?.includes("Respond")) as HTMLButtonElement;
    act(() => { btn.dispatchEvent(new MouseEvent("click", { bubbles: true })); });
    await flush();

    expect(apiStub.respondEscalation).toHaveBeenCalledWith("esc-1", "Deny — over policy limit");
  });
});
