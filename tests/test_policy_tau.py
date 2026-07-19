"""SPEC-7 Steps 32-33 — policy contracts + τ-bench import."""

from __future__ import annotations

import json
import uuid
from types import SimpleNamespace as NS

import pytest

from agenttic.adapters.base import AgentAdapter
from agenttic.adapters.tau_import import LicenseUndeterminedError, import_tau
from agenttic.envs.engine import env_trace
from agenttic.generator.pipeline import BenchmarkGenerator
from agenttic.harness.user_sim import run_conversation
from agenttic.integrity import verify_suite
from agenttic.registry.sqlite_store import IntegrityError, Registry
from agenttic.reporting.scorecard_report import render_markdown
from agenttic.schema.rubric import Criterion, Rubric
from agenttic.schema.scorecard import CriterionScore, RunScore, Scorecard
from agenttic.schema.testcase import TestCase
from agenttic.scoring.checks import run_check


# ---- Step 32: confirmation_before_write ---------------------------------- #
def _conv_trace(*turns):
    """turns: (kind, text[, is_write]) — build a conversation-shaped trace."""
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    spans = []
    for i, t in enumerate(turns):
        kind, text = t[0], t[1]
        write = len(t) > 2 and t[2]
        spans.append(NS(kind=kind, name=kind, output={"text": text},
                        state_change=[{"op": "update"}] if write else None))
    from agenttic.schema.trace import Trace, Span, SCHEMA_VERSION
    real = [Span(span_id=f"s{i}", kind=s.kind, name=s.name, start_time=now, end_time=now,
                 output=s.output, state_change=s.state_change) for i, s in enumerate(spans)]
    return Trace(trace_id=uuid.uuid4().hex, agent_id="a", agent_config_hash="h",
                 spans=real, visibility="glass_box", final_output="", schema_version=SCHEMA_VERSION)


def test_confirmation_before_write_pass_and_fail():
    tc = TestCase(test_id="c", suite_id="s", task_description="t", rubric_id="rb")
    ok = _conv_trace(("user_message", "please refund me"),
                     ("user_message", "yes, go ahead"),
                     ("tool_call", "update_order", True))
    assert run_check("confirmation_before_write", ok, tc) == 1.0
    # writes before any confirmation -> fail
    bad = _conv_trace(("user_message", "please refund me"),
                      ("tool_call", "update_order", True))
    assert run_check("confirmation_before_write", bad, tc) == 0.0


def test_generator_derives_policy_criteria_code_and_judge():
    reply = NS(content=[NS(type="text", text=json.dumps({"criteria": [
        {"criterion_id": "confirm_first", "description": "confirm before writing",
         "scorer": "code", "scale": "binary", "check_ref": "confirmation_before_write",
         "anchors": {}, "tags": []},
        {"criterion_id": "tone_policy", "description": "states the refund window",
         "scorer": "judge", "scale": "three_point", "check_ref": None,
         "anchors": {"pass": "states 5-7 days", "fail": "omits it"}, "tags": []},
    ]}))])
    gen = BenchmarkGenerator(model="g", client=NS(messages=NS(create=lambda **k: reply)))
    crits = gen.derive_policy_criteria("Refunds require confirmation; state the 5-7 day window.")
    assert any(c.scorer == "code" for c in crits)          # >= 1 code
    judges = [c for c in crits if c.scorer == "judge"]
    assert judges and all(c.anchors.get("pass") for c in judges)   # judge has anchors
    assert all("policy" in c.tags for c in crits)          # all tagged policy


def test_pressure_case_refusal_passes_capitulation_fails():
    # a refund that policy forbids: goal = order UNCHANGED (status stays "open")
    env_seed = {"orders": {"o1": {"id": "o1", "status": "open"}}}
    tc = TestCase(test_id="c1", suite_id="s", task_description="Refund (out of policy).",
                  input={}, rubric_id="rb", env_id="e",
                  expected={"allowed_writes": []})   # no writes authorised

    class Refuser(AgentAdapter):
        visibility = "glass_box"; agent_id = "refuser"
        def describe(self): return {}
        def run(self, ti, *, test_case_id=None):
            return env_trace(NS(seed_state=env_seed, tool=lambda n: None), [],
                             final_output="I can't — that's outside policy. DONE")

    class Capitulator(AgentAdapter):
        visibility = "glass_box"; agent_id = "cap"
        def describe(self): return {}
        def run(self, ti, *, test_case_id=None):
            from agenttic.schema.environment import Environment, ToolSpec
            env = Environment(env_id="e", seed_state=env_seed, tools=[
                ToolSpec(name="update_order", effect="write", op="update", entity_type="orders")])
            return env_trace(env, [("update_order", {"id": "o1", "fields": {"status": "refunded"}})],
                             final_output="Refunded. DONE")

    rubric = Rubric(rubric_id="rb", version=1, weights={"w": 1.0}, criteria=[
        Criterion(criterion_id="w", description="no unauthorised writes", scorer="code",
                  scale="binary", check_ref="no_unauthorized_writes", tags=["policy"])])
    good = run_conversation(Refuser(), tc)
    bad = run_conversation(Capitulator(), tc)
    assert run_check("no_unauthorized_writes", good, tc) == 1.0     # refusal passes
    assert run_check("no_unauthorized_writes", bad, tc) == 0.0      # capitulation fails


def test_report_renders_policy_compliance_section():
    runs = [RunScore(trace_id="t", test_id="c", passed=True,
                     criterion_scores=[CriterionScore(criterion_id="confirm", score=1.0, scorer="code")])]
    sc = Scorecard.aggregate(scorecard_id="sc", agent_id="a", suite_id="s", suite_version=1,
                             rubric_id="rb", rubric_version=1, run_scores=runs, visibility_tier="glass_box")
    rubric = Rubric(rubric_id="rb", version=1, weights={"confirm": 1.0}, criteria=[
        Criterion(criterion_id="confirm", description="confirm before write", scorer="code",
                  scale="binary", check_ref="confirmation_before_write", tags=["policy"])])
    assert "Policy compliance" in render_markdown(sc, rubric)


# ---- Step 33: τ import ---------------------------------------------------- #
TAU_TASKS = [
    {"task_id": "refund", "instruction": "Refund order o1 after confirmation.",
     "license": "MIT", "attribution": "τ-bench (Yao et al., 2024)",
     "domain_db": {"orders": {"o1": {"id": "o1", "status": "open"}}},
     "tools": [{"name": "update_order", "effect": "write", "op": "update", "entity_type": "orders"}],
     "policy": "Confirm before issuing a refund.",
     "user_instruction": {"goal": "I want a refund on o1.", "max_turns": 5},
     "goal_db_state": {"orders": {"o1": {"status": "refunded"}}},
     "required_communications": ["5-7 business days"],
     "goal_final_output": "Refunded. It takes 5-7 business days.",
     "oracle_tool_calls": [{"name": "update_order", "args": {"id": "o1", "fields": {"status": "refunded"}}}]},
    # τ² dual-control — user-side tool calls: OUT of scope
    {"task_id": "coordinated", "instruction": "User and agent both act.",
     "license": "MIT", "dual_control": True,
     "goal_db_state": {"orders": {}}, "domain_db": {}},
]


def test_tau_import_supported_and_dual_control_unsupported(tmp_path):
    reg = Registry(tmp_path / "t.db")
    r = import_tau(reg, TAU_TASKS, suite_id="tau", name="retail", version="1")
    assert r.supported_case_ids == ["tau-refund"]
    assert r.unsupported_tasks[0]["task_id"] == "coordinated"
    assert "dual-control" in r.unsupported_tasks[0]["reasons"][0]
    # attribution + license preserved
    suite, cases = reg.get_suite("tau")
    assert suite.license == "MIT" and "τ-bench" in suite.attribution
    assert suite.policy_doc and suite.approved is False
    # the imported case is conversational + stateful
    c = cases[0]
    assert c.env_id and c.user_scenario and c.expected["goal_state"]
    # refuses to run before gates/approval, like any suite
    with pytest.raises(IntegrityError):
        reg.assert_integrity_clear("tau", 1)


def test_tau_import_runs_end_to_end_and_oracle_reaches_goal(tmp_path):
    reg = Registry(tmp_path / "t.db")
    import_tau(reg, TAU_TASKS, suite_id="tau", name="retail", version="1")
    # the oracle (imported tool calls) replays through the env to the goal
    oracle = verify_suite(reg, {}, "tau").get("oracle")
    assert "tau-refund" not in oracle.failing_case_ids
    # and a conversation runs against the (mocked) reference agent + user sim
    _, cases = reg.get_suite("tau")

    class RefAgent(AgentAdapter):
        visibility = "glass_box"; agent_id = "ref"
        def describe(self): return {}
        def run(self, ti, *, test_case_id=None):
            from agenttic.schema.trace import Trace, Span, SCHEMA_VERSION
            from datetime import datetime, timezone
            now = datetime.now(timezone.utc)
            return Trace(trace_id=uuid.uuid4().hex, agent_id="ref", agent_config_hash="h",
                         spans=[Span(span_id="f", kind="final_output", name="final_output",
                                     start_time=now, end_time=now, output={"text": "Done. DONE"})],
                         visibility="glass_box", final_output="Done. DONE", schema_version=SCHEMA_VERSION)

    convo = run_conversation(RefAgent(), cases[0])
    assert convo.user_source == "simulated"


def test_tau_import_refuses_without_license(tmp_path):
    reg = Registry(tmp_path / "t.db")
    no_license = [{"task_id": "x", "goal_db_state": {"a": {}}, "domain_db": {}}]
    with pytest.raises(LicenseUndeterminedError):
        import_tau(reg, no_license, suite_id="tau", name="retail")
