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

/** One thing that happened during a run. `text` is the terse line the advanced
 *  log prints; `data` is the event's own payload, kept so the guided view can
 *  render it as a readable card instead of re-parsing the sentence. */
export interface LogEntry {
  seq: number;
  type: string;
  nodeId: string | null;
  text: string;
  data: Record<string, any>;
}

export interface ExecState {
  executionId: string | null;
  status: string; // idle | running | waiting_approval | succeeded | failed | cancelled
  nodeStates: Record<string, NodeRunState>;
  progress: Record<string, { done: number; total: number }>;
  log: LogEntry[];
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

/** Events that each mean "one more unit of this step is finished" — a case for
 *  Run/Score, a task for Generate.
 *
 *  Progress is COUNTED rather than read off the event's `index`. Cases and
 *  generator tasks run concurrently, so events arrive out of order: case 9 can
 *  land before case 2, and an index-derived bar would jump to 90% and then back
 *  down. Counting is order-independent and can only go forwards. */
// Events that mean "one unit of work finished", for the progress bar. Anything
// else (a warning, a projection, a proposal) is a log line, not a tick.
const UNIT_DONE = new Set([
  "case_finished", "case_scored", "case_error", "case_resumed",
  "budget_exceeded", "cases_generated", "cases_skipped",
  "scenario_executed",
]);

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
    case "node_progress": {
      if (!nid) break;
      const total = Number(evt.data.total) || prev.progress[nid]?.total || 0;
      if (!total) break;
      const wasDone = prev.progress[nid]?.done ?? 0;
      next.progress[nid] = {
        total,
        done: Math.min(total, wasDone + (UNIT_DONE.has(evt.data.event) ? 1 : 0)),
      };
      break;
    }
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
    next.log = [...prev.log,
      { seq: evt.seq, type: evt.type, nodeId: nid, text, data: evt.data ?? {} }];
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
      if (d.event === "case_error")
        return `case ${d.index + 1}/${d.total} NOT SCORED (${d.test_id}): ${d.error ?? ""}`;
      // Money and refusals — the events a watcher most needs and the ones that
      // were silently dropped, because a payload with no `message` rendered "".
      if (d.event === "budget_stop")
        return `BUDGET STOP round ${d.round}: ${d.reason ?? ""} (${d.n_runs} run(s))`;
      if (d.event === "budget_warning")
        return `budget warning — projected $${Number(d.projected_usd ?? 0).toFixed(2)}`
          + (Array.isArray(d.warnings) && d.warnings.length ? `: ${d.warnings[0]}` : "");
      if (d.event === "cost_projection")
        return `projected ${d.projected_agent_runs}/${d.max_agent_runs} agent runs `
          + `(${d.n_train} train, ${d.n_heldout} held-out)`;
      // Generation pipeline
      if (d.event === "tasks_extracted") return `extracted ${d.n ?? d.count ?? "?"} task(s)`;
      if (d.event === "criteria_defined") return `defined ${d.n ?? d.count ?? "?"} criteria`;
      // CDV / rubric search
      if (d.event === "scenario_executed")
        return `scenario ${d.index} ${d.passed ? "passed" : "FAILED"} `
          + `(${d.trajectory}${(d.failures ?? []).length ? `, ${d.failures.length} failure(s)` : ""})`;
      if (d.event === "scenario_run_not_stored")
        return `scenario ${d.scenario_id} NOT STORED: ${d.error ?? ""}`;
      if (d.event === "propose")
        return `round ${d.round}: proposing against ${d.n_failing} failing criterion/a`;
      if (d.event === "candidate")
        return `round ${d.round} candidate ${d.index}: `
          + `${d.accepted ? "accepted" : "rejected"} — ${d.reason ?? ""}`;
      if (d.event === "round_done")
        return `round ${d.round} done: chose ${d.chosen ?? "nothing"}`
          + (d.reason ? ` — ${d.reason}` : "");
      // Per-case lifecycle
      if (d.event === "case_started")
        return `case ${d.index + 1}/${d.total} started (${d.test_id})`;
      if (d.event === "case_resumed")
        return `case ${d.index + 1}/${d.total} resumed from a stored trace (${d.test_id})`;
      if (d.event === "budget_exceeded")
        return `case ${d.index + 1}/${d.total} STOPPED on budget `
          + `($${Number(d.spent_usd ?? 0).toFixed(2)} spent) — ${d.test_id}`;
      // Generation
      if (d.event === "cases_generated")
        return `generated ${d.n_cases} case(s) for task ${d.index + 1}/${d.total}`;
      if (d.event === "cases_skipped")
        return `task ${d.index + 1}/${d.total} produced NO cases: ${d.reason ?? ""}`;
      // EGR probes
      if (d.event === "probe_started")
        return `probe ${d.probe_id} (${d.mechanism}) started`;
      if (d.event === "probe_finished")
        return `probe ${d.probe_id}: ${d.sub_score}`
          + (d.incident ? " — INCIDENT" : "");
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
  addNode: (ntype: string) => void;
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
  // Click a palette item: focus the existing node of that type if present
  // ("linked to its box"), otherwise add a new one (cascaded so it doesn't
  // stack) and select it. Drag-to-place still adds via the canvas onDrop.
  addNode: (ntype) =>
    set((s) => {
      const existing = s.nodes.find((n) => (n.data as any).ntype === ntype);
      if (existing) return { selectedNodeId: existing.id };
      const k = s.nodes.length;
      const id = `${ntype}_${Date.now().toString(36)}_${Math.floor(Math.random() * 1000)}`;
      const node: Node = {
        id, type: "agenttic",
        position: { x: 80 + (k * 48) % 480, y: 70 + (k * 56) % 360 },
        data: { ntype, label: "", config: {} },
      };
      return { nodes: [...s.nodes, node], selectedNodeId: id, dirty: true };
    }),
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
      type: "agenttic",
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
