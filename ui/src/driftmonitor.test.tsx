// @vitest-environment jsdom
/* SPEC-5 23.4 — the live drift strip re-derives the drift decision from the raw
   window through sim-core: the drifting criterion flags DRIFT with the
   production re-eval string; the steady one stays stable. */
import { afterEach, describe, expect, it, vi } from "vitest";
import { createRoot, type Root } from "react-dom/client";
import React, { act } from "react";

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const { apiMock } = vi.hoisted(() => ({ apiMock: { getLiveWindows: vi.fn() } }));
vi.mock("./api", async (importOriginal) => {
  const real = await importOriginal<typeof import("./api")>();
  return { ...real, api: apiMock };
});

import { DriftMonitor } from "./components/DriftMonitor";

let container: HTMLDivElement, root: Root;
afterEach(() => act(() => root.unmount()));

async function mount() {
  container = document.createElement("div");
  root = createRoot(container);
  await act(async () => {
    root.render(<DriftMonitor agentId="a" baselineScorecardId="sc" />);
    await Promise.resolve();
  });
}

describe("DriftMonitor", () => {
  it("flags a drifted criterion and keeps a steady one stable", async () => {
    apiMock.getLiveWindows.mockResolvedValue({
      agent_id: "a", window: 20, drift_threshold: 0.15,
      criteria: [
        { criterion_id: "injection", baseline_mean: 0.9, window_scores: Array(20).fill(0.5) }, // mean .50, drop .40 -> fires
        { criterion_id: "tool_call", baseline_mean: 0.8, window_scores: Array(20).fill(0.9) },  // mean .90, no drop
      ],
    });
    await mount();
    const flags = [...container.querySelectorAll(".dm-flag")].map((f) => f.textContent);
    expect(flags).toEqual(["DRIFT", "stable"]);
    expect(container.textContent).toContain("batch re-evaluation recommended");
    expect(container.querySelector(".dm-reeval")?.textContent).toContain("'injection'");
  });

  it("shows an empty state when there are no live scores", async () => {
    apiMock.getLiveWindows.mockResolvedValue({
      agent_id: "a", window: 20, drift_threshold: 0.15, criteria: [],
    });
    await mount();
    expect(container.textContent).toContain("No live scores yet");
  });
});
