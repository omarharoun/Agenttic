import { describe, it, expect } from "vitest";
import { applyEvent, emptyExec, type ExecState, type SSEEvent } from "./store";
import { pipelineStages } from "./components/PipelineFlow";

const ev = (type: string, node_id: string | null, data: Record<string, unknown>, seq: number): SSEEvent =>
  ({ seq, type, node_id, data });

describe("pipelineStages (SPEC-5 23.1)", () => {
  it("orders stages by execution order and carries per-node progress", () => {
    let s: ExecState = emptyExec();
    s = applyEvent(s, ev("execution_started", null, {}, 1));
    // 'run' starts first, then 'score' — even though 'score' sorts earlier
    s = applyEvent(s, ev("node_started", "run", {}, 2));
    s = applyEvent(s, ev("node_progress", "run",
      { event: "case_finished", index: 4, total: 20, ok: true, test_id: "t" }, 3));
    s = applyEvent(s, ev("node_started", "score", {}, 4));

    const stages = pipelineStages(s);
    expect(stages.map((x) => x.nodeId)).toEqual(["run", "score"]); // execution order, not alphabetical
    const run = stages.find((x) => x.nodeId === "run")!;
    expect(run.state).toBe("running");
    expect(run.done).toBe(5);       // index 4 -> 5 done (reducer semantics)
    expect(run.total).toBe(20);
    expect(stages.find((x) => x.nodeId === "score")!.total).toBe(0);
  });

  it("is empty before any node reports", () => {
    expect(pipelineStages(emptyExec())).toEqual([]);
  });

  it("reflects a failed node", () => {
    let s = emptyExec();
    s = applyEvent(s, ev("node_failed", "score", { error: "boom" }, 1));
    expect(pipelineStages(s)[0]).toMatchObject({ nodeId: "score", state: "failed" });
  });
});
