"""SPEC-12 Step 56 — tool (component tier) certification acceptance tests."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from agenttic.adapters.mcp_server import connect_stdio
from agenttic.certification.tool_suite import (
    ToolSpec, certify_toolset, from_mcp, from_native, link_to_agent_scorecard,
    selection_accuracy)

FIXTURE = str(Path(__file__).parent / "fixtures" / "mcp_server_fixture.py")

SCHEMA = {"type": "object", "properties": {"order_id": {"type": "string"}},
          "required": ["order_id"]}


def _refund(order_id: str) -> str:
    return f"refunded {order_id}"


def _typed(args):
    """A well-behaved callable: typed errors, never raises."""
    if "__simulate__" in args:
        return False, f"UpstreamError: {args['__simulate__']} — retry with backoff"
    if "__force_error__" in args:
        return False, "InvalidRequest: unknown parameter"
    if "order_id" not in args:
        return False, "InvalidRequest: missing required parameter 'order_id'"
    if not isinstance(args.get("order_id"), str):
        return False, "InvalidRequest: order_id must be a string"
    if len(args["order_id"]) > 4096:
        return False, "InvalidRequest: order_id exceeds length limit"
    return True, _refund(args["order_id"])


def _good_tool(desc: str) -> ToolSpec:
    return ToolSpec(name="issue_refund", description=desc, input_schema=SCHEMA,
                    call=_typed, mutating=True, version="2.0", source="native")


CLEAR = ("Issues a refund for a customer order. Modifies billing state.")
VAGUE = ("Handles the thing.")

LOOKUP = ToolSpec(
    name="lookup_order", description="Looks up an order by its identifier.",
    input_schema=SCHEMA, call=_typed, version="1.0", source="native")

TASKS = [
    ("issue a refund for the customer order", "issue_refund"),
    ("refund this order for the buyer", "issue_refund"),
    ("look up the order by identifier", "lookup_order"),
]


# --- 1. a fixture toolset certifies ---------------------------------------- #

def test_fixture_toolset_certifies():
    rep = certify_toolset([_good_tool(CLEAR), LOOKUP], tasks=TASKS,
                          models=["m1", "m2", "m3"],
                          failure_modes={"issue_refund": ["rate_limit", "timeout", "http_500"]})
    assert rep.passed, f"unexpected failures: {rep.failed}"
    assert rep.score == 1.0
    assert rep.selection and rep.selection.accuracy == 1.0


# --- 2. a vague description scores measurably lower ------------------------ #

def test_vague_description_scores_lower_than_a_rewritten_one():
    clear = selection_accuracy([_good_tool(CLEAR), LOOKUP], TASKS,
                               models=["m1", "m2", "m3"])
    vague = selection_accuracy([_good_tool(VAGUE), LOOKUP], TASKS,
                               models=["m1", "m2", "m3"])
    assert clear.accuracy > vague.accuracy, (clear.accuracy, vague.accuracy)
    assert clear.per_tool["issue_refund"] > vague.per_tool["issue_refund"]
    # and the battery turns that into a named, actionable finding
    rep = certify_toolset([_good_tool(VAGUE), LOOKUP], tasks=TASKS,
                          models=["m1", "m2", "m3"])
    assert "description_quality" in rep.failed
    detail = next(o.detail for o in rep.outcomes
                  if o.check_id == "description_quality")
    assert "issue_refund" in detail and "rewrite the description" in detail


# --- 3. component results link to the agent scorecard ---------------------- #

def test_component_results_link_into_the_agent_scorecard():
    rep = certify_toolset([_good_tool(VAGUE), LOOKUP], tasks=TASKS,
                          models=["m1", "m2"])
    agent_sc = {"scorecard_id": "sc-9", "task_success_rate": 0.6}
    linked = link_to_agent_scorecard(agent_sc, rep, used_tools=["issue_refund"])
    ev = linked["component_evidence"]
    # the agent used a tool that was ALREADY known weak — a root cause, not a mystery
    assert ev["known_weak_tools_used"] == ["issue_refund"]
    assert ev["tool_versions"]["issue_refund"] == "2.0"
    assert "description_quality" in ev["toolset_failed_checks"]


# --- 4. two tool sources: native + MCP ------------------------------------- #

def test_works_for_native_and_mcp_sources():
    native = from_native([{
        "name": "issue_refund", "description": CLEAR, "input_schema": SCHEMA,
        "fn": _refund, "mutating": True, "version": "2.0"}])
    assert native[0].source == "native"

    env = {**os.environ, "MCP_FIXTURE_MODE": "good"}
    with connect_stdio([sys.executable, FIXTURE], env=env, timeout=10.0) as c:
        mcp_tools = from_mcp(c)
        assert {t.name for t in mcp_tools} == {"lookup", "create_ticket", "admin_delete"}
        assert all(t.source == "mcp" for t in mcp_tools)
        rep = certify_toolset(mcp_tools)
    # the same component battery ran against MCP-sourced tools
    assert set(rep.sources) == {"mcp"}
    assert "contract_schema" not in rep.failed
    assert "input_fuzzing" not in rep.failed


# --- component-tier defects are caught ------------------------------------- #

def test_a_raising_tool_is_a_failure_not_a_crash():
    def _raises(args):
        raise RuntimeError("boom /home/svc/app/tool.py")
    bad = ToolSpec(name="fragile", description="Does a thing.",
                   input_schema=SCHEMA, call=_raises)
    rep = certify_toolset([bad])
    assert "input_fuzzing" in rep.failed
    assert "RAISED" in next(o.detail for o in rep.outcomes
                            if o.check_id == "input_fuzzing")


def test_undisclosed_mutating_tool_is_caught():
    silent = ToolSpec(name="wipe", description="Does a thing.",
                      input_schema=SCHEMA, call=_typed, mutating=True)
    rep = certify_toolset([silent])
    assert "side_effect_disclosure" in rep.failed


def test_untyped_failure_mode_is_caught():
    def _swallow(args):
        return True, "ok"          # pretends success even on a simulated 5xx
    t = ToolSpec(name="flaky", description="Sends a request. Modifies state.",
                 input_schema=SCHEMA, call=_swallow, mutating=True)
    rep = certify_toolset([t], failure_modes={"flaky": ["http_500"]})
    assert "failure_mode_handling" in rep.failed
