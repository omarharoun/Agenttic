import {
  Background,
  BackgroundVariant,
  Connection,
  Controls,
  MiniMap,
  ReactFlow,
  applyEdgeChanges,
  applyNodeChanges,
  useReactFlow,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { useCallback } from "react";
import { useFlowStore } from "../store";
import { AgentticNode } from "./AgentticNode";

const nodeTypes = { agenttic: AgentticNode };

let counter = 1;
const freshId = (t: string) => `${t}_${counter++}_${Date.now() % 10000}`;

export function Canvas() {
  const { nodes, edges, catalog, setGraph, select, markDirty } = useFlowStore();
  const { screenToFlowPosition } = useReactFlow();

  // make the canvas mesh + background follow the active theme (true-black dark
  // / cream light) by reading the resolved Noor tokens
  const rootStyle = typeof window !== "undefined"
    ? getComputedStyle(document.documentElement) : null;
  const themeMode = (document.documentElement.getAttribute("data-theme") as
    "dark" | "light") || "dark";
  // fall back to the resolved token per-theme (never a dark-only default) so the
  // mesh stays correct even before styles are computed
  const dotColor = rootStyle?.getPropertyValue("--border-strong").trim()
    || (themeMode === "light" ? "#DBD6C8" : "#38353a");
  const bgColor = rootStyle?.getPropertyValue("--bg").trim()
    || (themeMode === "light" ? "#FAF9F5" : "#000000");

  const onNodesChange = useCallback(
    (changes: any) => {
      setGraph(applyNodeChanges(changes, useFlowStore.getState().nodes),
               useFlowStore.getState().edges);
      if (changes.some((c: any) => c.type !== "select" && c.type !== "dimensions"))
        markDirty(true);
    },
    [setGraph, markDirty],
  );

  const onEdgesChange = useCallback(
    (changes: any) => {
      setGraph(useFlowStore.getState().nodes,
               applyEdgeChanges(changes, useFlowStore.getState().edges));
      if (changes.some((c: any) => c.type !== "select")) markDirty(true);
    },
    [setGraph, markDirty],
  );

  const isValidConnection = useCallback(
    (conn: Connection | any) => {
      const s = useFlowStore.getState();
      const src = s.nodes.find((n) => n.id === conn.source);
      const tgt = s.nodes.find((n) => n.id === conn.target);
      if (!src || !tgt) return false;
      const srcSpec = s.catalog[(src.data as any).ntype];
      const tgtSpec = s.catalog[(tgt.data as any).ntype];
      const outKind = srcSpec?.outputs[conn.sourceHandle ?? ""];
      const inKind = tgtSpec?.inputs[conn.targetHandle ?? ""];
      return !!outKind && !!inKind && outKind === inKind; // typed ports
    },
    [],
  );

  const onConnect = useCallback(
    (conn: Connection) => {
      const s = useFlowStore.getState();
      setGraph(s.nodes, [
        ...s.edges,
        {
          id: `e_${conn.source}_${conn.sourceHandle}_${conn.target}_${Date.now() % 100000}`,
          source: conn.source!,
          sourceHandle: conn.sourceHandle,
          target: conn.target!,
          targetHandle: conn.targetHandle,
          animated: true,
        },
      ]);
      markDirty(true);
    },
    [setGraph, markDirty],
  );

  const onDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault();
      const ntype = e.dataTransfer.getData("application/agenttic-node");
      if (!ntype || !catalog[ntype]) return;
      const pos = screenToFlowPosition({ x: e.clientX, y: e.clientY });
      const s = useFlowStore.getState();
      setGraph(
        [...s.nodes, {
          id: freshId(ntype),
          type: "agenttic",
          position: pos,
          data: { ntype, label: "", config: {} },
        }],
        s.edges,
      );
      markDirty(true);
    },
    [catalog, screenToFlowPosition, setGraph, markDirty],
  );

  return (
    <div className="canvas-wrap"
         onDrop={onDrop} onDragOver={(e) => e.preventDefault()}>
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={nodeTypes}
        onNodesChange={onNodesChange}
        onEdgesChange={onEdgesChange}
        onConnect={onConnect}
        isValidConnection={isValidConnection}
        onNodeClick={(_, n) => select(n.id)}
        onPaneClick={() => select(null)}
        colorMode={themeMode}
        fitView
        deleteKeyCode={["Backspace", "Delete"]}
      >
        <Background variant={BackgroundVariant.Dots} gap={18} size={1.4}
                    color={dotColor} bgColor={bgColor} />
        <Controls />
        <MiniMap pannable zoomable />
      </ReactFlow>
    </div>
  );
}
