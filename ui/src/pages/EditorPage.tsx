import { ReactFlowProvider } from "@xyflow/react";
import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import { Canvas } from "../canvas/Canvas";
import { ConfigPanel } from "../panels/ConfigPanel";
import { Palette } from "../panels/Palette";
import { ensureNotifyPermission } from "../notify";
import {
  emptyExec,
  fromWorkflowDoc,
  toWorkflowDoc,
  useFlowStore,
} from "../store";
import { GuidedFlow } from "../workflow/GuidedFlow";
import { buildDoc, type Template } from "../workflow/templates";

/** Free-form starter graph, kept for the advanced canvas. */
const STARTER = {
  workflow_id: "my-workflow",
  name: "Benchmark pipeline",
  nodes: [
    { node_id: "agent", type: "agent", label: "", position: { x: 40, y: 230 },
      config: {
        variant: "reference", agent_id: "agent-under-test",
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

const slug = (s: string) =>
  s.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "") || "workflow";

const EMPTY = { workflow_id: "new-benchmark", name: "New benchmark", nodes: [], edges: [] };

type Mode = "guided" | "advanced";
const getMode = (): Mode =>
  (localStorage.getItem("ascore_editor_mode") as Mode) === "advanced" ? "advanced" : "guided";

export function EditorPage() {
  const store = useFlowStore();
  const [mode, setMode] = useState<Mode>(getMode);
  const [problems, setProblems] = useState<string[]>([]);
  const [workflows, setWorkflows] = useState<any[]>([]);
  const [results, setResults] = useState<any | null>(null);
  const [estimate, setEstimate] = useState<any | null>(null);

  // Reconnect to a run that's still executing server-side for this workflow —
  // leaving the page never loses it; the run is owned by the server and we just
  // re-subscribe. SSE replay (after=0) rebuilds the progress on return.
  const reconnect = async (workflowId: string) => {
    try {
      const rows = await api.listExecutions(workflowId);
      const active = rows.find((r) =>
        ["running", "waiting_approval"].includes(r.status));
      if (active)
        store.setExec({ ...emptyExec(), executionId: active.execution_id,
                        status: active.status });
    } catch { /* no active run */ }
  };

  const setEditorMode = (m: Mode) => {
    setMode(m);
    localStorage.setItem("ascore_editor_mode", m);
  };

  const refreshEstimate = (id: string) =>
    api.estimateWorkflow(id).then(setEstimate).catch(() => setEstimate(null));

  useEffect(() => {
    const terminal = ["succeeded", "failed", "cancelled", "completed_with_errors"];
    if (store.exec.executionId && terminal.includes(store.exec.status)) {
      api.executionResults(store.exec.executionId).then(setResults)
        .catch(() => setResults(null));
      store.select(null);
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

  const openWorkflow = async (id: string) => {
    load((await api.getWorkflow(id)).workflow);
    refreshEstimate(id);
    reconnect(id);
  };

  useEffect(() => {
    (async () => {
      store.setCatalog(await api.nodeTypes());
      const existing = await api.listWorkflows();
      setWorkflows(existing);
      // existing work resumes; otherwise the guided picker / empty canvas
      load(existing.length
        ? (await api.getWorkflow(existing[0].workflow_id)).workflow
        : EMPTY);
      if (existing.length) {
        refreshEstimate(existing[0].workflow_id);
        reconnect(existing[0].workflow_id);
      }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const uniqueId = (base: string) =>
    workflows.some((w) => w.workflow_id === base) ? `${base}-${Date.now() % 1000}` : base;

  // Guided: a template was chosen → materialise it into the store.
  const pickTemplate = (t: Template) => {
    const doc = buildDoc(t, uniqueId(`${t.key}-benchmark`), t.name);
    load(doc);
    store.markDirty(true);
  };

  const newWorkflow = () => {
    if (mode === "guided") { load({ ...EMPTY, workflow_id: uniqueId("new-benchmark") }); return; }
    const name = window.prompt("Name for the new workflow:", "New benchmark");
    if (!name) return;
    load({ workflow_id: uniqueId(slug(name)), name, nodes: [], edges: [] });
  };

  const fileInput = useRef<HTMLInputElement>(null);

  const exportWorkflow = () => {
    const doc = toWorkflowDoc(store.workflowId, store.workflowName, store.nodes, store.edges);
    const blob = new Blob([JSON.stringify(doc, null, 2)], { type: "application/json" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `${doc.workflow_id}.workflow.json`;
    a.click();
    URL.revokeObjectURL(a.href);
  };

  const importWorkflow = async (file: File) => {
    let doc: any;
    try { doc = JSON.parse(await file.text()); }
    catch { setProblems([`${file.name}: not valid JSON`]); return; }
    if (!doc?.workflow_id || !Array.isArray(doc.nodes) || !Array.isArray(doc.edges)) {
      setProblems([`${file.name}: not a workflow document (needs workflow_id, nodes[], edges[])`]);
      return;
    }
    if (workflows.some((w) => w.workflow_id === doc.workflow_id))
      doc.workflow_id = `${doc.workflow_id}-imported-${Date.now() % 1000}`;
    load(doc);
    store.markDirty(true);
    try { setProblems((await api.saveWorkflow(doc, true)).problems); }
    catch (e: any) { setProblems([String(e.message ?? e)]); }
  };

  const deleteWorkflow = async () => {
    if (!window.confirm(`Delete workflow "${store.workflowName}"? Past ` +
                        "executions keep their frozen snapshots.")) return;
    await api.deleteWorkflow(store.workflowId);
    const existing = await api.listWorkflows();
    setWorkflows(existing);
    load(existing.length
      ? (await api.getWorkflow(existing[0].workflow_id)).workflow
      : EMPTY);
  };

  const save = async () => {
    const doc = toWorkflowDoc(store.workflowId, store.workflowName, store.nodes, store.edges);
    const r = await api.saveWorkflow(doc);
    setProblems(r.problems);
    store.markDirty(false);
    setWorkflows(await api.listWorkflows());
    refreshEstimate(store.workflowId);
    return r.problems;
  };

  const run = async () => {
    ensureNotifyPermission();  // ask once, on the user gesture
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
  const hasNodes = store.nodes.length > 0;

  return (
    <div className="page">
      <div className="topbar">
        <select
          value={workflows.some((w) => w.workflow_id === store.workflowId) ? store.workflowId : ""}
          onChange={(e) => e.target.value && openWorkflow(e.target.value)}
          style={{ background: "var(--panel-2)", color: "var(--text)",
                   border: "1px solid var(--border)", borderRadius: 8,
                   padding: "6px 9px", maxWidth: 180 }}>
          {!workflows.some((w) => w.workflow_id === store.workflowId) && (
            <option value="">(unsaved) {store.workflowId}</option>
          )}
          {workflows.map((w) => (
            <option key={w.workflow_id} value={w.workflow_id}>{w.name} · {w.n_nodes} nodes</option>
          ))}
        </select>
        <button onClick={newWorkflow} title="New benchmark">＋</button>
        {mode === "guided" && hasNodes && (
          <button title="Choose a different template"
                  onClick={() => load({ ...EMPTY, workflow_id: uniqueId("new-benchmark") })}>
            ⊞ Templates
          </button>
        )}
        {mode === "advanced" && (
          <button title="Insert the starter benchmark graph"
                  onClick={() => { load({ ...STARTER, workflow_id: uniqueId(STARTER.workflow_id) }); store.markDirty(true); }}>
            ⊞ Starter
          </button>
        )}
        {mode === "advanced" && (
          <button onClick={exportWorkflow} title="Export this workflow as JSON">⤓</button>
        )}
        {mode === "advanced" && (
          <button onClick={() => fileInput.current?.click()} title="Import a workflow JSON file">⤒</button>
        )}
        <button onClick={deleteWorkflow} title="Delete workflow">🗑</button>
        <input ref={fileInput} type="file" accept=".json,application/json"
               style={{ display: "none" }}
               onChange={(e) => { const f = e.target.files?.[0]; if (f) importWorkflow(f); e.target.value = ""; }} />
        <input className="wfname" value={store.workflowName}
               onChange={(e) => { store.setWorkflowMeta(store.workflowId, e.target.value); store.markDirty(true); }} />
        {store.dirty && <span style={{ color: "var(--muted)" }}>●</span>}
        <span className="spacer" />

        <div className="seg" title="Editing mode">
          <button className={mode === "guided" ? "on" : ""} onClick={() => setEditorMode("guided")}>Guided</button>
          <button className={mode === "advanced" ? "on" : ""} onClick={() => setEditorMode("advanced")}>Advanced</button>
        </div>

        {store.exec.status !== "idle" && (
          <span className={`status-chip ${store.exec.status}`}>{store.exec.status.replace("_", " ")}</span>
        )}
        {store.exec.status === "waiting_approval" && store.exec.executionId && (
          <button className="approve" onClick={() => api.approve(store.exec.executionId!)}>✋ Approve</button>
        )}
        {estimate?.estimate && (() => {
          const e = estimate.estimate, b = estimate.budget || {};
          const over = b.would_exceed_run || b.would_exceed_daily;
          const note = e.notes?.length ? "\n" + e.notes.join("\n") : "";
          return (
            <span style={{ fontSize: 12, color: over ? "var(--fail)" : "var(--muted)" }}
                  title={`projected: agent $${e.projected_agent_usd}, judge ` +
                         `$${e.projected_judge_usd} over ${e.n_cases} cases` +
                         (over ? "\n⚠ exceeds budget cap" : "") + note}>
              ~${e.projected_usd.toFixed(4)}{over ? " ⚠ over budget" : ""}
            </span>
          );
        })()}
        <button onClick={save} disabled={!hasNodes}>Save</button>
        {running ? (
          <button onClick={() => store.exec.executionId && api.cancel(store.exec.executionId)}>Stop</button>
        ) : (
          <button className="primary" onClick={run} disabled={!hasNodes}>▶ Run</button>
        )}
      </div>

      {problems.length > 0 && <RunProblems problems={problems} onDismiss={() => setProblems([])} />}

      {mode === "guided" ? (
        <GuidedFlow results={results} onPickTemplate={pickTemplate} />
      ) : (
        <div className="editor-body">
          <Palette />
          <ReactFlowProvider>
            <Canvas />
          </ReactFlowProvider>
          <ConfigPanel results={results} />
        </div>
      )}
    </div>
  );
}

/** Visible, actionable error banner for run/validation failures — replaces the
 *  old bare "N problems" count. Surfaces the real backend message; when the run
 *  was blocked for a missing Anthropic key, links straight to Settings. */
function RunProblems({ problems, onDismiss }: { problems: string[]; onDismiss: () => void }) {
  const clean = (s: string) => s.replace(/^"+|"+$/g, "").replace(/^\d+\s*—?\s*/, "");
  const needsKey = problems.some((p) => /Anthropic API key/i.test(p));
  return (
    <div className="run-problems">
      <span className="rp-ico">⚠</span>
      <div className="rp-body">
        <div className="rp-title">{needsKey ? "Can't run yet" : `Couldn't run — ${problems.length} problem${problems.length > 1 ? "s" : ""}`}</div>
        <ul className="rp-list">
          {problems.map((p, i) => <li key={i}>{clean(p)}</li>)}
        </ul>
        {needsKey && (
          <Link className="rp-cta" to="/app/settings?section=api-keys">
            Add your Anthropic API key in Settings →
          </Link>
        )}
      </div>
      <button className="rp-x" onClick={onDismiss} title="Dismiss">✕</button>
    </div>
  );
}
