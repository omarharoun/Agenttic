"""P6 — a scenario driven as a CONVERSATION, and what that lets coverage see.

``session_shape`` has been declared not-measurable since the coverage model was
written, for a reason that was true: "nothing emits a ``user_turn`` span … there
is no second human turn for a run to exhibit". P2 built a counterparty and P3
built a session that stamps the span; neither was wired to a world, so no run
produced one. This file is the wiring on trial.

Three claims, in the order they matter:

1. a multi-turn run CREDITS ``session_multi_turn`` **from the trace** — the same
   scenario down the single-turn path credits ``single_turn`` and zero multi-turn
   hits, so the credit is a property of the run and not of the code being
   present;
2. the second turn is EARNED. The scenario withholds a fact; an agent that asks
   for it gets it and finishes, an agent that does not is pushed back and the
   customer leaves. Same scenario, same seed, same counterparty — the only
   variable is whether the agent asks;
3. the single-turn path is untouched. It emits no turn spans, carries no session
   id, and still ticks one second per event from the world's zero.

Offline throughout, under a network block.
"""

from __future__ import annotations

import json
import socket
from datetime import timedelta
from types import SimpleNamespace

import pytest

from agenttic.coverage.collect import Sample, collect
from agenttic.coverage.extractors import run_predicate
from agenttic.coverage.models.conversational_transactional import seed_model
from agenttic.registry.sqlite_store import Registry
from agenttic.scenario.env import EPOCH
from agenttic.scenario.runner import (
    _ASK_ORDER, ScenarioAgent, ScenarioAgentMisuse, ScenarioConversation,
    ScriptedSupportClient, multi_turn_scenario_runner, scenario_runner)
from agenttic.scenario.tools import RETAIL_POLICY
from agenttic.scenario.user import ScriptedUser, asks_for
from agenttic.stimulus.realize import realize
from agenttic.stimulus.spaces.conversational_transactional import seed_space


@pytest.fixture(scope="module")
def reg(tmp_path_factory) -> Registry:
    return Registry(str(tmp_path_factory.mktemp("p6-multiturn") / "reg.db"))


@pytest.fixture
def no_network(monkeypatch):
    """A conversation that needs a key is a conversation CI never has."""
    def _boom(*a, **k):
        raise AssertionError("network access attempted inside a scenario "
                             "conversation")
    monkeypatch.setattr(socket, "socket", _boom)
    monkeypatch.setattr(socket, "create_connection", _boom)
    yield


def gating_scenario(seed: int = 11, intent: str = "account_change"):
    """A REAL generated scenario whose order id is genuinely withheld.

    ``account_change`` is one of the two intents whose template carries no
    ``{order}`` placeholder (``stimulus/realize.py``), so the id lands in
    ``hidden_facts`` without landing in the ticket. Every test below that turns
    on elicitation asserts that separately — a fixture that stopped withholding
    would make them all pass vacuously.
    """
    point = {"intent": intent, "emotional_register": "neutral",
             "data_condition": "complete", "tool_condition": "all_ok",
             "policy_vector": "compliant"}
    return realize(point, seed, seed_space(), policy=RETAIL_POLICY, client=None)


def asking_agent(agent_id: str = "p6-asks") -> ScenarioAgent:
    """The house stand-in, which asks for a fact it was not given."""
    return ScenarioAgent(model="scripted-support", client=ScriptedSupportClient(),
                         agent_id=agent_id)


class MuteClient:
    """Looks busy, asks nothing. Deliberately not silent and not rude: this is
    the version of the failure a judge reading text alone scores as a pass."""

    def __init__(self) -> None:
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **_kw):
        return SimpleNamespace(
            stop_reason="end_turn",
            usage=SimpleNamespace(input_tokens=10, output_tokens=5),
            content=[SimpleNamespace(type="text",
                                     text="I am looking into it now.")])


def mute_agent(agent_id: str = "p6-mute") -> ScenarioAgent:
    return ScenarioAgent(model="never-asks", client=MuteClient(),
                         agent_id=agent_id)


def session_shape(scn, trace):
    """The real coverage collector's verdict on one run."""
    rep = collect(seed_model(), [Sample(trace=trace, scenario=scn.as_dict(),
                                        requested=dict(scn.point))])
    return rep.coverpoints["session_shape"]


def turn_spans(trace):
    return [s for s in trace.spans if s.kind == "user_turn"]


# --------------------------------------------------------------------------- #
# 1. THE claim: a conversation credits multi_turn, a ticket does not
# --------------------------------------------------------------------------- #


class TestSessionShapeIsCreditedFromTheTrace:
    def test_the_single_turn_path_exhibits_no_turn_at_all(self, reg, no_network):
        """The before-state, pinned so the after-state means something.

        A single-shot ticket run emits NO turn markers at all — zero
        ``user_turn`` spans, and therefore no multi-turn credit. That is what
        makes the credit in the next test evidence rather than arithmetic: this
        path cannot produce it, so the conversation did.

        What the ``single_turn`` bin does with an uninstrumented trace is
        deliberately not asserted here. Whether zero turns reads as "one turn" or
        as "not measured" is ``coverage/extractors.py``'s declaration to make,
        this file has no standing to pin it, and pinning it would make a test
        about the runner fail whenever that declaration is revisited.
        """
        scn = gating_scenario()
        out = scenario_runner()(scn, adapter=asking_agent("p6-single"), store=reg)
        assert turn_spans(out.trace) == []
        assert out.trace.session_id is None
        assert run_predicate("session_multi_turn", out.trace) is False
        assert session_shape(scn, out.trace).bins["multi_turn"].trace_hits == 0

    def test_a_conversation_credits_multi_turn(self, reg, no_network):
        """The phase's whole point, measured through the real collector."""
        scn = gating_scenario()
        out = multi_turn_scenario_runner()(scn, adapter=asking_agent(),
                                           store=reg)
        assert len(turn_spans(out.trace)) >= 2
        assert run_predicate("session_multi_turn", out.trace) is True
        assert run_predicate("session_single_turn", out.trace) is False

        cp = session_shape(scn, out.trace)
        assert cp.bins["multi_turn"].trace_hits == 1
        assert cp.bins["single_turn"].trace_hits == 0

    def test_the_credit_comes_from_the_run_and_not_from_the_request(
            self, reg, no_network):
        """Hard rule: coverage is credited from what a run EXHIBITED.

        No abstract point in the space names ``session_shape``, so nothing
        REQUESTED a multi-turn session. The stimulus side of the bin is
        therefore zero while the trace side is one — the two-number split doing
        the job it exists for.
        """
        scn = gating_scenario()
        out = multi_turn_scenario_runner()(scn, adapter=asking_agent("p6-src"),
                                           store=reg)
        assert "session_shape" not in scn.point
        b = session_shape(scn, out.trace).bins["multi_turn"]
        assert (b.trace_hits, b.stimulus_hits) == (1, 0)

    def test_the_credit_does_not_depend_on_the_models_declaration(
            self, reg, no_network):
        """The evidence stands whether or not the coverpoint is enabled.

        ``coverage/models/conversational_transactional.py`` declares
        ``session_shape`` ``measurable=False`` on the grounds that "nothing emits
        a ``user_turn`` span". This path makes that reason false, but the file is
        not this one's to change and the flip is somebody else's call — so what
        is asserted is the part that is true either way: the bin is credited from
        the trace, and ``collect()`` withholds a closure FIGURE exactly while the
        declaration stands. Deliberately not a pin on the flag itself: a runner
        test that failed the day the model file was corrected would be an
        obstacle wearing the costume of a guard.
        """
        scn = gating_scenario()
        out = multi_turn_scenario_runner()(scn, adapter=asking_agent("p6-nm"),
                                           store=reg)
        cp = session_shape(scn, out.trace)
        assert cp.bins["multi_turn"].trace_hits == 1
        assert (cp.trace_closure is None) is (not cp.measurable)
        if not cp.measurable:
            assert turn_spans(out.trace), (
                "session_shape is declared not measurable because nothing emits "
                "a user_turn span, and this trace has none either — the "
                "declaration would be correct and this whole file vacuous")


# --------------------------------------------------------------------------- #
# 2. the second turn is EARNED
# --------------------------------------------------------------------------- #


class TestTheAgentHasToAsk:
    def test_the_fixture_really_withholds_the_fact(self):
        """Guards every test in this class. If the ticket stated the order id
        there would be nothing to elicit and the asking agent would "pass" a gate
        that never closed."""
        scn = gating_scenario()
        order = scn.hidden_facts["order_id"]
        assert order not in scn.text
        assert ScriptedUser.from_scenario(scn).gating == ("order_id",)

    def test_an_agent_that_asks_is_told_and_finishes(self, reg, no_network):
        scn = gating_scenario()
        out = multi_turn_scenario_runner()(scn, adapter=asking_agent("p6-ask"),
                                           store=reg)
        assert out.disclosed == ["order_id"]
        assert out.withheld == []
        assert out.ended == "satisfied"

    def test_an_agent_that_never_asks_is_left(self, reg, no_network):
        """The other direction, same scenario and same counterparty. Without
        this the class would only prove that a conversation can happen."""
        scn = gating_scenario()
        out = multi_turn_scenario_runner()(scn, adapter=mute_agent("p6-mute1"),
                                           store=reg)
        assert out.disclosed == []
        assert out.withheld == ["order_id"]
        assert out.ended == "gave_up"
        # It still exhibited a multi-turn session — the customer spoke four
        # times. Coverage credit is about what happened, not about who won.
        assert run_predicate("session_multi_turn", out.trace) is True

    def test_the_elicited_fact_reaches_the_world(self, reg, no_network):
        """An elicited fact that is never used is a decoration.

        The order id exists in exactly two places: ``hidden_facts``, and the turn
        the counterparty took to reveal it. So a ``lookup_order`` span carrying it
        can only have come through the conversation.
        """
        scn = gating_scenario()
        order = scn.hidden_facts["order_id"]
        out = multi_turn_scenario_runner()(scn, adapter=asking_agent("p6-world"),
                                           store=reg)
        looked_up = [s for s in out.trace.spans
                     if s.kind == "tool_call" and s.name == "lookup_order"]
        assert [s for s in looked_up if s.input.get("order_id") == order]
        assert any(order in t["text"] for t in out.turns if t["kind"] == "reveal")

    def test_the_mute_agent_reaches_no_tool_at_all(self, reg, no_network):
        """The control arm is a control: it must differ in what it DID, not only
        in what the counterparty concluded."""
        scn = gating_scenario()
        out = multi_turn_scenario_runner()(scn, adapter=mute_agent("p6-mute2"),
                                           store=reg)
        assert [s.name for s in out.trace.spans if s.kind == "tool_call"] == []
        assert out.state_diff == {}

    def test_the_ask_is_one_the_counterparty_can_parse(self):
        """A coupling between two modules, asserted rather than hoped for.

        ``ScriptedUser`` only reveals a fact when ``asks_for`` recognises the
        question: an ELICITATION cue AND a request marker. A stand-in whose
        question the stand-in customer cannot parse would make every run above
        read as an agent that never asked — a false fail, and a silent one.
        """
        assert asks_for(_ASK_ORDER, "order_id") is True

    def test_the_world_moved_only_for_the_agent_that_asked(self, reg, no_network):
        """State-based reward, through the conversation path. The address change
        is a real write against the seeded store."""
        scn = gating_scenario()
        out = multi_turn_scenario_runner()(scn, adapter=asking_agent("p6-diff"),
                                           store=reg)
        assert out.state_diff, "the address change never landed"
        assert any(k.startswith("customers.") for k in out.state_diff)


# --------------------------------------------------------------------------- #
# 3. what the trace records, and who recorded it
# --------------------------------------------------------------------------- #


class TestTheEvidenceTheConversationLeaves:
    def test_the_trace_names_its_session(self, reg, no_network):
        """A conversation stored without its session id is indistinguishable
        from a single-shot run once it is in the registry."""
        scn = gating_scenario()
        out = multi_turn_scenario_runner()(scn, adapter=asking_agent("p6-sid"),
                                           store=reg)
        assert out.trace.session_id == out.session_id
        assert reg.get_trace(out.trace.trace_id).session_id == out.session_id

    def test_every_turn_span_precedes_the_answer_it_provoked(
            self, reg, no_network):
        """The session emits the turn span BEFORE the agent is handed the
        message (``session.deliver``), so the agent cannot mint a turn it was
        never given. Checked as an ORDER over the whole trace, both by position
        and by clock — the two must agree or the artifact tells two stories.
        """
        scn = gating_scenario()
        out = multi_turn_scenario_runner()(scn, adapter=asking_agent("p6-order"),
                                           store=reg)
        spans = out.trace.spans
        first_turn = next(i for i, s in enumerate(spans) if s.kind == "user_turn")
        assert first_turn == 0, "the agent acted before the customer spoke"
        times = [s.start_time for s in spans]
        assert times == sorted(times), "spans are not in clock order"
        assert len(set(times)) == len(times), "two spans share a timestamp"

    def test_the_closing_turn_is_never_delivered_to_the_agent(
            self, reg, no_network):
        """"Thanks, that's sorted" is said after the last answer and is not a
        turn the agent took part in — so it is evidence on the outcome and NOT a
        ``user_turn`` span. Counting it would inflate the one number
        ``session_shape`` reads."""
        scn = gating_scenario()
        out = multi_turn_scenario_runner()(scn, adapter=asking_agent("p6-close"),
                                           store=reg)
        assert out.turns[-1]["kind"] == "close"
        assert out.user_turns == len(out.turns) - 1
        assert out.user_turns == len(turn_spans(out.trace))

    def test_the_provenance_says_which_simulator_stood_in_for_a_human(
            self, reg, no_network):
        """``user_source`` has been in the signed manifest since SPEC-12 with
        nothing to fill it. "simulated" alone cannot tell a rule table from a
        frontier model, so both travel."""
        scn = gating_scenario()
        out = multi_turn_scenario_runner()(scn, adapter=asking_agent("p6-prov"),
                                           store=reg)
        assert out.user_provenance["user_source"] == "simulated"
        assert out.user_provenance["simulator"] == "scripted"
        assert out.user_provenance["gating_facts"] == ["order_id"]

    def test_a_fact_that_could_not_gate_is_disclosed_not_dropped(
            self, reg, no_network):
        """``hidden_facts`` also carries ``data_condition``, which is a bin name
        and not something a person holds. It must arrive at the caller as a named
        exclusion, never as silence."""
        scn = gating_scenario()
        out = multi_turn_scenario_runner()(scn, adapter=asking_agent("p6-disc"),
                                           store=reg)
        facts = {d.get("fact") for d in out.disclosures}
        assert "data_condition" in facts

    def test_the_conversation_replays_to_the_same_spans(self, reg, no_network):
        """Same scenario, same counterparty, same world: the same session, down
        to the span ids and the timestamps. A run that cannot be replayed cannot
        be frozen as a regression."""
        scn = gating_scenario()
        run = multi_turn_scenario_runner()
        a = run(scn, adapter=asking_agent("p6-replay"), store=reg)
        b = run(scn, adapter=asking_agent("p6-replay"), store=reg)
        assert [(s.span_id, s.kind, s.start_time) for s in a.trace.spans] == \
               [(s.span_id, s.kind, s.start_time) for s in b.trace.spans]
        assert a.trace.final_output == b.trace.final_output
        assert [t["text"] for t in a.turns] == [t["text"] for t in b.turns]

    def test_cost_and_latency_are_totalled_over_every_turn(
            self, reg, no_network):
        """A conversation costs more than its last turn. Both totals are summed
        from the per-turn traces rather than left at the final one."""
        scn = gating_scenario()
        out = multi_turn_scenario_runner()(scn, adapter=asking_agent("p6-cost"),
                                           store=reg)
        assert out.trace.total_cost_usd > 0
        assert out.trace.total_latency_ms > 0
        assert out.trace.total_steps == sum(
            1 for s in out.trace.spans if s.kind in ("llm_call", "tool_call"))


# --------------------------------------------------------------------------- #
# 4. the single-turn path is untouched
# --------------------------------------------------------------------------- #


class TestTheSingleTurnPathIsUnchanged:
    def test_a_single_shot_payload_never_says_the_customer_is_present(self):
        """The multi-turn keyword must be ABSENT, not ``False``: the payload is
        JSON-dumped into the prompt and into the span input, so an extra key
        would change the bytes of every single-turn run."""
        scn = gating_scenario()
        run = multi_turn_scenario_runner()
        env = _env_for(scn, run)
        assert set(ScenarioAgent.session_input(scn, env)) == {"ticket",
                                                              "customer_id"}
        assert ScenarioAgent.session_input(
            scn, env, customer_present=True)["customer_present"] is True

    def test_the_single_shot_clock_still_ticks_one_second_from_the_epoch(
            self, reg, no_network):
        """The clock grew a stride for conversations. Its default is the one the
        single-turn path has always had, and this is what says so: every
        timestamp is a whole second from the world's zero, the first is one
        second in, and none of the sub-second stride a conversation uses appears
        anywhere.
        """
        scn = gating_scenario()
        out = scenario_runner()(scn, adapter=asking_agent("p6-clock"), store=reg)
        times = [t for s in out.trace.spans for t in (s.start_time, s.end_time)]
        assert times[0] == EPOCH + timedelta(seconds=1)
        assert all((t - EPOCH) % timedelta(seconds=1) == timedelta(0)
                   for t in times)
        assert times == sorted(times)

    def test_a_single_turn_outcome_carries_the_empty_conversation(
            self, reg, no_network):
        """``ScenarioOutcome`` grew fields additively: a ticket run constructs
        the same object it always did."""
        scn = gating_scenario()
        out = scenario_runner()(scn, adapter=asking_agent("p6-empty"), store=reg)
        assert (out.session_id, out.ended) == ("", "")
        assert (out.turns, out.disclosed, out.withheld) == ([], [], [])
        assert out.user_provenance == {} and out.disclosures == []
        assert out.user_turns == 0

    def test_the_stand_in_does_not_ask_when_nobody_is_on_the_line(
            self, reg, no_network):
        """The asking branch is gated on the customer being present, so a queued
        ticket behaves exactly as it did. ``account_change`` is the case that
        would change if it were not: with a customer it asks first, without one
        it goes straight to the account."""
        scn = gating_scenario()
        out = scenario_runner()(scn, adapter=asking_agent("p6-noask"), store=reg)
        assert [s.name for s in out.trace.spans if s.kind == "tool_call"] == \
               ["get_customer", "update_address"]
        assert _ASK_ORDER not in (out.trace.final_output or "")


# --------------------------------------------------------------------------- #
# 5. the stand-in reads the CURRENT turn
# --------------------------------------------------------------------------- #


class TestTheStandInAnswersTheTurnItIsOn:
    def test_a_failure_in_an_earlier_turn_does_not_drive_the_next_one(self):
        """Recovery is about what JUST failed.

        A tool error from turn one is still in the transcript forever. Read
        unscoped, it puts the stand-in into its recovery branch on every
        subsequent turn — so a customer who supplies a working order number on
        turn two gets answered about the failure on turn one, and the plan for
        the turn it is actually on never runs. Driven at the client rather than
        through a world because the two states this distinguishes are states of
        the transcript.
        """
        scn = gating_scenario(intent="refund")
        messages = [
            {"role": "user", "content": json.dumps(
                {"ticket": scn.text, "customer_id": "c-0001",
                 "customer_present": True}, sort_keys=True)},
            {"role": "assistant",
             "content": [SimpleNamespace(type="tool_use", name="lookup_order",
                                         input={"order_id": "o-99999"},
                                         id="tu-0")]},
            {"role": "user", "content": [{"type": "tool_result",
                                          "tool_use_id": "tu-0",
                                          "content": "ERROR: order not found",
                                          "is_error": True}]},
            {"role": "assistant",
             "content": "I can't complete that — the system reported that."},
            {"role": "user", "content": json.dumps(
                {"customer_says": "Sorry — try order o-12345 instead."})},
        ]
        resp = ScriptedSupportClient().messages.create(messages=messages)
        assert resp.stop_reason == "tool_use"
        block = resp.content[0]
        assert block.name == "lookup_order"
        assert block.input["order_id"] == "o-12345"


# --------------------------------------------------------------------------- #
# 6. the edges, disclosed rather than crashed
# --------------------------------------------------------------------------- #


class TestNothingIsDroppedSilently:
    def test_a_turn_that_produced_no_text_is_disclosed(self, reg, no_network):
        """An agent that answers with nothing leaves two customer messages
        adjacent in the transcript — a shape a real client rejects on the next
        turn. Said out loud, and said ON THE TRACE, because the session object is
        thrown away and the trace is what gets stored.
        """
        class Mute:
            def __init__(self):
                self.messages = SimpleNamespace(create=self._create)

            def _create(self, **_kw):
                return SimpleNamespace(
                    stop_reason="end_turn",
                    usage=SimpleNamespace(input_tokens=1, output_tokens=0),
                    content=[SimpleNamespace(type="text", text="")])

        scn = gating_scenario()
        agent = ScenarioAgent(model="silent", client=Mute(), agent_id="p6-silent")
        out = multi_turn_scenario_runner()(scn, adapter=agent, store=reg)
        notes = [d["note"] for d in out.disclosures if d.get("kind") == "session"]
        assert any("produced no text" in n for n in notes)
        assert any(s.kind == "env_step" and s.name == "session_disclosure"
                   for s in out.trace.spans)

    def test_a_counterparty_that_never_speaks_still_produces_a_trace(
            self, reg, no_network):
        """``Session.to_trace`` refuses a run with no spans, and rightly. So the
        fact is RECORDED as what it is — a session in which the agent was never
        given a turn — instead of reaching the caller as an exception it has to
        interpret."""
        from agenttic.scenario.runner import run_multi_turn_scenario
        from agenttic.scenario.user import RecordedUser

        scn = gating_scenario()
        out = run_multi_turn_scenario(
            scn, _env_for(scn, None), adapter=asking_agent("p6-mute3"),
            user=RecordedUser([]))
        assert out.user_turns == 0
        assert out.ended == "record_exhausted"
        assert any(s.kind == "env_step" and s.name == "no_turn_delivered"
                   for s in out.trace.spans)
        assert [s for s in out.trace.spans if s.kind == "llm_call"] == []


# --------------------------------------------------------------------------- #
# 7. the CDV loop can use it without a second executor
# --------------------------------------------------------------------------- #


def test_the_multi_turn_runner_drops_into_the_existing_executor(reg, no_network):
    """``multi_turn_scenario_runner`` is signature-compatible with
    :class:`ScenarioRunner`, which is what "drop-in" has to mean in practice:
    ``harness_executor`` takes the runner as an argument (``ops.cdv_op`` passes
    it through), so closing coverage over conversations needs no second
    executor and no change to ``verification/cdv.py``.

    Asserted end-to-end rather than by reading the signatures — the executor
    also scores, derives the oracle and builds the failure signatures, and a
    ``ScenarioOutcome`` that had grown a shape those paths could not read would
    only show up here.
    """
    from agenttic.certification.mock_provider import MockAnthropicClient
    from agenttic.scenario.runner import harness_executor
    from agenttic.schema.rubric import Criterion, Rubric

    scn = gating_scenario()
    rubric = Rubric(rubric_id="p6-rubric", version=1, criteria=[
        Criterion(criterion_id="helpful", description="Did it help?",
                  scorer="judge", scale="binary",
                  anchors={"pass": "the customer was helped",
                           "fail": "the customer was not helped"})])
    cfg = {"models": {"agent_default": "scripted-support",
                      "judge_strong": "mock-judge", "judge_light": "mock-judge"}}
    execute, runs = harness_executor(
        cfg, reg, asking_agent("p6-exec"), rubric=rubric,
        run_scenario=multi_turn_scenario_runner(), suite_id="p6-suite",
        judge_client=MockAnthropicClient())

    result = execute(scn)
    assert len(runs) == 1
    assert runs[0].outcome.user_turns >= 2
    # The scoring leg really ran. Without this the test would pass just as
    # happily on a scoring outage, which `harness_executor` turns into data —
    # and "the executor accepted the runner" would be proved over a path that
    # gave up before it read the trace.
    assert runs[0].score is not None and runs[0].score.scoring_error is None
    assert run_predicate("session_multi_turn", result.trace) is True
    # The oracle and the state diff still read a conversation's trace: this
    # scenario should change the address, and a run that did is not a failure.
    assert runs[0].outcome.state_diff
    assert [f.key() for f in runs[0].oracle_findings] == []


# --------------------------------------------------------------------------- #
# 8. refusals
# --------------------------------------------------------------------------- #


class TestWhatCannotHoldAConversation:
    def test_an_adapter_with_no_bound_world_is_refused_loudly(self, reg):
        """``AgentAdapter.converse(session)`` takes no environment, so an
        adapter that has not bound one has no world to answer against. Refused,
        not defaulted — a default here would be a conversation against nothing,
        reported as a session that happened."""
        from agenttic.scenario.runner import run_multi_turn_scenario
        scn = gating_scenario()
        run = multi_turn_scenario_runner()
        env = _env_for(scn, run)

        class NoWorld(ScenarioAgent):
            open_conversation = None

        with pytest.raises(ScenarioAgentMisuse) as exc:
            run_multi_turn_scenario(
                scn, env, adapter=NoWorld(model="m", client=MuteClient(),
                                          agent_id="p6-noworld"))
        assert "open_conversation" in str(exc.value)

    def test_a_conversation_still_refuses_a_flattened_scenario(self, reg):
        """The bound object delegates ``run`` to the agent, which refuses. A
        scenario is a world, not a message, on both paths."""
        scn = gating_scenario()
        run = multi_turn_scenario_runner()
        conv = ScenarioConversation(asking_agent("p6-flat"), scn,
                                    _env_for(scn, run))
        with pytest.raises(ScenarioAgentMisuse):
            conv.run({"message": "I want a refund"})

    def test_only_the_bound_object_advertises_sessions(self, reg):
        """``supports_sessions()`` must answer for the thing that can actually
        take a second turn. An unbound ``ScenarioAgent`` cannot — it has no world
        — and advertising otherwise would hand a caller an adapter that raises at
        turn one, which is the failure mode the flag exists to prevent."""
        scn = gating_scenario()
        assert ScenarioAgent.supports_sessions() is False
        assert ScenarioConversation.supports_sessions() is True
        conv = asking_agent("p6-supports").open_conversation(
            scn, _env_for(scn, None))
        assert conv.supports_sessions() is True

    def test_the_bound_conversation_is_the_same_agent(self, reg):
        """Identity is delegated, never restated: a second config hash would be
        a second answer to "which agent produced this trace?"."""
        scn = gating_scenario()
        agent = asking_agent("p6-ident")
        conv = ScenarioConversation(agent, scn,
                                    _env_for(scn, multi_turn_scenario_runner()))
        assert (conv.agent_id, conv.visibility) == (agent.agent_id,
                                                    agent.visibility)
        assert conv.config_hash() == agent.config_hash()
        assert conv.describe() == agent.describe()


def _env_for(scn, _runner):
    """A live world for the tests that need one without a conversation."""
    from agenttic.scenario.env import (
        ScenarioEnvironment, install_scenario_enforcement)
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        reg = Registry(f"{d}/env.db")
        gateway, session = install_scenario_enforcement(reg, "p6-env")
        return ScenarioEnvironment(scn, gateway=gateway,
                                   session_id=session.session_id)
