import type { Edge, Node } from "@xyflow/react";
import { useEffect, useState } from "react";
import { api } from "../api";
import { ExecutionLog } from "../panels/ExecutionLog";
import { ResultsPanel } from "../panels/ResultsPanel";
import { SchemaForm } from "../panels/SchemaForm";
import { useFlowStore } from "../store";
import { STEPS, type Template, TEMPLATES, buildEdges, stepById } from "./templates";

const AGENT_FIELDS = ["agent_id", "variant", "model", "system_prompt", "url",
  "managed_agent_id", "environment_id", "cost_per_call_usd",
  "expected_input_tokens", "expected_output_tokens"] as const;

/** Status of a step, derived from the live execution state. */
function stepStatus(state: string | undefined) {
  switch (state) {
    case "succeeded": return { cls: "done", label: "done" };
    case "running": return { cls: "running", label: "running" };
    case "waiting": return { cls: "waiting", label: "needs approval" };
    case "failed": return { cls: "failed", label: "failed" };
    case "skipped": return { cls: "", label: "skipped" };
    default: return { cls: "", label: "pending" };
  }
}

/** Pick from the declared catalog; freezes connection details into the node. */
function CatalogPicker({ onPick }: { onPick: (a: any) => void }) {
  const [agents, setAgents] = useState<any[]>([]);
  useEffect(() => {
    api.listCatalog().then((c) => setAgents(c.agents)).catch(() => setAgents([]));
  }, []);
  if (agents.length === 0) return null;
  return (
    <div>
      <label>declared agent <small>(prefills connection details)</small></label>
      <select defaultValue="" onChange={(e) => {
        const a = agents.find((x) => x.agent_id === e.target.value);
        if (a) onPick(a);
      }}>
        <option value="">— pick from catalog —</option>
        {agents.map((a) => (
          <option key={a.agent_id} value={a.agent_id}>{a.agent_id} ({a.variant})</option>
        ))}
      </select>
    </div>
  );
}

/** Rebuild react-flow edges deterministically from the current node set. */
function edgesFor(nodes: Node[]): Edge[] {
  const ids = new Set(nodes.map((n) => n.id));
  return buildEdges(ids).map((e) => ({
    id: e.edge_id, source: e.source, sourceHandle: e.source_port,
    target: e.target, targetHandle: e.target_port, animated: true,
  }));
}

function TemplatePicker({ onPick }: { onPick: (t: Template) => void }) {
  return (
    <div className="guided-inner">
      <div className="tpl-head">
        <div className="eyebrow">New benchmark</div>
        <h1>How do you want to start?</h1>
        <p>Pick a path. Each lays out the pipeline as guided steps you fill in.</p>
      </div>
      <div className="tpl-grid">
        {TEMPLATES.map((t) => (
          <button key={t.key} className="tpl-card" onClick={() => onPick(t)}>
            <div className="tpl-ico">{t.icon}</div>
            <h3>{t.name}</h3>
            <p>{t.tagline}</p>
            <div className="tpl-steps">
              {t.stepIds.map((id) => stepById(id).title).join("  →  ")}
            </div>
          </button>
        ))}
      </div>
    </div>
  );
}

/** One big step box: numeral, title, status, and an in-place config form. */
function StepCard({ node }: { node: Node }) {
  const { exec, catalog, updateConfig, setGraph, markDirty } = useFlowStore();
  const step = stepById(node.id);
  const data = node.data as any;
  const spec = catalog[data.ntype];
  const state = exec.nodeStates[node.id];
  const progress = exec.progress[node.id];
  const { cls, label } = stepStatus(state);
  const config = data.config ?? {};

  const isDoc = node.id === "business_doc";
  const empty = isDoc && !String(config.text ?? "").trim() && !config.file_path;
  const active = empty && exec.status === "idle";
  const pct = progress?.total ? Math.round((progress.done / progress.total) * 100) : 0;

  // scoring step: flip the node type between LLM judge and Future AGI metrics
  const setEngine = (fi: boolean) => {
    const s = useFlowStore.getState();
    setGraph(s.nodes.map((n) => n.id === "score"
      ? { ...n, data: { ...n.data, ntype: fi ? "fi_eval" : "score",
                        config: fi ? {} : { pass_threshold: 0.7 } } } : n), s.edges);
    markDirty(true);
  };

  return (
    <div className={`step-card ${cls} ${active ? "active" : ""}`}>
      <div className="step-head">
        <div className="step-num">{step.num}</div>
        <div className="step-title-wrap">
          <h3 className="step-title">
            <span style={{ color: "var(--accent)", fontFamily: "var(--font-ui)" }}>{step.icon}</span>
            {node.id === "score" ? (data.ntype === "fi_eval" ? "Future AGI evaluation" : step.title) : step.title}
            {step.optional && <span className="opt-tag">optional</span>}
          </h3>
          <p className="step-blurb">{empty ? (step.cta ?? step.blurb) : step.blurb}</p>
        </div>
        {state && <span className={`status-chip ${state === "waiting" ? "waiting_approval"
          : state === "succeeded" ? "succeeded" : state === "running" ? "running"
          : state === "failed" ? "failed" : ""}`}>{label}</span>}
      </div>

      <div className="step-body cfg">
        {progress?.total ? (
          <>
            <div className="step-progress"><div style={{ width: `${pct}%` }} /></div>
            <div className="step-progress-label">{progress.done}/{progress.total} cases</div>
          </>
        ) : null}

        {state === "waiting" && exec.executionId && (
          <button className="approve" style={{ marginBottom: 12 }}
                  onClick={() => api.approve(exec.executionId!)}>
            ✋ Review done — approve suite
          </button>
        )}

        {node.id === "score" && (
          <div className="engine-toggle">
            <div className="seg">
              <button className={data.ntype !== "fi_eval" ? "on" : ""}
                      onClick={() => setEngine(false)}>LLM judge</button>
              <button className={data.ntype === "fi_eval" ? "on" : ""}
                      onClick={() => setEngine(true)}>Future AGI</button>
            </div>
          </div>
        )}

        {node.id === "agent" && (
          <CatalogPicker onPick={(a) => updateConfig(node.id, {
            ...config,
            ...Object.fromEntries(AGENT_FIELDS.map((k) => [k, a[k] ?? ""])),
          })} />
        )}

        {spec ? (
          <SchemaForm schema={spec.config_schema} value={config}
                      onChange={(c) => updateConfig(node.id, c)} />
        ) : (
          <p style={{ color: "var(--muted)" }}>Loading configuration…</p>
        )}
      </div>
    </div>
  );
}

/** The guided, template-driven workflow surface. Replaces the free-form canvas
 *  as the primary editing experience. Renders a template picker when empty,
 *  otherwise the fixed pipeline as a vertical sequence of step boxes. */
export function GuidedFlow({ results, onPickTemplate }: {
  results: any | null;
  onPickTemplate: (t: Template) => void;
}) {
  const { nodes, exec, setGraph, markDirty, workflowName } = useFlowStore();

  if (nodes.length === 0) {
    return <div className="guided"><TemplatePicker onPick={onPickTemplate} /></div>;
  }

  // order the present nodes by their pipeline position; flag any unknown shape
  const present = STEPS.filter((s) => nodes.some((n) => n.id === s.id));
  const knownIds = new Set(STEPS.map((s) => s.id));
  const isGuided = nodes.every((n) => knownIds.has(n.id));
  const hasMonitor = nodes.some((n) => n.id === "monitor");

  const toggleMonitor = () => {
    const s = useFlowStore.getState();
    let next: Node[];
    if (hasMonitor) {
      next = s.nodes.filter((n) => n.id !== "monitor");
    } else {
      next = [...s.nodes, {
        id: "monitor", type: "ascore",
        position: { x: 40 + 9 * 210, y: 150 },
        data: { ntype: "monitor", label: "", config: { window: 50 } },
      }];
    }
    setGraph(next, edgesFor(next));
    markDirty(true);
  };

  return (
    <div className="guided">
      <div className="guided-inner">
        <div className="flow-banner">
          <div className="step-num" style={{ width: 40, height: 40 }}>⬡</div>
          <div style={{ flex: 1 }}>
            <h2>{workflowName}</h2>
            <p>Fill in each step top to bottom, then hit Run. Steps light up as the run flows through them.</p>
          </div>
        </div>

        {!isGuided && (
          <p style={{ color: "var(--wait)", marginBottom: 18 }}>
            ⚠ This workflow was built with custom nodes — switch to Advanced to edit its full graph.
          </p>
        )}

        <div className="steps-flow">
          {present.map((s) => (
            <StepCard key={s.id} node={nodes.find((n) => n.id === s.id)!} />
          ))}
        </div>

        <div className="opt-row">
          <button className={`opt-chip ${hasMonitor ? "on" : ""}`} onClick={toggleMonitor}>
            {hasMonitor ? "✓ " : "+ "}Live monitor
          </button>
        </div>

        {results && (results.cases?.length || results.scorecards?.length) ? (
          <div style={{ marginTop: 10 }}>
            <div className="eyebrow" style={{ marginBottom: 10 }}>Results</div>
            <ResultsPanel results={results} />
          </div>
        ) : null}

        {exec.executionId && (
          <div style={{ marginTop: 18 }}>
            <div className="eyebrow" style={{ marginBottom: 6 }}>Execution log</div>
            <div style={{ border: "1px solid var(--border)", borderRadius: "var(--r)",
                          overflow: "hidden", background: "var(--panel)" }}>
              <ExecutionLog />
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
