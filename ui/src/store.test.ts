import { describe, expect, it } from "vitest";
import { applyEvent, emptyExec, type ExecState, type SSEEvent } from "./store";

const ev = (type: string, node_id: string | null = null,
            data: Record<string, any> = {}, seq = 1): SSEEvent =>
  ({ seq, type, node_id, data });

describe("applyEvent reducer", () => {
  it("walks a node through its lifecycle", () => {
    let s: ExecState = emptyExec();
    s = applyEvent(s, ev("execution_started", null, {}, 1));
    expect(s.status).toBe("running");
    s = applyEvent(s, ev("node_started", "run", {}, 2));
    expect(s.nodeStates.run).toBe("running");
    s = applyEvent(s, ev("node_progress", "run",
      { event: "case_finished", index: 6, total: 10, ok: true, test_id: "t" }, 3));
    expect(s.progress.run).toEqual({ done: 7, total: 10 });
    s = applyEvent(s, ev("node_completed", "run", {}, 4));
    expect(s.nodeStates.run).toBe("succeeded");
    s = applyEvent(s, ev("execution_succeeded", null, {}, 5));
    expect(s.status).toBe("succeeded");
    expect(s.log.map((l) => l.seq)).toEqual([1, 2, 3, 4, 5]);
  });

  it("gate waiting flips execution status and node state", () => {
    let s = applyEvent(emptyExec(),
      ev("node_waiting", "gate", { suite_id: "s", version: 1 }));
    expect(s.status).toBe("waiting_approval");
    expect(s.nodeStates.gate).toBe("waiting");
    expect(s.log[0].text).toContain("approval");
  });

  it("failure and skip propagate to node states", () => {
    let s = applyEvent(emptyExec(), ev("node_failed", "score", { error: "boom" }));
    s = applyEvent(s, ev("node_skipped", "card", {}, 2));
    expect(s.nodeStates).toEqual({ score: "failed", card: "skipped" });
    expect(s.log[0].text).toContain("boom");
  });
});
