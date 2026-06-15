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
                node("a", "slow_agent", seconds=0.4),
                node("b", "slow_agent", seconds=0.4),
            ], edges=[])
            t0 = asyncio.get_running_loop().time()
            eid = mgr.start(wf)
            await mgr._handles[eid].task
            elapsed = asyncio.get_running_loop().time() - t0
            # concurrent ~0.4s (+ thread-pool spin-up); serial would be >= 0.8s.
            # wide margin so the assertion proves concurrency without flaking.
            assert elapsed < 0.7
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


class TestUnapprovedSuiteHint:
    def test_run_suite_node_fails_with_canvas_hint(self, tmp_path):
        async def main():
            reg, store, mgr = make_manager(tmp_path)
            suite_id = load_pilot(reg, approved=False)
            eid = mgr.start(eval_workflow(suite_id))  # no gate wired
            await mgr._handles[eid].task
            ex = store.get_execution(eid)
            assert ex["status"] == "failed"
            failed = [e for e in store.events_after(eid)
                      if e["type"] == "node_failed"][0]
            msg = failed["data"]["error"]
            assert "Human Gate" in msg and "Resources" in msg
            assert "ascore approve" not in msg  # no CLI hint in the UI
        asyncio.run(main())


class TestResilience:
    def test_retry_then_succeed(self, tmp_path):
        async def main():
            reg, store, mgr = make_manager(tmp_path)
            attempts = {"n": 0}

            class FlakyConfig(BaseModel):
                fail_times: int = 0

            async def run_flaky(ctx, cfg, inputs):
                attempts["n"] += 1
                if attempts["n"] <= cfg.fail_times:
                    raise RuntimeError(f"transient {attempts['n']}")
                return {"agent": {"variant": "reference", "agent_id": "ok"}}

            NODE_TYPES["flaky"] = NodeSpec("flaky", "Flaky", "agents",
                FlakyConfig, {}, {"agent": "agent_ref"}, run_flaky)
            try:
                wf = Workflow(workflow_id="w", name="t", nodes=[
                    WorkflowNode(node_id="a", type="flaky",
                                 config={"fail_times": 2}, retries=2),
                ], edges=[])
                eid = mgr.start(wf)
                await mgr._handles[eid].task
                ex = store.get_execution(eid)
                assert ex["status"] == "succeeded"
                assert ex["node_states"]["a"] == "succeeded"
                retries = [e for e in store.events_after(eid)
                           if e["type"] == "node_retry"]
                assert len(retries) == 2
                assert retries[0]["data"]["attempt"] == 1
            finally:
                NODE_TYPES.pop("flaky", None)
        asyncio.run(main())

    def test_continue_on_error_completes_with_errors(self, tmp_path):
        async def main():
            reg, store, mgr = make_manager(tmp_path)
            # two independent branches; one fails but is marked continue_on_error
            wf = Workflow(workflow_id="w", name="t", nodes=[
                WorkflowNode(node_id="bad", type="slow_agent",
                             config={"seconds": 0.0, "fail": True},
                             continue_on_error=True),
                WorkflowNode(node_id="good", type="slow_agent",
                             config={"seconds": 0.0, "fail": False}),
            ], edges=[])
            eid = mgr.start(wf)
            await mgr._handles[eid].task
            ex = store.get_execution(eid)
            assert ex["status"] == "completed_with_errors"
            assert ex["node_states"] == {"bad": "failed", "good": "succeeded"}
            failed_evt = [e for e in store.events_after(eid)
                          if e["type"] == "node_failed"][0]
            assert failed_evt["data"]["continued"] is True
            final = [e for e in store.events_after(eid)
                     if e["type"] == "execution_completed_with_errors"][0]
            assert final["data"]["errored_nodes"] == ["bad"]
        asyncio.run(main())

    def test_continue_on_error_still_skips_starved_downstream(self, tmp_path):
        async def main():
            reg, store, mgr = make_manager(tmp_path)
            load_pilot(reg)
            # run_suite depends on the failed agent's output — it can't run,
            # but the execution is soft (completed_with_errors), not failed
            wf = Workflow(workflow_id="w", name="t", nodes=[
                WorkflowNode(node_id="a", type="slow_agent",
                             config={"seconds": 0.0, "fail": True},
                             continue_on_error=True),
                node("run", "run_suite", suite_id="pilot-support-triage"),
            ], edges=[edge("e", "a", "agent", "run", "agent")])
            eid = mgr.start(wf)
            await mgr._handles[eid].task
            ex = store.get_execution(eid)
            assert ex["status"] == "completed_with_errors"
            assert ex["node_states"] == {"a": "failed", "run": "skipped"}
        asyncio.run(main())

    def test_hard_failure_still_fails_the_run(self, tmp_path):
        async def main():
            reg, store, mgr = make_manager(tmp_path)
            wf = Workflow(workflow_id="w", name="t", nodes=[
                WorkflowNode(node_id="a", type="slow_agent",
                             config={"seconds": 0.0, "fail": True}),  # no flags
            ], edges=[])
            eid = mgr.start(wf)
            await mgr._handles[eid].task
            assert store.get_execution(eid)["status"] == "failed"
        asyncio.run(main())


class TestFiNode:
    def test_fi_eval_node_feeds_scorecard(self, tmp_path):
        async def main():
            reg, store, mgr = make_manager(tmp_path)
            suite_id = load_pilot(reg)
            from types import SimpleNamespace as NS
            def fake_fi(metric, **kw):
                # 'billing' outputs pass 'contains', everything else fails
                out = kw.get("output", "")
                ok = "billing" in out
                return NS(score=1.0 if ok else 0.0, passed=ok, reason=f"{metric}:{ok}")
            mgr.clients["fi"] = fake_fi

            wf = Workflow(workflow_id="wf-fi", name="fi", nodes=[
                node("agent", "agent", variant="reference", agent_id="ref-agent"),
                node("run", "run_suite", suite_id=suite_id),
                node("fi", "fi_eval", metrics=["contains"], threshold=0.5),
                node("card", "scorecard"),
            ], edges=[
                edge("e1", "agent", "agent", "run", "agent"),
                edge("e2", "run", "run", "fi", "run"),
                edge("e3", "fi", "scored", "card", "scored"),
            ])
            eid = mgr.start(wf)
            await mgr._handles[eid].task
            ex = store.get_execution(eid)
            assert ex["status"] == "succeeded"
            sc_ref = ex["node_outputs"]["card"]["scorecard"]
            sc = reg.get_scorecard(sc_ref["scorecard_id"])
            assert "contains" in sc.per_criterion_means
            assert all(c.scorer == "fi" for r in sc.run_scores
                       for c in r.criterion_scores)
            # synthetic fi rubric was persisted and pinned
            assert sc.rubric_id.startswith("fi::")
        asyncio.run(main())

    def test_fi_node_partial_batch_on_metric_error(self, tmp_path):
        async def main():
            reg, store, mgr = make_manager(tmp_path)
            suite_id = load_pilot(reg)
            from types import SimpleNamespace as NS
            calls = {"n": 0}
            def flaky_fi(metric, **kw):
                calls["n"] += 1
                if calls["n"] == 1:
                    raise RuntimeError("fi service 503")
                return NS(score=1.0, passed=True, reason="ok")
            mgr.clients["fi"] = flaky_fi
            wf = Workflow(workflow_id="wf-fi", name="fi", nodes=[
                node("agent", "agent", variant="reference", agent_id="ref-agent"),
                node("run", "run_suite", suite_id=suite_id),
                node("fi", "fi_eval", metrics=["contains"]),
                node("card", "scorecard"),
            ], edges=[
                edge("e1", "agent", "agent", "run", "agent"),
                edge("e2", "run", "run", "fi", "run"),
                edge("e3", "fi", "scored", "card", "scored"),
            ])
            eid = mgr.start(wf)
            await mgr._handles[eid].task
            ex = store.get_execution(eid)
            assert ex["status"] == "succeeded"  # one fi error didn't sink the run
            sc = reg.get_scorecard(ex["node_outputs"]["card"]["scorecard"]["scorecard_id"])
            assert len(sc.errored_test_ids) == 1
        asyncio.run(main())
