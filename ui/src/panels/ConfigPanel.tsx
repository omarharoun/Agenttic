import { useEffect, useState } from "react";
import { api } from "../api";
import { useFlowStore } from "../store";
import { ExecutionLog } from "./ExecutionLog";
import { ResultsPanel } from "./ResultsPanel";
import { SchemaForm } from "./SchemaForm";

const AGENT_FIELDS = ["agent_id", "variant", "model", "system_prompt", "url",
  "managed_agent_id", "environment_id", "cost_per_call_usd",
  "expected_input_tokens", "expected_output_tokens"] as const;

/** Dropdown of declared catalog agents. Picking one freezes its connection
 *  details into the node config (reproducible snapshot), then the form below
 *  stays fully editable for ad-hoc tweaks. */
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
          <option key={a.agent_id} value={a.agent_id}>
            {a.agent_id} ({a.variant})
          </option>
        ))}
      </select>
    </div>
  );
}

export function ConfigPanel({ results }: { results?: any }) {
  const { selectedNodeId, nodes, catalog, updateConfig, exec, setGraph,
          markDirty } = useFlowStore();
  const node = nodes.find((n) => n.id === selectedNodeId);
  const spec = node ? catalog[(node.data as any).ntype] : null;
  const nodeState = node ? exec.nodeStates[node.id] : undefined;
  const hasResults = results && (results.cases?.length || results.scorecards?.length);

  const setData = (nodeId: string, patch: Record<string, any>) => {
    const s = useFlowStore.getState();
    setGraph(s.nodes.map((n) => n.id === nodeId
      ? { ...n, data: { ...n.data, ...patch } } : n), s.edges);
    markDirty(true);
  };

  return (
    <div className="side-panel">
      <div className="panel-head">
        {spec ? spec.title : hasResults && !node ? "Results" : "Workflow"}
        {node && (
          <button onClick={() => {
            const s = useFlowStore.getState();
            setGraph(s.nodes.filter((n) => n.id !== node.id),
                     s.edges.filter((e) => e.source !== node.id && e.target !== node.id));
            markDirty(true);
          }}>delete</button>
        )}
      </div>
      <div className="panel-body">
        {!node && hasResults && <ResultsPanel results={results} />}
        {!node && !hasResults && (
          <p style={{ color: "var(--muted)" }}>
            Drag nodes from the palette onto the canvas, wire matching ports
            (kinds must agree), then hit <b>Run</b>. Select a node to
            configure it — results appear here when the run finishes.
          </p>
        )}
        {node && spec && (
          <>
            <p style={{ color: "var(--muted)", marginTop: 0 }}>{spec.description}</p>
            {nodeState === "waiting" && (
              <button className="approve" style={{ width: "100%", marginBottom: 10 }}
                      onClick={() => exec.executionId && api.approve(exec.executionId)}>
                ✋ Review done — approve suite
              </button>
            )}
            <label>label</label>
            <input
              value={(node.data as any).label ?? ""}
              onChange={(e) => setData(node.id, { label: e.target.value })}
            />
            {spec.type === "agent" && (
              <CatalogPicker onPick={(a) => updateConfig(node.id, {
                ...((node.data as any).config ?? {}),
                ...Object.fromEntries(
                  AGENT_FIELDS.map((k) => [k, a[k] ?? ""])),
              })} />
            )}
            <SchemaForm
              schema={spec.config_schema}
              value={(node.data as any).config ?? {}}
              onChange={(config) => updateConfig(node.id, config)}
            />
            <div className="policy-box">
              <div className="policy-title">resilience</div>
              <label>retries on failure</label>
              <input type="number" min={0}
                     value={(node.data as any).retries ?? 0}
                     onChange={(e) => setData(node.id,
                       { retries: Math.max(0, Number(e.target.value) || 0) })} />
              <label style={{ marginTop: 8 }}>
                <input type="checkbox" style={{ width: "auto", marginRight: 6 }}
                       checked={!!(node.data as any).continue_on_error}
                       onChange={(e) => setData(node.id,
                         { continue_on_error: e.target.checked })} />
                continue run if this node fails
              </label>
            </div>
          </>
        )}
      </div>
      {exec.executionId && <ExecutionLog />}
    </div>
  );
}
