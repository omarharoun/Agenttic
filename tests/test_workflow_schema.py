"""Workflow document validation: structure, ports, cycles, configs."""

from ascore.server.workflow_schema import (
    Workflow,
    WorkflowEdge,
    WorkflowNode,
    topo_levels,
    validate_workflow,
)


def node(nid, ntype, **config):
    return WorkflowNode(node_id=nid, type=ntype, config=config)


def edge(eid, src, tgt, sp="out", tp="in"):
    return WorkflowEdge(edge_id=eid, source=src, target=tgt,
                        source_port=sp, target_port=tp)


def pipeline_wf():
    return Workflow(workflow_id="wf1", name="t", nodes=[
        node("a", "agent", variant="reference", agent_id="x"),
        node("r", "run_suite", suite_id="s-1"),
        node("s", "score"),
    ], edges=[
        edge("e1", "a", "r", sp="agent", tp="agent"),
        edge("e2", "r", "s", sp="run", tp="run"),
    ])


class TestValidation:
    def test_valid_pipeline_round_trips(self):
        wf = pipeline_wf()
        assert validate_workflow(wf) == []
        again = Workflow.model_validate_json(wf.model_dump_json())
        assert again == wf

    def test_unknown_node_type(self):
        wf = Workflow(workflow_id="w", name="t",
                      nodes=[node("x", "teleport")], edges=[])
        assert any("unknown node type" in p for p in validate_workflow(wf))

    def test_invalid_config(self):
        wf = Workflow(workflow_id="w", name="t",
                      nodes=[node("g", "generator")], edges=[])  # missing suite_id
        assert any("invalid config" in p for p in validate_workflow(wf))

    def test_dangling_edge(self):
        wf = Workflow(workflow_id="w", name="t",
                      nodes=[node("a", "agent")],
                      edges=[edge("e", "a", "ghost", sp="agent", tp="agent")])
        assert any("dangling" in p for p in validate_workflow(wf))

    def test_port_kind_mismatch(self):
        wf = Workflow(workflow_id="w", name="t", nodes=[
            node("a", "agent"),
            node("s", "score"),
        ], edges=[edge("e", "a", "s", sp="agent", tp="run")])
        assert any("mismatch" in p for p in validate_workflow(wf))

    def test_cycle_detected(self):
        wf = Workflow(workflow_id="w", name="t", nodes=[
            node("g1", "human_gate"), node("g2", "human_gate"),
        ], edges=[
            edge("e1", "g1", "g2", sp="suite", tp="suite"),
            edge("e2", "g2", "g1", sp="suite", tp="suite"),
        ])
        assert topo_levels(wf) is None
        assert any("cycle" in p for p in validate_workflow(wf))

    def test_duplicate_node_ids(self):
        wf = Workflow(workflow_id="w", name="t",
                      nodes=[node("a", "agent"), node("a", "agent")], edges=[])
        assert any("duplicate" in p for p in validate_workflow(wf))


class TestTopoLevels:
    def test_independent_nodes_share_a_level(self):
        wf = Workflow(workflow_id="w", name="t", nodes=[
            node("a", "agent"), node("b", "agent"), node("r", "run_suite"),
        ], edges=[edge("e", "a", "r", sp="agent", tp="agent")])
        levels = topo_levels(wf)
        assert levels == [["a", "b"], ["r"]]
