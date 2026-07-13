"""Workflow document: the canvas graph the UI edits and the executor runs.

Deliberately NOT in ascore/schema/ — that package is the spec'd trace
contract. Workflows are UI-layer state: mutable drafts whose reproducibility
comes from the immutable ``workflow_snapshot`` frozen into every execution
(mirroring how scorecards pin suite/rubric versions).
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class WorkflowNode(BaseModel):
    node_id: str
    type: str                       # validated against NODE_TYPES at save time
    label: str = ""
    position: dict = Field(default_factory=lambda: {"x": 0, "y": 0})
    config: dict = Field(default_factory=dict)
    # execution-resilience policy (not node-type config):
    retries: int = Field(default=0, ge=0)   # re-attempt on failure before giving up
    continue_on_error: bool = False         # failure here doesn't abort the run


class WorkflowEdge(BaseModel):
    edge_id: str
    source: str
    source_port: str = "out"
    target: str
    target_port: str = "in"


class Workflow(BaseModel):
    workflow_id: str
    name: str
    nodes: list[WorkflowNode] = Field(default_factory=list)
    edges: list[WorkflowEdge] = Field(default_factory=list)


def validate_workflow(wf: Workflow) -> list[str]:
    """Structural validation; returns a list of problems (empty = valid).

    Checks: unknown node types, per-node config validity, dangling edges,
    port-kind mismatches, duplicate node ids, cycles (Kahn).
    """
    from agenttic.server.nodes import NODE_TYPES

    problems: list[str] = []
    ids = [n.node_id for n in wf.nodes]
    if len(ids) != len(set(ids)):
        problems.append("duplicate node_id values")
    by_id = {n.node_id: n for n in wf.nodes}

    for n in wf.nodes:
        spec = NODE_TYPES.get(n.type)
        if spec is None:
            problems.append(f"{n.node_id}: unknown node type {n.type!r}")
            continue
        try:
            spec.config_model.model_validate(n.config)
        except Exception as exc:  # noqa: BLE001 — collect, don't raise
            problems.append(f"{n.node_id}: invalid config: {exc}")

    for e in wf.edges:
        src, tgt = by_id.get(e.source), by_id.get(e.target)
        if src is None or tgt is None:
            problems.append(f"{e.edge_id}: dangling edge {e.source}->{e.target}")
            continue
        src_spec, tgt_spec = NODE_TYPES.get(src.type), NODE_TYPES.get(tgt.type)
        if src_spec is None or tgt_spec is None:
            continue  # already reported above
        out_kind = src_spec.outputs.get(e.source_port)
        in_kind = tgt_spec.inputs.get(e.target_port)
        if out_kind is None:
            problems.append(f"{e.edge_id}: {src.type} has no output port "
                            f"{e.source_port!r}")
        if in_kind is None:
            problems.append(f"{e.edge_id}: {tgt.type} has no input port "
                            f"{e.target_port!r}")
        if out_kind and in_kind and out_kind != in_kind:
            problems.append(f"{e.edge_id}: port kind mismatch "
                            f"{out_kind!r} -> {in_kind!r}")

    if topo_levels(wf) is None:
        problems.append("workflow contains a cycle")
    return problems


def topo_levels(wf: Workflow) -> list[list[str]] | None:
    """Kahn's algorithm grouped into levels (nodes per level are independent
    and may run concurrently). Returns None if the graph has a cycle."""
    indeg = {n.node_id: 0 for n in wf.nodes}
    out: dict[str, list[str]] = {n.node_id: [] for n in wf.nodes}
    for e in wf.edges:
        if e.source in indeg and e.target in indeg:
            indeg[e.target] += 1
            out[e.source].append(e.target)
    levels: list[list[str]] = []
    ready = sorted(nid for nid, d in indeg.items() if d == 0)
    seen = 0
    while ready:
        levels.append(ready)
        seen += len(ready)
        nxt: list[str] = []
        for nid in ready:
            for child in out[nid]:
                indeg[child] -= 1
                if indeg[child] == 0:
                    nxt.append(child)
        ready = sorted(nxt)
    return levels if seen == len(indeg) else None
