import type { Node } from "@xyflow/react";
import { useEffect, useState } from "react";
import { api } from "../api";
import { ExecutionLog } from "../panels/ExecutionLog";
import { ResultsPanel } from "../panels/ResultsPanel";
import { useFlowStore } from "../store";
import { STEPS, type Template, TEMPLATES, isConfigurable, stepById } from "./templates";

const AGENT_FIELDS = ["agent_id", "variant", "model", "system_prompt", "url",
  "managed_agent_id", "environment_id", "cost_per_call_usd",
  "expected_input_tokens", "expected_output_tokens"] as const;

function stepStatus(state: string | undefined) {
  switch (state) {
    case "succeeded": return { cls: "done", label: "done", chip: "succeeded" };
    case "running": return { cls: "running", label: "running", chip: "running" };
    case "waiting": return { cls: "waiting", label: "needs approval", chip: "waiting_approval" };
    case "failed": return { cls: "failed", label: "failed", chip: "failed" };
    case "skipped": return { cls: "", label: "skipped", chip: "" };
    default: return { cls: "", label: "pending", chip: "" };
  }
}

/** Pick from the declared catalog; freezes connection details into the node. */
function CatalogPicker({ config, onPick }: { config: any; onPick: (a: any) => void }) {
  const [agents, setAgents] = useState<any[]>([]);
  useEffect(() => {
    // Managed (Anthropic-hosted) agents aren't selectable in the guided flow —
    // they need a deployed agent/environment the user doesn't have here, and
    // picking one would otherwise sneak variant="managed" into the config.
    api.listCatalog()
      .then((c) => setAgents((c.agents ?? []).filter((a: any) => a.variant !== "managed")))
      .catch(() => setAgents([]));
  }, []);
  if (agents.length === 0) return null;
  return (
    <div>
      <label>or pick a saved agent <small>(prefills the fields below)</small></label>
      <select value="" onChange={(e) => {
        const a = agents.find((x) => x.agent_id === e.target.value);
        if (a) onPick(a);
      }}>
        <option value="">— saved agents —</option>
        {agents.map((a) => (
          <option key={a.agent_id} value={a.agent_id}>{a.agent_id} ({a.variant})</option>
        ))}
      </select>
    </div>
  );
}

/** Plain-language, variant-aware configuration for the agent under test.
 *  Replaces the raw schema form: external API agents get their endpoint URL +
 *  optional auth header; built-in agents get task instructions. The hosted
 *  ("managed") variant is intentionally not offered here — see the report. */
function AgentConfigCard({ node }: { node: Node }) {
  const updateConfig = useFlowStore((s) => s.updateConfig);
  const config = (node.data as any).config ?? {};
  const variant = config.variant === "blackbox" ? "blackbox" : "reference";
  const set = (patch: Record<string, any>) => updateConfig(node.id, { ...config, ...patch });
  const auth = config.headers?.Authorization ?? "";

  return (
    <div>
      <label>What are you testing?</label>
      <div className="seg" style={{ marginBottom: 6 }}>
        <button className={variant === "blackbox" ? "on" : ""}
                onClick={() => set({ variant: "blackbox" })}>Your API agent</button>
        <button className={variant === "reference" ? "on" : ""}
                onClick={() => set({ variant: "reference" })}>Built-in test agent</button>
      </div>
      <p className="step-hint">
        {variant === "blackbox"
          ? "We call your agent at an HTTP endpoint you control."
          : "A built-in Anthropic-powered agent that follows the instructions you give it."}
      </p>

      <label>Agent name</label>
      <input value={config.agent_id ?? ""} placeholder="my-agent"
             onChange={(e) => set({ agent_id: e.target.value })} />

      {variant === "blackbox" ? (
        <>
          <label>Endpoint URL <small>(required)</small></label>
          <input value={config.url ?? ""} placeholder="https://api.yourcompany.com/agent"
                 onChange={(e) => set({ url: e.target.value })} />
          <label>Authorization header <small>(optional)</small></label>
          <input value={auth} placeholder="Bearer sk-…"
                 onChange={(e) => set({ headers: e.target.value ? { Authorization: e.target.value } : {} })} />
        </>
      ) : (
        <>
          <CatalogPicker config={config} onPick={(a) => set(
            Object.fromEntries(AGENT_FIELDS.map((k) => [k, a[k] ?? config[k] ?? ""])))} />
          <label>Task instructions <small>(what the agent should do)</small></label>
          <textarea value={config.system_prompt ?? ""}
                    onChange={(e) => set({ system_prompt: e.target.value })} />
          <label>Model <small>(optional override)</small></label>
          <input value={config.model ?? ""} placeholder="default"
                 onChange={(e) => set({ model: e.target.value })} />
        </>
      )}
    </div>
  );
}

/** Business Requirement input: paste text, or upload a document (pdf/docx/txt/
 *  md) that the server extracts to text the user can review/edit before
 *  generating tests. */
function BusinessDocCard({ node }: { node: Node }) {
  const updateConfig = useFlowStore((s) => s.updateConfig);
  const config = (node.data as any).config ?? {};
  const [fileName, setFileName] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const set = (patch: Record<string, any>) =>
    updateConfig(node.id, { ...config, ...patch });

  async function onFile(f: File | undefined) {
    if (!f) return;
    setBusy(true);
    setErr("");
    try {
      const r = await api.extractDocument(f);
      // populate the requirement text from the file; clear any stale file_path
      set({ text: r.text, file_path: "" });
      setFileName(`${r.filename} (${r.chars.toLocaleString()} chars)`);
    } catch {
      setErr("Couldn't read that file. Use a pdf, docx, txt, or md under 10 MB.");
      setFileName("");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div>
      <label>Business requirement <small>(paste, or upload a document)</small></label>
      <textarea value={config.text ?? ""} rows={6}
                placeholder="Describe what the agent should do and the rules it must follow…"
                onChange={(e) => set({ text: e.target.value })} />
      <label style={{ marginTop: 8 }}>
        Upload a document <small>(pdf, docx, txt, md)</small>
      </label>
      <input type="file" accept=".pdf,.docx,.txt,.md,.markdown,.text"
             disabled={busy}
             onChange={(e) => onFile(e.target.files?.[0])} />
      {busy && <p className="step-hint">Extracting text…</p>}
      {fileName && !busy && (
        <p className="step-hint">Loaded {fileName} — review or edit the text above before generating.</p>
      )}
      {err && <p className="step-hint" style={{ color: "var(--bad, #c0392b)" }}>{err}</p>}
    </div>
  );
}

function TemplatePicker({ onPick }: { onPick: (t: Template) => void }) {
  return (
    <div className="guided-inner">
      <div className="tpl-head">
        <div className="eyebrow">New safety test</div>
        <h1>What do you want to test?</h1>
        <p>Pick a starting point. Each lays out the steps as a simple, guided flow.</p>
      </div>
      <div className="tpl-grid">
        {TEMPLATES.map((t) => (
          <button key={t.key} className="tpl-card" onClick={() => onPick(t)}>
            <div className="tpl-ico">{t.icon}</div>
            <h3>{t.name}</h3>
            <p>{t.tagline}</p>
          </button>
        ))}
      </div>
    </div>
  );
}

/** One big, deliberately minimal step box. */
function StepCard({ node }: { node: Node }) {
  const { exec } = useFlowStore();
  const step = stepById(node.id);
  const data = node.data as any;
  const state = exec.nodeStates[node.id];
  const progress = exec.progress[node.id];
  const { cls, label, chip } = stepStatus(state);
  const config = data.config ?? {};

  const empty = node.id === "business_doc"
    && !String(config.text ?? "").trim() && !config.file_path;
  const active = empty && exec.status === "idle";
  const pct = progress?.total ? Math.round((progress.done / progress.total) * 100) : 0;

  return (
    <div className={`step-card ${cls} ${active ? "active" : ""}`}>
      <div className="step-head">
        <div className="step-num">{step.num}</div>
        <div className="step-title-wrap">
          <h3 className="step-title">
            <span style={{ color: "var(--accent)" }}>{step.icon}</span>
            {step.title}
          </h3>
          <p className="step-blurb">{empty ? (step.cta ?? step.blurb) : step.blurb}</p>
        </div>
        {state && <span className={`status-chip ${chip}`}>{label}</span>}
      </div>

      <div className="step-body cfg">
        {progress?.total ? (
          <>
            <div className="step-progress"><div style={{ width: `${pct}%` }} /></div>
            <div className="step-progress-label">{progress.done}/{progress.total} cases</div>
          </>
        ) : null}

        {state === "waiting" && exec.executionId && (
          <button className="approve" style={{ marginBottom: 6 }}
                  onClick={() => api.approve(exec.executionId!)}>
            ✋ Approve these tests
          </button>
        )}

        {node.id === "agent" ? (
          <AgentConfigCard node={node} />
        ) : node.id === "business_doc" ? (
          <BusinessDocCard node={node} />
        ) : isConfigurable(node.id) ? null : (
          step.note && <p className="step-note">{step.note}</p>
        )}
      </div>
    </div>
  );
}

/** The guided, template-driven workflow surface. */
export function GuidedFlow({ results, onPickTemplate }: {
  results: any | null;
  onPickTemplate: (t: Template) => void;
}) {
  const { nodes, exec, workflowName } = useFlowStore();

  if (nodes.length === 0) {
    return <div className="guided"><TemplatePicker onPick={onPickTemplate} /></div>;
  }

  const present = STEPS.filter((s) => nodes.some((n) => n.id === s.id));
  const knownIds = new Set(STEPS.map((s) => s.id));
  const isGuided = nodes.every((n) => knownIds.has(n.id));

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
            ⚠ This workflow uses custom nodes — switch to Advanced to edit its full graph.
          </p>
        )}

        <div className="steps-flow">
          {present.map((s) => (
            <StepCard key={s.id} node={nodes.find((n) => n.id === s.id)!} />
          ))}
        </div>

        {results && (results.cases?.length || results.scorecards?.length) ? (
          <div style={{ marginTop: 10 }}>
            <div className="eyebrow" style={{ marginBottom: 10 }}>Results</div>
            <ResultsPanel results={results} />
          </div>
        ) : null}

        {exec.executionId && (
          <div style={{ marginTop: 18 }}>
            <div className="eyebrow" style={{ marginBottom: 6 }}>Activity</div>
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
