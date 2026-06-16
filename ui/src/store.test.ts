import { describe, expect, it } from "vitest";
import {
  applyEvent, emptyExec, useFlowStore, type ExecState, type SSEEvent,
} from "./store";

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

describe("addNode (clickable palette)", () => {
  it("adds a node and selects it, then focuses the existing one on re-add", () => {
    useFlowStore.setState({ nodes: [], edges: [], selectedNodeId: null });
    useFlowStore.getState().addNode("agent");
    let st = useFlowStore.getState();
    expect(st.nodes).toHaveLength(1);
    expect((st.nodes[0].data as any).ntype).toBe("agent");
    expect(st.selectedNodeId).toBe(st.nodes[0].id);
    const firstId = st.nodes[0].id;

    // re-adding the same type focuses the existing node (no duplicate)
    useFlowStore.getState().addNode("agent");
    st = useFlowStore.getState();
    expect(st.nodes).toHaveLength(1);
    expect(st.selectedNodeId).toBe(firstId);

    // a different type adds a new node
    useFlowStore.getState().addNode("run_suite");
    st = useFlowStore.getState();
    expect(st.nodes).toHaveLength(2);
    expect(st.dirty).toBe(true);
  });
});
