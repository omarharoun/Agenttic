"""Result cache: an identical run reuses the prior scorecard with ZERO agent or
judge calls; a changed input misses and re-runs; force bypasses; the cache is
per-tenant; and the history endpoint flags cached results."""

from __future__ import annotations

import asyncio

import pytest

from ascore.registry.sqlite_store import Registry, make_engine
from ascore.result_cache import scorecard_cache_key
from ascore.server.events import EventBus
from ascore.server.executor import ExecutionManager
from ascore.server.store import UIStore
from tests.test_e2e_pipeline import ProfessionalToneJudgeClient, RoutingFakeClient
from tests.test_executor import CFG, edge, eval_workflow, load_pilot, node
from ascore.server.workflow_schema import Workflow


# -- call-counting LLM clients --------------------------------------------

class _Counter:
    def __init__(self, inner):
        self.inner, self.calls = inner, 0

    def create(self, *a, **k):
        self.calls += 1
        return self.inner.create(*a, **k)


class CountingClient:
    """Wraps a fake client and counts .messages.create calls (LLM calls)."""
    def __init__(self, inner):
        self.messages = _Counter(inner.messages)

    @property
    def calls(self) -> int:
        return self.messages.calls


def _manager(tmp_path, db="cache.db"):
    reg = Registry(tmp_path / db)
    store = UIStore(reg.engine)
    bus = EventBus(store)
    agent = CountingClient(RoutingFakeClient())
    judge = CountingClient(ProfessionalToneJudgeClient())
    mgr = ExecutionManager(CFG, reg, store, bus,
                           clients={"agent": agent, "judge": judge})
    return reg, store, mgr, agent, judge


async def _run(mgr, store, wf, *, force=False):
    eid = mgr.start(wf, force=force)
    await mgr._handles[eid].task
    ex = store.get_execution(eid)
    assert ex["status"] == "succeeded", ex
    # the scorecard node output (cached flag + id)
    card = next(p for o in ex["node_outputs"].values() for p in o.values()
               if isinstance(p, dict) and "scorecard_id" in p)
    return card


# -- unit: cache key -------------------------------------------------------

def _key(**over):
    base = dict(agent_id="a", suite_id="s", suite_version=1,
                agent_config_hash="h", rubric_id="r", rubric_version=1,
                cfg={"models": {"judge_strong": "js", "judge_light": "jl"}})
    base.update(over)
    return scorecard_cache_key(**base)


class TestCacheKey:
    def test_identical_inputs_same_key(self):
        assert _key() == _key()

    def test_sensitive_to_each_input(self):
        base = _key()
        assert base != _key(agent_id="other-agent")
        assert base != _key(suite_version=2)
        assert base != _key(agent_config_hash="other")
        assert base != _key(rubric_version=2)
        assert base != _key(cfg={"models": {"judge_strong": "X", "judge_light": "jl"}})


# -- integration: zero-spend cache hit ------------------------------------

class TestResultCache:
    def test_identical_run_is_cache_hit_with_no_llm_calls(self, tmp_path):
        async def main():
            reg, store, mgr, agent, judge = _manager(tmp_path)
            wf = eval_workflow(load_pilot(reg))

            first = await _run(mgr, store, wf)
            assert first.get("cached") is False
            assert agent.calls > 0 and judge.calls > 0      # real work happened
            a0, j0, sid = agent.calls, judge.calls, first["scorecard_id"]

            second = await _run(mgr, store, wf)             # identical re-run
            assert second.get("cached") is True
            assert second["scorecard_id"] == sid            # same original result
            assert agent.calls == a0 and judge.calls == j0  # NO new LLM calls
        asyncio.run(main())

    def test_changed_agent_config_is_cache_miss(self, tmp_path):
        async def main():
            reg, store, mgr, agent, judge = _manager(tmp_path)
            suite_id = load_pilot(reg)
            await _run(mgr, store, eval_workflow(suite_id))
            a0, j0 = agent.calls, judge.calls

            # a different system prompt => different agent config_hash => miss
            nodes = [
                node("agent", "agent", variant="reference", agent_id="ref-agent",
                     system_prompt="A DIFFERENT prompt changes the config hash"),
                node("run", "run_suite", suite_id=suite_id),
                node("score", "score"), node("card", "scorecard"),
                node("rpt", "report"),
            ]
            edges = [edge("e1", "agent", "agent", "run", "agent"),
                     edge("e2", "run", "run", "score", "run"),
                     edge("e3", "score", "scored", "card", "scored"),
                     edge("e4", "card", "scorecard", "rpt", "scorecard")]
            wf2 = Workflow(workflow_id="wf2", name="v2", nodes=nodes, edges=edges)
            card = await _run(mgr, store, wf2)
            assert card.get("cached") is False
            assert agent.calls > a0 and judge.calls > j0    # re-ran
        asyncio.run(main())

    def test_force_bypasses_cache(self, tmp_path):
        async def main():
            reg, store, mgr, agent, judge = _manager(tmp_path)
            wf = eval_workflow(load_pilot(reg))
            first = await _run(mgr, store, wf)
            j0 = judge.calls
            card = await _run(mgr, store, wf, force=True)
            # force bypasses the scorecard cache: NOT served from cache, the
            # run is re-scored (judge runs again), and a fresh scorecard_id is
            # produced. (Agent traces may be reused via trace-level checkpoint,
            # which is a separate, lower layer.)
            assert card.get("cached") is False
            assert judge.calls > j0
            assert card["scorecard_id"] != first["scorecard_id"]
        asyncio.run(main())


# -- tenant isolation of the cache ----------------------------------------

def test_cache_is_per_tenant(tmp_path):
    eng = make_engine(f"sqlite:///{tmp_path/'shared.db'}")
    from ascore.migrations import run_migrations
    run_migrations(eng)
    a = Registry(engine=eng, tenant="tenant-a")
    b = Registry(engine=eng, tenant="tenant-b")
    a.put_cached_result("k1", "scorecard", "sc-a")
    assert a.get_cached_result("k1")["ref_id"] == "sc-a"
    assert b.get_cached_result("k1") is None            # no cross-tenant leak
    assert a.cached_scorecard_ids() == {"sc-a"}
    assert b.cached_scorecard_ids() == set()


# -- history endpoint flags cached results --------------------------------

from tests.test_api import client, poll  # noqa: E402,F401  (pytest fixture + helper)
from tests.test_executor import eval_workflow as _eval  # noqa: E402


def test_history_lists_scorecards_and_flags_cached(client):
    wf = _eval("pilot-support-triage").model_dump()
    client.post("/api/workflows", json=wf)

    # first run: a FRESH result (this run made real calls)
    eid = client.post("/api/workflows/wf-eval/executions").json()["execution_id"]
    poll(client, eid, "succeeded")
    res1 = client.get(f"/api/executions/{eid}/results").json()
    assert res1["scorecards"][0]["cached"] is False          # fresh run

    rows = client.get("/api/scorecards").json()
    assert len(rows) == 1                                     # listed in history
    sid = rows[0]["scorecard_id"]
    assert rows[0]["cached"] is True   # now reusable-for-free (a cache target)

    # identical re-run: cache HIT — $0, same scorecard, no duplicate row
    eid2 = client.post("/api/workflows/wf-eval/executions").json()["execution_id"]
    poll(client, eid2, "succeeded")
    res2 = client.get(f"/api/executions/{eid2}/results").json()
    assert res2["scorecards"][0]["cached"] is True           # served from cache
    assert res2["scorecards"][0]["scorecard_id"] == sid
    assert res2["scorecards"][0]["total_cost_usd"] == 0.0    # $0 on a cache hit

    rows = client.get("/api/scorecards").json()
    assert [r["scorecard_id"] for r in rows] == [sid]        # no duplicate created
