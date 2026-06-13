import type { Edge, Node } from "@xyflow/react";
import { create } from "zustand";
import type { NodeTypeSpec, WorkflowDoc } from "./api";

export type NodeRunState =
  | "idle"
  | "running"
  | "waiting"
  | "succeeded"
  | "failed"
  | "skipped";

export interface ExecState {
  executionId: string | null;
  status: string; // idle | running | waiting_approval | succeeded | failed | cancelled
  nodeStates: Record<string, NodeRunState>;
  progress: Record<string, { done: number; total: number }>;
  log: { seq: number; type: string; nodeId: string | null; text: string }[];
}

export const emptyExec = (): ExecState => ({
  executionId: null,
  status: "idle",
  nodeStates: {},
  progress: {},
  log: [],
});

export interface SSEEvent {
  seq: number;
  type: string;
  node_id: string | null;
  data: Record<string, any>;
}

/** Pure reducer: one SSE event -> next execution state (unit-tested). */
export function applyEvent(prev: ExecState, evt: SSEEvent): ExecState {
  const next: ExecState = {
    ...prev,
    nodeStates: { ...prev.nodeStates },
    progress: { ...prev.progress },
    log: prev.log,
  };
  const nid = evt.node_id;
  switch (evt.type) {
    case "execution_started":
      next.status = "running";
      break;
    case "node_started":
      if (nid) next.nodeStates[nid] = "running";
      break;
    case "node_progress":
      if (nid && typeof evt.data.index === "number") {
        const done =
          evt.data.event === "case_finished" || evt.data.event === "case_scored"
            ? evt.data.index + 1
            : evt.data.index;
        next.progress[nid] = { done, total: evt.data.total ?? 0 };
      }
      break;
    case "node_waiting":
      if (nid) next.nodeStates[nid] = "waiting";
      next.status = "waiting_approval";
      break;
    case "node_completed":
      if (nid) next.nodeStates[nid] = "succeeded";
      break;
    case "node_failed":
      if (nid) next.nodeStates[nid] = "failed";
      break;
    case "node_skipped":
      if (nid) next.nodeStates[nid] = "skipped";
      break;
    case "node_retry":
      // node stays "running"; the log line records the attempt
      break;
    case "execution_succeeded":
    case "execution_failed":
    case "execution_cancelled":
    case "execution_completed_with_errors":
      next.status = evt.type.replace("execution_", "");
      break;
  }
  const text = summarize(evt);
  if (text) {
    next.log = [...prev.log, { seq: evt.seq, type: evt.type, nodeId: nid, text }];
  }
  return next;
}

function summarize(evt: SSEEvent): string {
  const d = evt.data ?? {};
  switch (evt.type) {
    case "execution_started":
      return "execution started";
    case "node_started":
      return "started";
    case "node_progress":
      if (d.event === "case_finished")
        return `case ${d.index + 1}/${d.total} ${d.ok ? "ok" : "FAILED"} (${d.test_id})`;
      if (d.event === "case_scored")
        return `scored ${d.index + 1}/${d.total} ${d.passed ? "pass" : "fail"} (${d.test_id})`;
      if (d.message) return d.message;
      return "";
    case "node_waiting":
      return `waiting for approval of suite ${d.suite_id} v${d.version}`;
    case "node_completed":
      return "completed";
    case "node_failed":
      return `failed${d.continued ? " (continued)" : ""}: ${d.error ?? ""}`;
    case "node_retry":
      return `retry ${d.attempt}/${d.of} after error: ${d.error ?? ""}`;
    case "node_skipped":
      return "skipped (no input — upstream produced none)";
    case "execution_succeeded":
      return "execution succeeded ✓";
    case "execution_failed":
      return "execution failed";
    case "execution_completed_with_errors":
      return "completed with errors ⚠";
    case "execution_cancelled":
      return "execution cancelled";
    default:
      return "";
  }
}

interface FlowState {
  workflowId: string;
  workflowName: string;
  nodes: Node[];
  edges: Edge[];
  catalog: Record<string, NodeTypeSpec>;
  selectedNodeId: string | null;
  exec: ExecState;
  dirty: boolean;
  setCatalog: (specs: NodeTypeSpec[]) => void;
  setGraph: (nodes: Node[], edges: Edge[]) => void;
  setWorkflowMeta: (id: string, name: string) => void;
  select: (id: string | null) => void;
  updateConfig: (nodeId: string, config: Record<string, any>) => void;
  setExec: (exec: ExecState) => void;
  pushEvent: (evt: SSEEvent) => void;
  markDirty: (d: boolean) => void;
}

export const useFlowStore = create<FlowState>((set) => ({
  workflowId: "my-workflow",
  workflowName: "My workflow",
  nodes: [],
  edges: [],
  catalog: {},
  selectedNodeId: null,
  exec: emptyExec(),
  dirty: false,
  setCatalog: (specs) =>
    set({ catalog: Object.fromEntries(specs.map((s) => [s.type, s])) }),
  setGraph: (nodes, edges) => set({ nodes, edges }),
  setWorkflowMeta: (workflowId, workflowName) =>
    set({ workflowId, workflowName }),
  select: (selectedNodeId) => set({ selectedNodeId }),
  updateConfig: (nodeId, config) =>
    set((s) => ({
      dirty: true,
      nodes: s.nodes.map((n) =>
        n.id === nodeId ? { ...n, data: { ...n.data, config } } : n),
    })),
  setExec: (exec) => set({ exec }),
  pushEvent: (evt) => set((s) => ({ exec: applyEvent(s.exec, evt) })),
  markDirty: (dirty) => set({ dirty }),
}));

/** Canvas graph -> backend workflow document. */
export function toWorkflowDoc(
  workflowId: string,
  name: string,
  nodes: Node[],
  edges: Edge[],
): WorkflowDoc {
  return {
    workflow_id: workflowId,
    name,
    nodes: nodes.map((n) => ({
      node_id: n.id,
      type: (n.data as any).ntype,
      label: (n.data as any).label ?? "",
      position: { x: n.position.x, y: n.position.y },
      config: (n.data as any).config ?? {},
      retries: (n.data as any).retries ?? 0,
      continue_on_error: (n.data as any).continue_on_error ?? false,
    })),
    edges: edges.map((e) => ({
      edge_id: e.id,
      source: e.source,
      source_port: e.sourceHandle ?? "out",
      target: e.target,
      target_port: e.targetHandle ?? "in",
    })),
  };
}

/** Backend workflow document -> canvas graph. */
export function fromWorkflowDoc(wf: WorkflowDoc): { nodes: Node[]; edges: Edge[] } {
  return {
    nodes: wf.nodes.map((n) => ({
      id: n.node_id,
      type: "ascore",
      position: n.position ?? { x: 0, y: 0 },
      data: { ntype: n.type, label: n.label, config: n.config,
              retries: (n as any).retries ?? 0,
              continue_on_error: (n as any).continue_on_error ?? false },
    })),
    edges: wf.edges.map((e) => ({
      id: e.edge_id,
      source: e.source,
      sourceHandle: e.source_port,
      target: e.target,
      targetHandle: e.target_port,
      animated: true,
    })),
  };
}
