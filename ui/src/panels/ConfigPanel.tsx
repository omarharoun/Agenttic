import { api } from "../api";
import { useFlowStore } from "../store";
import { ExecutionLog } from "./ExecutionLog";
import { SchemaForm } from "./SchemaForm";

export function ConfigPanel() {
  const { selectedNodeId, nodes, catalog, updateConfig, exec, setGraph,
          markDirty } = useFlowStore();
  const node = nodes.find((n) => n.id === selectedNodeId);
  const spec = node ? catalog[(node.data as any).ntype] : null;
  const nodeState = node ? exec.nodeStates[node.id] : undefined;

  return (
    <div className="side-panel">
      <div className="panel-head">
        {spec ? spec.title : "Workflow"}
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
        {!node && (
          <p style={{ color: "var(--muted)" }}>
            Drag nodes from the palette onto the canvas, wire matching ports
            (kinds must agree), then hit <b>Run</b>. Select a node to
            configure it.
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
              onChange={(e) => {
                const s = useFlowStore.getState();
                setGraph(s.nodes.map((n) => n.id === node.id
                  ? { ...n, data: { ...n.data, label: e.target.value } } : n),
                  s.edges);
                markDirty(true);
              }}
            />
            <SchemaForm
              schema={spec.config_schema}
              value={(node.data as any).config ?? {}}
              onChange={(config) => updateConfig(node.id, config)}
            />
          </>
        )}
      </div>
      {exec.executionId && <ExecutionLog />}
    </div>
  );
}
