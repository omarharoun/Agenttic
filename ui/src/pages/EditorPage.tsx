import { ReactFlowProvider } from "@xyflow/react";
import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import { Canvas } from "../canvas/Canvas";
import { ConfigPanel } from "../panels/ConfigPanel";
import { Palette } from "../panels/Palette";
import { useExecutionEvents } from "../sse";
import {
  emptyExec,
  fromWorkflowDoc,
  toWorkflowDoc,
  useFlowStore,
} from "../store";

/** Canonical starter pipeline shown when no workflow exists yet. */
const STARTER = {
  workflow_id: "my-workflow",
  name: "Benchmark pipeline",
  nodes: [
    { node_id: "agent", type: "agent", label: "", position: { x: 40, y: 230 },
      config: {
        variant: "reference", agent_id: "agent-under-test",
        // task instructions ARE the configuration under test — edit and
        // re-run to see the scorecard move
        system_prompt:
          "You are the ticket-triage step of a support workflow. You receive " +
          "a JSON object with a \"ticket\" field. Classify it into exactly one " +
          "queue: billing (payments, charges, refunds, invoices), technical " +
          "(crashes, errors, bugs, login/password issues), or general " +
          "(everything else). Consult the knowledge base routing_rules with " +
          "lookup_kb when a ticket is ambiguous. Your FINAL message must be " +
          "ONLY the queue name — one lowercase word: billing, technical, or " +
          "general.",
      } },
    { node_id: "run", type: "run_suite", label: "", position: { x: 300, y: 140 },
      config: { suite_id: "pilot-support-triage" } },
    { node_id: "score", type: "score", label: "", position: { x: 540, y: 140 },
      config: {} },
    { node_id: "card", type: "scorecard", label: "", position: { x: 760, y: 140 },
      config: {} },
    { node_id: "rpt", type: "report", label: "", position: { x: 960, y: 140 },
      config: {} },
  ],
  edges: [
    { edge_id: "e1", source: "agent", source_port: "agent", target: "run", target_port: "agent" },
    { edge_id: "e2", source: "run", source_port: "run", target: "score", target_port: "run" },
    { edge_id: "e3", source: "score", source_port: "scored", target: "card", target_port: "scored" },
    { edge_id: "e4", source: "card", source_port: "scorecard", target: "rpt", target_port: "scorecard" },
  ],
};

/** Full business-workflow template: doc → generator → human gate feeding the
 * eval chain, exactly the client-engagement loop from the README. */
const FULL_TEMPLATE = {
  workflow_id: "business-workflow",
  name: "Business workflow benchmark",
  nodes: [
    { node_id: "doc", type: "business_doc", label: "", position: { x: 20, y: 60 },
      config: { text: "" } },
    { node_id: "gen", type: "generator", label: "", position: { x: 240, y: 60 },
      config: { suite_id: "generated-suite", cases_per_task: 5 } },
    { node_id: "gate", type: "human_gate", label: "", position: { x: 470, y: 60 },
      config: {} },
    { node_id: "agent", type: "agent", label: "", position: { x: 470, y: 300 },
      config: { variant: "managed", agent_id: "workflow-under-test",
                agent_yaml_path: "", deploy: true } },
    { node_id: "run", type: "run_suite", label: "", position: { x: 700, y: 170 },
      config: {} },
    { node_id: "score", type: "score", label: "", position: { x: 920, y: 170 },
      config: {} },
    { node_id: "card", type: "scorecard", label: "", position: { x: 1120, y: 170 },
      config: {} },
    { node_id: "rpt", type: "report", label: "", position: { x: 1310, y: 170 },
      config: {} },
  ],
  edges: [
    { edge_id: "t1", source: "doc", source_port: "doc", target: "gen", target_port: "doc" },
    { edge_id: "t2", source: "gen", source_port: "suite", target: "gate", target_port: "suite" },
    { edge_id: "t3", source: "gate", source_port: "suite", target: "run", target_port: "suite" },
    { edge_id: "t4", source: "agent", source_port: "agent", target: "run", target_port: "agent" },
    { edge_id: "t5", source: "run", source_port: "run", target: "score", target_port: "run" },
    { edge_id: "t6", source: "score", source_port: "scored", target: "card", target_port: "scored" },
    { edge_id: "t7", source: "card", source_port: "scorecard", target: "rpt", target_port: "scorecard" },
  ],
};

const slug = (s: string) =>
  s.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "") || "workflow";

export function EditorPage() {
  const store = useFlowStore();
  const [problems, setProblems] = useState<string[]>([]);
  const [workflows, setWorkflows] = useState<any[]>([]);
  const [results, setResults] = useState<any | null>(null);
  useExecutionEvents(store.exec.executionId);

  // fetch the joined scoreboard once the run reaches a terminal state
  useEffect(() => {
    const terminal = ["succeeded", "failed", "cancelled"];
    if (store.exec.executionId && terminal.includes(store.exec.status)) {
      api.executionResults(store.exec.executionId).then(setResults)
        .catch(() => setResults(null));
      store.select(null); // surface the results panel
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [store.exec.status, store.exec.executionId]);

  const load = (doc: any) => {
    const { nodes, edges } = fromWorkflowDoc(doc);
    store.setWorkflowMeta(doc.workflow_id, doc.name);
    store.setGraph(nodes, edges);
    store.setExec(emptyExec());
    store.select(null);
    store.markDirty(false);
    setProblems([]);
    setResults(null);
  };

  const openWorkflow = async (id: string) =>
    load((await api.getWorkflow(id)).workflow);

  useEffect(() => {
    (async () => {
      store.setCatalog(await api.nodeTypes());
      const existing = await api.listWorkflows();
      setWorkflows(existing);
      load(existing.length
        ? (await api.getWorkflow(existing[0].workflow_id)).workflow
        : STARTER);
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const newWorkflow = () => {
    const name = window.prompt("Name for the new workflow:", "New benchmark");
    if (!name) return;
    let id = slug(name);
    if (workflows.some((w) => w.workflow_id === id)) id = `${id}-${Date.now() % 1000}`;
    load({ workflow_id: id, name, nodes: [], edges: [] });
  };

  const fileInput = useRef<HTMLInputElement>(null);

  const exportWorkflow = () => {
    const doc = toWorkflowDoc(store.workflowId, store.workflowName,
                              store.nodes, store.edges);
    const blob = new Blob([JSON.stringify(doc, null, 2)],
                          { type: "application/json" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `${doc.workflow_id}.workflow.json`;
    a.click();
    URL.revokeObjectURL(a.href);
  };

  const importWorkflow = async (file: File) => {
    let doc: any;
    try {
      doc = JSON.parse(await file.text());
    } catch {
      setProblems([`${file.name}: not valid JSON`]);
      return;
    }
    if (!doc?.workflow_id || !Array.isArray(doc.nodes) || !Array.isArray(doc.edges)) {
      setProblems([`${file.name}: not a workflow document ` +
                   "(needs workflow_id, nodes[], edges[])"]);
      return;
    }
    if (workflows.some((w) => w.workflow_id === doc.workflow_id)) {
      doc.workflow_id = `${doc.workflow_id}-imported-${Date.now() % 1000}`;
    }
    load(doc);
    store.markDirty(true); // imported, not yet saved — Save persists it
    try { // server-side validation without persisting
      setProblems((await api.saveWorkflow(doc, true)).problems);
    } catch (e: any) {
      setProblems([String(e.message ?? e)]);
    }
  };

  const deleteWorkflow = async () => {
    if (!window.confirm(`Delete workflow "${store.workflowName}"? Past ` +
                        "executions keep their frozen snapshots.")) return;
    await api.deleteWorkflow(store.workflowId);
    const existing = await api.listWorkflows();
    setWorkflows(existing);
    load(existing.length
      ? (await api.getWorkflow(existing[0].workflow_id)).workflow
      : STARTER);
  };

  const save = async () => {
    const doc = toWorkflowDoc(store.workflowId, store.workflowName,
                              store.nodes, store.edges);
    const r = await api.saveWorkflow(doc);
    setProblems(r.problems);
    store.markDirty(false);
    setWorkflows(await api.listWorkflows());
    return r.problems;
  };

  const run = async () => {
    const probs = await save();
    if (probs.length) return;
    store.setExec(emptyExec());
    setResults(null);
    try {
      const { execution_id } = await api.startExecution(store.workflowId);
      store.setExec({ ...emptyExec(), executionId: execution_id, status: "running" });
    } catch (e: any) {
      setProblems([String(e.message ?? e)]);
    }
  };

  const running = ["running", "waiting_approval"].includes(store.exec.status);

  return (
    <div className="page">
      <div className="topbar">
        <select
          value={workflows.some((w) => w.workflow_id === store.workflowId)
            ? store.workflowId : ""}
          onChange={(e) => e.target.value && openWorkflow(e.target.value)}
          style={{ background: "var(--panel-2)", color: "var(--text)",
                   border: "1px solid var(--border)", borderRadius: 7,
                   padding: "5px 8px", maxWidth: 180 }}>
          {!workflows.some((w) => w.workflow_id === store.workflowId) && (
            <option value="">(unsaved) {store.workflowId}</option>
          )}
          {workflows.map((w) => (
            <option key={w.workflow_id} value={w.workflow_id}>
              {w.name} · {w.n_nodes} nodes
            </option>
          ))}
        </select>
        <button onClick={newWorkflow} title="New workflow">＋</button>
        <button title="Insert the full business-workflow template (doc → generate → gate → run → report)"
                onClick={() => {
                  let id = FULL_TEMPLATE.workflow_id;
                  if (workflows.some((w) => w.workflow_id === id))
                    id = `${id}-${Date.now() % 1000}`;
                  load({ ...FULL_TEMPLATE, workflow_id: id });
                  store.markDirty(true);
                }}>⊞ Template</button>
        <button onClick={deleteWorkflow} title="Delete workflow">🗑</button>
        <button onClick={exportWorkflow}
                title="Export this workflow as JSON">⤓</button>
        <button onClick={() => fileInput.current?.click()}
                title="Import a workflow JSON file">⤒</button>
        <input ref={fileInput} type="file" accept=".json,application/json"
               style={{ display: "none" }}
               onChange={(e) => {
                 const f = e.target.files?.[0];
                 if (f) importWorkflow(f);
                 e.target.value = "";
               }} />
        <input className="wfname" value={store.workflowName}
               onChange={(e) => {
                 store.setWorkflowMeta(store.workflowId, e.target.value);
                 store.markDirty(true);
               }} />
        {store.dirty && <span style={{ color: "var(--muted)" }}>●</span>}
        <span className="spacer" />
        {problems.length > 0 && (
          <span style={{ color: "var(--fail)", fontSize: 12 }}
                title={problems.join("\n")}>
            {problems.length} problem{problems.length > 1 ? "s" : ""}
          </span>
        )}
        {store.exec.status !== "idle" && (
          <span className={`status-chip ${store.exec.status}`}>
            {store.exec.status.replace("_", " ")}
          </span>
        )}
        {store.exec.status === "waiting_approval" && store.exec.executionId && (
          <button className="approve"
                  onClick={() => api.approve(store.exec.executionId!)}>
            ✋ Approve
          </button>
        )}
        <button onClick={save}>Save</button>
        {running ? (
          <button onClick={() => store.exec.executionId &&
                  api.cancel(store.exec.executionId)}>
            Stop
          </button>
        ) : (
          <button className="primary" onClick={run}>▶ Run</button>
        )}
      </div>
      <div className="editor-body">
        <Palette />
        <ReactFlowProvider>
          <Canvas />
        </ReactFlowProvider>
        <ConfigPanel results={results} />
      </div>
    </div>
  );
}
