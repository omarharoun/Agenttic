import { api } from "../api";
import { useFlowStore } from "../store";
import { ExecutionLog } from "./ExecutionLog";
import { ResultsPanel } from "./ResultsPanel";
import { SchemaForm } from "./SchemaForm";

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
