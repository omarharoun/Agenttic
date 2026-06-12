"""Workflow executor + execution manager: full pipeline run with fake
clients, live event ordering, gate pause/approve/resume across a simulated
restart, failure propagation (skip), cancellation, and parallel levels.
"""

import asyncio
import json
from pathlib import Path

import pytest
from pydantic import BaseModel

from ascore.registry.sqlite_store import Registry
from ascore.schema.rubric import Rubric
from ascore.schema.testcase import TestCase, TestSuite
from ascore.server.events import EventBus
from ascore.server.executor import ExecutionManager, WorkflowValidationError
from ascore.server.nodes import NODE_TYPES, NodeSpec
from ascore.server.store import UIStore
from ascore.server.workflow_schema import Workflow, WorkflowEdge, WorkflowNode
from tests.test_e2e_pipeline import ProfessionalToneJudgeClient, RoutingFakeClient

PILOT = Path(__file__).parent.parent / "examples" / "pilot_support_triage"

CFG = {
    "models": {"agent_default": "agent-model", "judge_strong": "judge-model",
               "judge_light": "judge-light"},
    "harness": {"timeout_seconds": 10, "max_parallel": 5,
                "transport_retries": 1, "max_steps": 10},
    "scoring": {"calibration_threshold": 0.8},
    "paths": {"review_dir": "review/"},
    "live": {"drift_threshold": 0.15},
}


def node(nid, ntype, **config):
    return WorkflowNode(node_id=nid, type=ntype, config=config)


def edge(eid, src, sp, tgt, tp):
    return WorkflowEdge(edge_id=eid, source=src, source_port=sp,
                        target=tgt, target_port=tp)


def load_pilot(reg: Registry, approved: bool = True) -> str:
    reg.save_rubric(Rubric.model_validate_json((PILOT / "rubric.json").read_text()))
    suite = TestSuite.model_validate_json((PILOT / "suite.json").read_text())
    cases = [TestCase.model_validate(c)
             for c in json.loads((PILOT / "cases.json").read_text())]
    reg.save_suite(suite, cases)
    if approved:
        reg.approve_suite(suite.suite_id, suite.version)
    return suite.suite_id


def eval_workflow(suite_id: str, with_gate: bool = False) -> Workflow:
    nodes = [
        node("agent", "agent", variant="reference", agent_id="ref-agent"),
        node("run", "run_suite", suite_id="" if with_gate else suite_id),
        node("score", "score"),
        node("card", "scorecard"),
        node("rpt", "report"),
    ]
    edges = [
        edge("e1", "agent", "agent", "run", "agent"),
        edge("e2", "run", "run", "score", "run"),
        edge("e3", "score", "scored", "card", "scored"),
        edge("e4", "card", "scorecard", "rpt", "scorecard"),
    ]
    if with_gate:
        nodes += [node("src", "const_suite", value={"suite_id": suite_id,
                                                    "version": 1,
                                                    "approved": False}),
                  node("gate", "human_gate")]
        edges += [edge("e5", "src", "suite", "gate", "suite"),
                  edge("e6", "gate", "suite", "run", "suite")]
    return Workflow(workflow_id="wf-eval", name="eval", nodes=nodes, edges=edges)


@pytest.fixture(autouse=True)
def fake_nodes():
    """Register helper node types for tests; cleaned up afterwards."""

    class ConstConfig(BaseModel):
        value: dict = {}

    async def run_const(ctx, cfg, inputs):
        return {"suite": cfg.value}

    class SlowConfig(BaseModel):
        seconds: float = 0.2
        fail: bool = False

    async def run_slow(ctx, cfg, inputs):
        await asyncio.sleep(cfg.seconds)
        if cfg.fail:
            raise RuntimeError("scripted failure")
        return {"agent": {"variant": "reference", "agent_id": "slow"}}

    NODE_TYPES["const_suite"] = NodeSpec(
        "const_suite", "Const Suite", "input", ConstConfig,
        {}, {"suite": "suite_ref"}, run_const)
    NODE_TYPES["slow_agent"] = NodeSpec(
        "slow_agent", "Slow", "agents", SlowConfig,
        {}, {"agent": "agent_ref"}, run_slow)
    yield
    NODE_TYPES.pop("const_suite", None)
    NODE_TYPES.pop("slow_agent", None)


def make_manager(tmp_path, *, db="exec.db"):
    reg = Registry(tmp_path / db)
    store = UIStore(reg.engine)
    bus = EventBus(store)
    mgr = ExecutionManager(CFG, reg, store, bus, clients={
        "agent": RoutingFakeClient(), "judge": ProfessionalToneJudgeClient()})
    return reg, store, mgr


async def wait_status(store, eid, status, timeout=5.0):
    for _ in range(int(timeout / 0.02)):
        if store.get_execution(eid)["status"] == status:
            return
        await asyncio.sleep(0.02)
    raise AssertionError(
        f"never reached {status}; now {store.get_execution(eid)['status']}")


class TestFullPipeline:
    def test_pipeline_executes_with_live_events(self, tmp_path):
        async def main():
            reg, store, mgr = make_manager(tmp_path)
            eid = mgr.start(eval_workflow(load_pilot(reg)))
            await mgr._handles[eid].task
            ex = store.get_execution(eid)
            assert ex["status"] == "succeeded"
            assert set(ex["node_states"].values()) == {"succeeded"}
            md = ex["node_outputs"]["rpt"]["markdown"]
            assert "Executive summary" in md and "80%" in md
            assert ex["node_outputs"]["card"]["scorecard"]["task_success_rate"] \
                == pytest.approx(0.8)
            events = store.events_after(eid)
            types = [e["type"] for e in events]
            assert types[0] == "execution_started"
            assert types[-1] == "execution_succeeded"
            progress = [e for e in events if e["type"] == "node_progress"
                        and e["node_id"] == "run"]
            assert len(progress) == 20  # 10 case_started + 10 case_finished
            scored = [e for e in events if e["type"] == "node_progress"
                      and e["node_id"] == "score"]
            assert len(scored) == 10
        asyncio.run(main())

    def test_invalid_workflow_refuses_to_start(self, tmp_path):
        async def main():
            reg, store, mgr = make_manager(tmp_path)
            bad = Workflow(workflow_id="w", name="t",
                           nodes=[node("x", "nope")], edges=[])
            with pytest.raises(WorkflowValidationError):
                mgr.start(bad)
        asyncio.run(main())


class TestHumanGate:
    def test_gate_pauses_then_approval_resumes(self, tmp_path):
        async def main():
            reg, store, mgr = make_manager(tmp_path)
            suite_id = load_pilot(reg, approved=False)
            eid = mgr.start(eval_workflow(suite_id, with_gate=True))
            await wait_status(store, eid, "waiting_approval")
            ex = store.get_execution(eid)
            assert ex["node_states"]["gate"] == "waiting"
            waiting = [e for e in store.events_after(eid)
                       if e["type"] == "node_waiting"][0]
            # the approve API does exactly this:
            reg.approve_suite(waiting["data"]["suite_id"],
                              waiting["data"]["version"])
            assert mgr.approve_gate(eid) is True
            await mgr._handles[eid].task
            assert store.get_execution(eid)["status"] == "succeeded"
        asyncio.run(main())

    def test_gate_survives_restart_via_resume(self, tmp_path):
        async def main():
            reg, store, mgr = make_manager(tmp_path)
            suite_id = load_pilot(reg, approved=False)
            eid = mgr.start(eval_workflow(suite_id, with_gate=True))
            await wait_status(store, eid, "waiting_approval")
            # simulate a process crash: kill the task without finalizing
            mgr._handles[eid].task.cancel()
            await asyncio.sleep(0.05)
            assert store.get_execution(eid)["status"] == "waiting_approval"

            # "new process": approve, fresh manager, resume seeded
            reg.approve_suite(suite_id, 1)
            _, store2, mgr2 = make_manager(tmp_path)  # same db file
            mgr2.reg = reg
            mgr2.resume(eid)
            await mgr2._handles[eid].task
            ex = store2.get_execution(eid)
            assert ex["status"] == "succeeded"
            assert "Executive summary" in ex["node_outputs"]["rpt"]["markdown"]
        asyncio.run(main())


class TestFailureAndCancel:
    def test_failure_skips_downstream(self, tmp_path):
        async def main():
            reg, store, mgr = make_manager(tmp_path)
            load_pilot(reg)
            wf = Workflow(workflow_id="w", name="t", nodes=[
                node("a", "slow_agent", seconds=0.0, fail=True),
                node("run", "run_suite", suite_id="pilot-support-triage"),
                node("score", "score"),
            ], edges=[
                edge("e1", "a", "agent", "run", "agent"),
                edge("e2", "run", "run", "score", "run"),
            ])
            eid = mgr.start(wf)
            await mgr._handles[eid].task
            ex = store.get_execution(eid)
            assert ex["status"] == "failed"
            assert ex["node_states"] == {"a": "failed", "run": "skipped",
                                         "score": "skipped"}
            failed_evt = [e for e in store.events_after(eid)
                          if e["type"] == "node_failed"][0]
            assert "scripted failure" in failed_evt["data"]["error"]
        asyncio.run(main())

    def test_cancel_mid_run(self, tmp_path):
        async def main():
            reg, store, mgr = make_manager(tmp_path)
            wf = Workflow(workflow_id="w", name="t",
                          nodes=[node("a", "slow_agent", seconds=30)], edges=[])
            eid = mgr.start(wf)
            await asyncio.sleep(0.1)
            await mgr.cancel(eid)
            assert store.get_execution(eid)["status"] == "cancelled"
        asyncio.run(main())

    def test_parallel_level_runs_concurrently(self, tmp_path):
        async def main():
            reg, store, mgr = make_manager(tmp_path)
            wf = Workflow(workflow_id="w", name="t", nodes=[
                node("a", "slow_agent", seconds=0.15),
                node("b", "slow_agent", seconds=0.15),
            ], edges=[])
            t0 = asyncio.get_running_loop().time()
            eid = mgr.start(wf)
            await mgr._handles[eid].task
            elapsed = asyncio.get_running_loop().time() - t0
            assert elapsed < 0.28  # serial would be >= 0.3
            types = [e["type"] for e in store.events_after(eid)]
            assert types.count("node_started") == 2
        asyncio.run(main())


class TestEventBusReplay:
    def test_subscribe_replays_then_terminates_for_finished(self, tmp_path):
        async def main():
            reg, store, mgr = make_manager(tmp_path)
            eid = mgr.start(eval_workflow(load_pilot(reg)))
            await mgr._handles[eid].task
            bus2 = EventBus(store)  # fresh bus, replay-only
            seen = [e async for e in bus2.subscribe(eid)]
            assert seen[0]["type"] == "execution_started"
            assert seen[-1]["type"] == "execution_succeeded"
            assert [e["seq"] for e in seen] == sorted(e["seq"] for e in seen)
            # resume-from: replay only events after a checkpoint
            tail = [e async for e in bus2.subscribe(eid, after=seen[-3]["seq"])]
            assert len(tail) == 2
        asyncio.run(main())
