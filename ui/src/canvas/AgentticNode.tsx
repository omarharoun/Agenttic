import { Handle, Position } from "@xyflow/react";
import { useFlowStore } from "../store";

const CAT_COLOR: Record<string, string> = {
  input: "var(--cat-input)",
  benchmark: "var(--cat-benchmark)",
  agents: "var(--cat-agents)",
  evaluation: "var(--cat-evaluation)",
  delivery: "var(--cat-delivery)",
};

const STATE_ICON: Record<string, string> = {
  running: "⟳",
  waiting: "✋",
  succeeded: "✓",
  failed: "✕",
  skipped: "–",
};

export function AgentticNode({ id, data }: { id: string; data: any }) {
  const spec = useFlowStore((s) => s.catalog[data.ntype]);
  const liveState = useFlowStore((s) => s.exec.nodeStates[id] ?? "idle");
  const liveProgress = useFlowStore((s) => s.exec.progress[id]);
  // replay canvases embed final states in node data; editor uses live store
  const state = (data.runState as string) ?? liveState;
  const progress = data.runState ? undefined : liveProgress;
  if (!spec) return <div className="agenttic-node">{data.ntype}?</div>;

  const inPorts = Object.keys(spec.inputs);
  const outPorts = Object.keys(spec.outputs);
  const pct = progress && progress.total
    ? Math.round((progress.done / progress.total) * 100) : 0;

  return (
    <div className={`agenttic-node ${state}`}>
      {state !== "idle" && (
        <div className="state-badge" title={state}>{STATE_ICON[state]}</div>
      )}
      <div className="head">
        <span className="cat-dot"
              style={{ background: CAT_COLOR[spec.category] ?? "var(--muted)" }} />
        {data.label || spec.title}
      </div>
      <div className="ports">
        {progress && progress.total
          ? `${progress.done}/${progress.total} cases`
          : spec.description.slice(0, 46) + (spec.description.length > 46 ? "…" : "")}
      </div>
      {progress && progress.total ? (
        <div className="progress"><div style={{ width: `${pct}%` }} /></div>
      ) : null}
      {inPorts.map((p, i) => (
        <Handle key={p} id={p} type="target" position={Position.Left}
                style={{ top: 24 + i * 18 }} title={`${p}: ${spec.inputs[p]}`} />
      ))}
      {outPorts.map((p, i) => (
        <Handle key={p} id={p} type="source" position={Position.Right}
                style={{ top: 24 + i * 18 }} title={`${p}: ${spec.outputs[p]}`} />
      ))}
    </div>
  );
}
