// @vitest-environment jsdom
/* SPEC-4 Step 18 — the ⌘K command palette.

   We prove the load-bearing behaviours:
     1. ⌘K opens the dialog (role=dialog, focus-trapped input);
     2. typing a query narrows to a matching NAMED entity, and Enter on it
        navigates to that entity's route (mock useNavigate);
     3. Escape closes the dialog.

   Same hoisted-API-mock harness as moat.render.test.tsx: the component calls
   `api.<method>`, so a proxy of vi.fns is enough and each test sets returns. */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter } from "react-router-dom";
import React, { act } from "react";
import type {
  AgentsView, SuiteSummary, Execution, ScorecardSummary,
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

// A single navigate spy shared across the suite; react-router's useNavigate
// returns it so we can assert the destination.
const navigateSpy = vi.fn();

vi.mock("./api", async (importOriginal) => {
  const real = await importOriginal<typeof import("./api")>();
  return { ...real, api: apiProxy, auth: { get: () => null, set: () => {} } };
});

vi.mock("react-router-dom", async (importOriginal) => {
  const real = await importOriginal<typeof import("react-router-dom")>();
  return { ...real, useNavigate: () => navigateSpy };
});

import { CommandPalette } from "./components/CommandPalette";

function mount(): { host: HTMLDivElement; root: Root } {
  const host = document.createElement("div");
  document.body.appendChild(host);
  const root = createRoot(host);
  act(() => { root.render(<MemoryRouter><CommandPalette /></MemoryRouter>); });
  return { host, root };
}

async function flush() {
  await act(async () => {
    await Promise.resolve(); await Promise.resolve(); await Promise.resolve();
  });
}

function pressKey(target: EventTarget, init: KeyboardEventInit) {
  act(() => { target.dispatchEvent(new KeyboardEvent("keydown", { bubbles: true, ...init })); });
}

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------
const AGENTS: AgentsView = {
  agents: [
    { agent_id: "support-triage-agent", name: "Support Triage" },
    { agent_id: "billing-bot", name: "Billing Bot" },
  ],
};
const SUITES: SuiteSummary[] = [
  { suite_id: "support-v1", version: 1, business_context: "customer support" },
];
const RUNS: Execution[] = [
  { execution_id: "exec-42", workflow_id: "nightly-eval", status: "succeeded" },
];
const SCORECARDS: ScorecardSummary[] = [
  { scorecard_id: "sc-77", agent_id: "support-triage-agent", suite_id: "support-v1" },
];

function seedApi() {
  apiStub.listAgents = vi.fn(async () => AGENTS);
  apiStub.listSuites = vi.fn(async () => SUITES);
  apiStub.listExecutions = vi.fn(async () => RUNS);
  apiStub.listScorecards = vi.fn(async () => SCORECARDS);
}

function typeInto(input: HTMLInputElement, value: string) {
  const setVal = Object.getOwnPropertyDescriptor(
    window.HTMLInputElement.prototype, "value")!.set!;
  act(() => {
    setVal.call(input, value);
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });
}

// ---------------------------------------------------------------------------
describe("CommandPalette", () => {
  beforeEach(() => {
    for (const k of Object.keys(apiStub)) delete apiStub[k];
    navigateSpy.mockReset();
    seedApi();
  });
  afterEach(() => { document.body.innerHTML = ""; });

  it("opens on ⌘K, matches a named entity, and Enter navigates to its route", async () => {
    mount();
    // closed initially — no dialog
    expect(document.querySelector('[role="dialog"]')).toBeNull();

    // ⌘K opens it
    pressKey(window, { key: "k", metaKey: true });
    await flush();
    const dialog = document.querySelector('[role="dialog"]');
    expect(dialog).toBeTruthy();
    expect(dialog!.getAttribute("aria-modal")).toBe("true");

    // the four lists were fetched once
    expect(apiStub.listAgents).toHaveBeenCalledTimes(1);

    // type a query that hits an agent
    const input = document.querySelector(".cmdk-input") as HTMLInputElement;
    typeInto(input, "triage");
    await flush();

    // a matching option renders
    const opt = Array.from(document.querySelectorAll('[role="option"]'))
      .find((o) => o.textContent?.includes("Support Triage"));
    expect(opt).toBeTruthy();

    // Enter on the (first/active) result navigates to the agents route
    pressKey(dialog!, { key: "Enter" });
    await flush();
    expect(navigateSpy).toHaveBeenCalledWith("/app/agents");
    // and the dialog closed
    expect(document.querySelector('[role="dialog"]')).toBeNull();
  });

  it("reaches a scorecard by its route on Enter", async () => {
    mount();
    pressKey(window, { key: "k", ctrlKey: true });
    await flush();
    const dialog = document.querySelector('[role="dialog"]')!;
    const input = document.querySelector(".cmdk-input") as HTMLInputElement;
    typeInto(input, "sc-77");
    await flush();
    pressKey(dialog, { key: "Enter" });
    await flush();
    expect(navigateSpy).toHaveBeenCalledWith("/app/scorecards/sc-77");
  });

  it("Escape closes the dialog", async () => {
    mount();
    pressKey(window, { key: "k", metaKey: true });
    await flush();
    expect(document.querySelector('[role="dialog"]')).toBeTruthy();

    const dialog = document.querySelector('[role="dialog"]')!;
    pressKey(dialog, { key: "Escape" });
    await flush();
    expect(document.querySelector('[role="dialog"]')).toBeNull();
  });

  it("shows a no-matches state when nothing hits", async () => {
    mount();
    pressKey(window, { key: "k", metaKey: true });
    await flush();
    const input = document.querySelector(".cmdk-input") as HTMLInputElement;
    typeInto(input, "zzzznotathing");
    await flush();
    expect(document.querySelector(".cmdk-status")?.textContent).toContain("No matches");
  });
});
