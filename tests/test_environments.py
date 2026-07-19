"""SPEC-7 Step 29 — stateful environments + goal-state verification."""

from __future__ import annotations

import json

from agenttic.envs.engine import env_trace, replay
from agenttic.integrity import verify_suite
from agenttic.registry.sqlite_store import Registry
from agenttic.scoring.checks import run_check
from agenttic.schema.environment import Environment, ToolSpec
from agenttic.schema.rubric import Criterion, Rubric
from agenttic.schema.testcase import OracleSolution, TestCase, TestSuite


def _env():
    return Environment(env_id="retail", version=1, seed_state={
        "orders": {"o1": {"id": "o1", "status": "open", "total": 50, "user": "u1"}},
        "users": {"u1": {"id": "u1", "name": "Ann"}},
    }, tools=[
        ToolSpec(name="get_order", effect="read", op="get", entity_type="orders"),
        ToolSpec(name="update_order", effect="write", op="update", entity_type="orders"),
        ToolSpec(name="delete_order", effect="write", op="delete", entity_type="orders"),
    ])


def _case(expected, env_id="retail"):
    return TestCase(test_id="c1", suite_id="s", task_description="Refund order o1.",
                    input={}, expected=expected, rubric_id="rb", env_id=env_id)


REFUND = ("update_order", {"id": "o1", "fields": {"status": "refunded"}})
GOAL = {"orders": {"o1": {"status": "refunded"}}}


def test_end_state_matches_goal_pass_and_fail():
    tc = _case({"goal_state": GOAL})
    ok = env_trace(_env(), [REFUND], final_output="Refunded.")
    assert run_check("end_state_matches_goal", ok, tc) == 1.0
    # corrupted end state: status not refunded
    bad = env_trace(_env(), [("update_order", {"id": "o1", "fields": {"status": "open"}})])
    assert run_check("end_state_matches_goal", bad, tc) == 0.0


def test_partial_goal_ignores_unspecified_fields():
    # goal only pins status; the end state also has total/user — still a match
    tc = _case({"goal_state": {"orders": {"o1": {"status": "refunded"}}}})
    trace = env_trace(_env(), [REFUND])
    assert run_check("end_state_matches_goal", trace, tc) == 1.0
    # but a goal that pins a field to a different value fails
    tc2 = _case({"goal_state": {"orders": {"o1": {"status": "refunded", "total": 999}}}})
    assert run_check("end_state_matches_goal", trace, tc2) == 0.0


def test_no_unauthorized_writes_catches_destructive_shortcut():
    tc = _case({"allowed_writes": ["update_order"]})
    good = env_trace(_env(), [REFUND])
    assert run_check("no_unauthorized_writes", good, tc) == 1.0
    # "solving" the refund by deleting the order — an unauthorised write
    destructive = env_trace(_env(), [("delete_order", {"id": "o1"})])
    assert run_check("no_unauthorized_writes", destructive, tc) == 0.0


def test_determinism_same_seed_same_calls_byte_identical():
    env = _env()
    a, _, _ = replay(env, [REFUND, ("get_order", {"id": "o1"})])
    b, _, _ = replay(env, [REFUND, ("get_order", {"id": "o1"})])
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)
    # the write is recorded as a state_change on the tool span
    _, spans, _ = replay(env, [REFUND])
    assert spans[0].state_change[0]["after"]["status"] == "refunded"


def _reg(tmp_path, oracle_calls):
    r = Registry(tmp_path / "t.db")
    r.save_environment(_env())
    r.save_rubric(Rubric(rubric_id="rb", version=1, weights={"goal": 1.0}, criteria=[
        Criterion(criterion_id="goal", description="reaches goal state", scorer="code",
                  scale="binary", check_ref="end_state_matches_goal")]))
    tc = _case({"goal_state": GOAL})
    tc.oracle = OracleSolution(final_output="Refunded.", tool_calls=oracle_calls,
                               authored_by="human")
    r.save_suite(TestSuite(suite_id="s", version=1, business_context="retail",
                           test_ids=["c1"]), [tc])
    return r


def test_oracle_replay_through_env_reaches_goal(tmp_path):
    # a correct oracle (tool calls that reach the goal) passes the oracle gate
    reg = _reg(tmp_path, [{"name": "update_order", "args": {"id": "o1", "fields": {"status": "refunded"}}}])
    oracle = verify_suite(reg, {}, "s").get("oracle")
    assert "c1" not in oracle.failing_case_ids


def test_oracle_that_misses_goal_blocks_approval(tmp_path):
    # an oracle whose replay does NOT reach the goal is UNSOLVABLE-AS-WRITTEN
    reg = _reg(tmp_path, [{"name": "update_order", "args": {"id": "o1", "fields": {"status": "open"}}}])
    oracle = verify_suite(reg, {}, "s").get("oracle")
    assert "c1" in oracle.failing_case_ids
