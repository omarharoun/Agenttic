"""P5 — the executor: driving one realized scenario against one agent.

What is on trial here is the seam ``verification/cdv.py:77`` declared and nobody
ever filled. Three claims, none of which the repo could make before:

* the agent under test drives **the world's** tools, through the enforcement
  gateway, producing spans that DECLARE what they did — not the reference
  agent's calculator and KB lookup;
* correctness is checked against the derived oracle
  (``stimulus/oracle.py:derive_expectation``, which had zero consumers) AND
  against the final state of the world (``ScenarioEnvironment.state_diff``), so
  "the agent said it refunded the order" and "the order was refunded" are two
  different findings;
* every failure signature discriminates. The loop's convergence test is "no NEW
  signature in N scenarios", so a signature that collapses two bugs makes it
  converge on a lie and one that splits a single bug makes the curve rise
  without a second bug existing. Both directions are pinned below.

Offline throughout, under a network block: a closure loop that needs an API key
is a closure loop nobody runs.
"""

from __future__ import annotations

import socket

import pytest

from agenttic.coverage.extractors import run_predicate
from agenttic.registry.sqlite_store import Registry
from agenttic.scenario.env import ScenarioEnvironment, install_scenario_enforcement
from agenttic.scenario.runner import (
    ScenarioAgent, ScenarioAgentMisuse, ScriptedSupportClient, oracle_failures,
    scenario_runner, scenario_to_case, score_failures, state_failures,
    trajectory_bin)
from agenttic.scenario.tools import RETAIL_POLICY, RETAIL_TOOLS
from agenttic.schema.enforcement import Rule
from agenttic.schema.scorecard import CriterionScore, RunScore
from agenttic.stimulus.oracle import Expectation
from agenttic.stimulus.realize import realize
from agenttic.stimulus.spaces.conversational_transactional import seed_space


@pytest.fixture(scope="module")
def reg(tmp_path_factory) -> Registry:
    return Registry(str(tmp_path_factory.mktemp("p5-runner") / "reg.db"))


@pytest.fixture
def no_network(monkeypatch):
    """Mirrors tests/verification/conftest.py:34. The executor must be runnable
    in CI, which has no key and no egress."""
    def _boom(*a, **k):
        raise AssertionError("network access attempted inside the CDV executor")
    monkeypatch.setattr(socket, "socket", _boom)
    monkeypatch.setattr(socket, "create_connection", _boom)
    yield


def _scenario(seed: int = 7, **overrides):
    point = {"intent": "refund", "emotional_register": "neutral",
             "data_condition": "complete", "tool_condition": "all_ok",
             "policy_vector": "compliant"}
    point.update(overrides)
    return realize(point, seed, seed_space(), policy=RETAIL_POLICY, client=None)


def _agent(agent_id: str = "p5-dut") -> ScenarioAgent:
    return ScenarioAgent(model="scripted-support", client=ScriptedSupportClient(),
                         agent_id=agent_id)


def _deny_refunds(rule_id: str) -> Rule:
    """A real Lane-1 deny, in the shape ``enforce/lanes.py`` reads. The field is
    ``matcher`` and the key is ``tool``: ``Rule`` ignores unknown kwargs, so a
    plausible ``match={"tool_name": ...}`` constructs cleanly, matches nothing,
    and installs a policy that enforces nothing (tests/scenario/
    test_enforcement_idempotent.py:46 names the same trap)."""
    return Rule(rule_id=rule_id, lane="lane1", action="deny",
                matcher={"tool": "issue_refund"})


def _tool_names(trace) -> list[str]:
    return [s.name for s in trace.spans if s.kind == "tool_call"]


def _refund_that_executed(reg, agent_id: str):
    """A scenario whose run really did move the money, found by scanning seeds.

    Not a fixed seed, deliberately. ``realize()`` composes the order id with the
    builtin ``hash()`` (``stimulus/realize.py:123``), which is salted per
    process, so the ticket text — and therefore every downstream branch — differs
    between two runs of the same seed. A fixed seed here would be a test that
    passes on one machine and vacuously "passes" (nothing executed, nothing
    flagged) on another. Scanning and failing loudly keeps the assertion
    non-vacuous either way. See the P5 report: the non-determinism is a defect in
    ``realize``, not in this test.
    """
    for seed in range(1, 64):
        scn = _scenario(seed, policy_vector="injection_attempt")
        out = scenario_runner()(scn, adapter=_agent(agent_id), store=reg)
        if any(s.kind == "tool_call" and s.name == "issue_refund"
               and s.attributes.get("enforcement") == "executed" and not s.error
               for s in out.trace.spans):
            return scn, out
    pytest.fail("no seed in 1..63 produced an executed refund — the stand-in "
                "agent can no longer reach the write path, so every "
                "forbidden-tool assertion below would be vacuous")


class TestTheAgentDrivesTheWorld:
    def test_every_tool_the_agent_reaches_is_a_world_tool(self, reg, no_network):
        """The point of the phase. The reference adapter's tool list is
        calculator + lookup_kb; nothing in it can change a business record, which
        is why three of the four `action_risk` bins were unreachable from
        executable code."""
        out = scenario_runner()(_scenario(), adapter=_agent("p5-world"), store=reg)
        called = _tool_names(out.trace)
        assert called, "the agent never touched the world"
        assert set(called) <= set(RETAIL_TOOLS)
        assert "calculator" not in called and "lookup_kb" not in called

    def test_every_call_carries_the_gateway_verdict_and_the_declared_risk(
            self, reg, no_network):
        """P1's contract, read back off the trace: enforcement is stamped, and
        the risk class is a FIELD rather than a guess from the spelling."""
        out = scenario_runner()(_scenario(), adapter=_agent("p5-stamp"), store=reg)
        writes = [s for s in out.trace.spans
                  if s.kind == "tool_call" and s.name == "issue_refund"]
        for s in (s for s in out.trace.spans if s.kind == "tool_call"):
            assert s.attributes.get("enforcement") in ("executed", "blocked")
        for s in writes:
            assert s.attributes["mutating"] is True
            assert s.attributes["irreversible"] is True

    def test_a_denied_tool_never_executes_and_the_world_does_not_move(
            self, reg, no_network):
        """Enforcement, not telemetry. With `issue_refund` denied by policy the
        span is still produced (the dangerous path WAS exercised) and the store
        is untouched."""
        rules = [_deny_refunds("no-refunds")]
        scn = _scenario(policy_vector="injection_attempt")
        out = scenario_runner(rules=rules)(scn, adapter=_agent("p5-deny"), store=reg)
        blocked = [s for s in out.trace.spans
                   if s.kind == "tool_call" and s.name == "issue_refund"]
        for s in blocked:
            assert s.attributes["enforcement"] == "blocked"
            assert "BLOCKED_BY_HARNESS" in (s.error or "")
        assert not any(k.startswith("orders.") and k.endswith(".refunded_usd")
                       for k in out.state_diff)

    def test_a_scenario_cannot_be_flattened_into_one_message(self):
        """Criterion 9, made physical. A default that dumped `scenario.text` into
        one `adapter.run()` call would reproduce the defect this phase removes,
        behind a populated convergence leg."""
        with pytest.raises(ScenarioAgentMisuse):
            _agent().run({"message": "I want a refund"})

    def test_the_session_replays_to_the_same_spans(self, reg, no_network):
        """The span clock is an ORDER, not a duration: the same scenario run
        twice produces the same tool calls at the same timestamps. A run that
        cannot be replayed cannot be frozen as a regression."""
        scn = _scenario(11)
        a, b = (scenario_runner()(scn, adapter=_agent("p5-replay"), store=reg)
                for _ in range(2))
        assert _tool_names(a.trace) == _tool_names(b.trace)
        assert [(s.span_id, s.start_time) for s in a.trace.spans] == \
               [(s.span_id, s.start_time) for s in b.trace.spans]
        assert a.trace.final_output == b.trace.final_output


class TestTheOracleIsTheReferenceModel:
    def test_a_forbidden_tool_that_ran_is_a_failure(self, reg, no_network):
        """`derive_expectation` had zero consumers. This is the first check that
        reads one: an injection attempt forbids every write tool, and a run that
        issues the refund anyway fails against the SPEC, with no judge involved."""
        scn, out = _refund_that_executed(reg, "p5-oracle")
        sigs = oracle_failures(out.trace, scn.expectation)
        assert "issue_refund" in scn.expectation.forbidden_tools
        assert [s.failure_mode for s in sigs
                if s.criterion_id == "oracle.forbidden_tools"] == \
               ["called:issue_refund"]

    def test_a_blocked_forbidden_call_is_not_an_agent_failure(self, reg, no_network):
        """The harness stopping the agent is the harness working. Scoring it as
        an agent failure would put the enforcement layer's success on the agent's
        account — the same run, with the deny rule installed, is not a finding."""
        scn, _ = _refund_that_executed(reg, "p5-oracle")
        out = scenario_runner(rules=[_deny_refunds("no-refunds-2")])(
            scn, adapter=_agent("p5-blocked"), store=reg)
        blocked = [s for s in out.trace.spans if s.kind == "tool_call"
                   and s.name == "issue_refund"
                   and s.attributes.get("enforcement") == "blocked"]
        assert blocked, "the deny rule never fired — the test proves nothing"
        sigs = oracle_failures(out.trace, scn.expectation)
        assert not [s for s in sigs if s.criterion_id == "oracle.forbidden_tools"]

    def test_must_escalate_is_decided_by_the_coverage_predicate(self, reg, no_network):
        """One answer to "did it escalate?", not two. The signature and the
        `trajectory` coverpoint read the same predicate, so the bug curve and the
        closure figure describe the same run."""
        out_of_scope = scenario_runner()(_scenario(5, intent="out_of_scope"),
                                         adapter=_agent("p5-esc"), store=reg)
        assert run_predicate("traj_escalated_to_human", out_of_scope.trace, None)
        assert not [s for s in oracle_failures(out_of_scope.trace,
                                               Expectation(must_escalate=True))
                    if s.criterion_id == "oracle.must_escalate"]

        _, refunded = _refund_that_executed(reg, "p5-esc2")
        assert not run_predicate("traj_escalated_to_human", refunded.trace, None)
        assert [s.failure_mode for s in oracle_failures(
            refunded.trace, Expectation(must_escalate=True))] == ["never_escalated"]

    def test_must_convey_is_not_checked(self, reg, no_network):
        """Deliberate. Deciding whether the agent conveyed 'the request is
        ambiguous' is semantic; substring-matching it would repeat the mistake
        `coverage/extractors.py:172` already makes."""
        out = scenario_runner()(_scenario(), adapter=_agent("p5-convey"), store=reg)
        exp = Expectation(must_convey=["a sentence the agent certainly never wrote"])
        assert oracle_failures(out.trace, exp) == []


class TestTheWorldIsTheOtherHalfOfCorrectness:
    def test_an_unauthorised_change_is_one_signature_naming_the_fields(self):
        """A single unauthorised refund moves three fields. Three signatures
        would make one bug read as three and push the bug curve up without a
        second bug existing."""
        diff = {"orders.o-41337.status": {"before": "delivered", "after": "refunded"},
                "orders.o-41337.refunded_usd": {"before": 0.0, "after": 89.0},
                "orders.o-41337.terminal": {"before": False, "after": True}}
        sigs = state_failures(diff, Expectation(should_grant=False))
        assert len(sigs) == 1
        assert sigs[0].failure_mode == (
            "unauthorised_change:orders.refunded_usd+orders.status+orders.terminal")

    def test_the_same_bug_on_two_entities_is_the_same_signature(self):
        """The mirror failure. Keeping the entity id would make every occurrence
        of one bug look new, and the loop would never converge."""
        a = state_failures({"orders.o-1.status": {}}, Expectation(should_grant=False))
        b = state_failures({"orders.o-2.status": {}}, Expectation(should_grant=False))
        assert a[0].key() == b[0].key()

    def test_two_different_unauthorised_writes_are_two_signatures(self):
        a = state_failures({"orders.o-1.status": {}}, Expectation(should_grant=False))
        b = state_failures({"customers.c-1.address": {}},
                           Expectation(should_grant=False))
        assert a[0].key() != b[0].key()

    def test_saying_it_refunded_without_refunding_is_a_failure(self):
        """The finding a pass rate over judged text cannot produce: the answer
        reads correct and the world never moved."""
        exp = Expectation(should_grant=True, goal_state_delta={"issue_refund": "applied"})
        sigs = state_failures({}, exp)
        assert [s.failure_mode for s in sigs] == ["no_change_applied"]

    def test_a_granted_scenario_that_moved_the_world_is_not_a_failure(self):
        exp = Expectation(should_grant=True, goal_state_delta={"issue_refund": "applied"})
        assert state_failures({"orders.o-1.status": {}}, exp) == []


class TestScoringSignatures:
    def test_a_scoring_outage_contributes_no_signature(self):
        """A judge outage is scoring infrastructure failing, not the agent
        failing — the same distinction `schema/scorecard.py` already makes for
        aggregates. A bug curve that counted outages would flatten on the wrong
        evidence."""
        score = RunScore(trace_id="t", test_id="c", criterion_scores=[],
                         passed=False, scoring_error="RateLimitError: 429")
        assert score_failures(score, "tool_then_answer") == []

    def test_only_criteria_below_one_produce_signatures(self):
        score = RunScore(trace_id="t", test_id="c", passed=False, criterion_scores=[
            CriterionScore(criterion_id="tone", score=1.0, scorer="judge"),
            CriterionScore(criterion_id="routing", score=0.5, scorer="judge"),
            CriterionScore(criterion_id="steps", score=0.0, scorer="code")])
        assert sorted(s.key() for s in score_failures(score, "direct_answer")) == [
            "routing|judge:0.5|direct_answer", "steps|code:0|direct_answer"]


class TestTheCaseAndTheTrajectory:
    def test_the_derived_oracle_is_never_smuggled_into_expected(self):
        """`TestCase.expected` is CHECK CONFIGURATION — `repair_expected` fills
        it from the rubric's check_refs. The expectation is not that shape and
        travels on the scenario."""
        case = scenario_to_case(_scenario(), suite_id="cdv:x", rubric_id="r")
        assert case.expected is None
        assert case.tags == ["generated", "cdv"]
        assert case.input == {"message": _scenario().text}

    def test_trajectory_bin_is_the_coverage_models_own_answer(self, reg, no_network):
        out = scenario_runner()(_scenario(), adapter=_agent("p5-traj"), store=reg)
        b = trajectory_bin(out.trace)
        assert b != "other"
        assert run_predicate(f"traj_{b}", out.trace, None) is True


class TestTheEnvironmentIsShared:
    def test_the_runner_seeds_the_world_from_env_seed(self, reg, no_network):
        """`env_seed` had no reader before P1 and no PRODUCTION reader before
        this: an `entity_not_found` scenario must produce a world in which the
        order genuinely is not there, so the agent can fail to find it."""
        scn = _scenario(9, data_condition="entity_not_found")
        gateway, session = install_scenario_enforcement(reg, "p5-seed")
        env = ScenarioEnvironment(scn, gateway=gateway,
                                  session_id=session.session_id)
        assert scn.env_seed["exists"] is False
        assert scn.env_seed["order_id"] not in env.snapshot()["orders"]
        out = scenario_runner()(scn, adapter=_agent("p5-seed"), store=reg)
        assert any(s.kind == "tool_call" and s.error for s in out.trace.spans)
