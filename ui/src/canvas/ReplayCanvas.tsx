import { Background, ReactFlow } from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { AgentticNode } from "./AgentticNode";

const nodeTypes = { agenttic: AgentticNode };

/** Read-only canvas of an execution's frozen workflow snapshot, each node
 * painted with its final run state. */
export function ReplayCanvas({ execution }: { execution: any }) {
  const wf = execution.workflow;
  const states: Record<string, string> = execution.node_states ?? {};
  const nodes = (wf?.nodes ?? []).map((n: any) => ({
    id: n.node_id,
    type: "agenttic",
    position: n.position ?? { x: 0, y: 0 },
    draggable: false,
    selectable: false,
    data: {
      ntype: n.type,
      label: n.label,
      config: n.config,
      runState: states[n.node_id] === "pending" ? "skipped"
        : states[n.node_id] ?? "idle",
    },
  }));
  const edges = (wf?.edges ?? []).map((e: any) => ({
    id: e.edge_id,
    source: e.source,
    sourceHandle: e.source_port,
    target: e.target,
    targetHandle: e.target_port,
  }));

  return (
    <div style={{ height: 320, border: "1px solid var(--border)",
                  borderRadius: 10, overflow: "hidden", marginTop: 14 }}>
      <ReactFlow nodes={nodes} edges={edges} nodeTypes={nodeTypes}
                 colorMode="dark" fitView nodesDraggable={false}
                 nodesConnectable={false} elementsSelectable={false}
                 zoomOnScroll={false} panOnDrag>
        <Background gap={18} size={1.2} />
      </ReactFlow>
    </div>
  );
}
