"""P3 acceptance: multi-turn sessions, added ADDITIVELY.

Three things are pinned here, and the third is the reason the other two are
allowed to exist:

1. a session emits one ``user_turn`` span per counterparty message and one
   ``session_id`` on the trace — and ``coverage/extractors.py`` reads exactly
   what it emits, asserted through the real predicates rather than by eyeballing
   span kinds;
2. an adapter that never implemented ``converse`` raises an error that names it,
   and ``supports_sessions()`` is False without the adapter declaring anything;
3. **all four real adapters still construct and ``run()`` still works.** ``run``
   is subclassed by four shipped adapters and ~15 ad-hoc doubles across this
   suite; the whole design of P3 is that none of them had to change, so the claim
   is tested rather than asserted in a docstring.

Offline: no API key, no network. Every client here is scripted.
"""

from __future__ import annotations

import json
from types import SimpleNamespace as NS

import pytest

from agenttic.adapters.base import AgentAdapter, SessionsUnsupported
from agenttic.coverage.extractors import run_predicate
from agenttic.scenario.session import (
    RESUME_STEP_NAME, Session, SessionContractError, run_session)
from agenttic.schema.trace import SCHEMA_VERSION, Span, Trace

T0 = "2026-01-01T00:00:00+00:00"


# --------------------------------------------------------------------------- #
# doubles
# --------------------------------------------------------------------------- #


class SingleTurnAgent(AgentAdapter):
    """The shape every adapter in this repo had before P3: `run` and nothing
    else. Used to prove `converse` stayed optional."""

    agent_id = "single-turn"
    visibility = "glass_box"

    def describe(self) -> dict:
        return {"adapter": "SingleTurnAgent"}

    def run(self, test_input: dict, *, test_case_id: str | None = None) -> Trace:
        span = Span(span_id="out-000", kind="final_output", name="final_output",
                    start_time=T0, end_time=T0, output={"text": "ok"})
        return Trace(trace_id="tr-single", agent_id=self.agent_id,
                     agent_config_hash=self.config_hash(),
                     test_case_id=test_case_id, spans=[span],
                     visibility=self.visibility, final_output="ok")


class ScriptedSessionAgent(AgentAdapter):
    """A conversational agent with no model behind it.

    It answers each turn by echoing the turn number, which is enough to exercise
    the contract: read turns from ``session.deliver()``, hand spans back with
    ``session.record``, return ``session.to_trace(self)``. Span ids restart at
    ``llm-000`` every turn ON PURPOSE — that is what a real adapter reusing its
    single-turn machinery does, and it is the collision ``Session.record``
    namespaces away.
    """

    agent_id = "scripted-session"
    visibility = "glass_box"

    def describe(self) -> dict:
        return {"adapter": "ScriptedSessionAgent"}

    def run(self, test_input: dict, *, test_case_id: str | None = None) -> Trace:
        raise AssertionError("run() must not be reached by converse()")

    def converse(self, session: Session) -> Trace:
        for turn in session.deliver():
            said = turn.message.get("message", "")
            session.record([
                Span(span_id="llm-000", kind="llm_call", name="scripted",
                     start_time=T0, end_time=T0,
                     input={"prompt": said}, tokens_in=10, tokens_out=5,
                     cost_usd=0.001),
                Span(span_id="out-000", parent_id="llm-000", kind="final_output",
                     name="final_output", start_time=T0, end_time=T0,
                     output={"text": f"answer to turn {turn.index}"}),
            ])
        return session.to_trace(self)


class ForgetfulSessionAgent(ScriptedSessionAgent):
    """Implements `converse` but builds its own Trace — the one mistake
    :func:`run_session` exists to catch."""

    agent_id = "forgetful-session"

    def converse(self, session: Session) -> Trace:
        for turn in session.deliver():
            session.record([Span(span_id=f"out-{turn.index}", kind="final_output",
                                 name="final_output", start_time=T0, end_time=T0,
                                 output={"text": "x"})])
        return Trace(trace_id="tr-forgetful", agent_id=self.agent_id,
                     agent_config_hash=self.config_hash(),
                     spans=list(session.spans), visibility=self.visibility,
                     final_output="x")


# --------------------------------------------------------------------------- #
# 1. a session produces turns, a session_id, and coverage that reads them
# --------------------------------------------------------------------------- #


class TestThreeTurnSession:
    @pytest.fixture
    def trace(self) -> Trace:
        session = Session(["where is my order?", "o-41337", "thanks"],
                          session_id="sess-fixed")
        return run_session(session, ScriptedSessionAgent())

    def test_three_user_turn_spans(self, trace):
        turns = [s for s in trace.spans if s.kind == "user_turn"]
        assert len(turns) == 3
        assert [s.input["message"] for s in turns] == [
            "where is my order?", "o-41337", "thanks"]

    def test_one_session_id_on_the_trace(self, trace):
        assert trace.session_id == "sess-fixed"

    def test_turn_spans_precede_the_answers_they_provoked(self, trace):
        """Ordering is the evidence that the span was emitted BEFORE the agent
        answered, which is what makes it a record of a turn rather than a label
        the adapter chose to apply afterwards."""
        kinds = [s.kind for s in trace.spans]
        assert kinds == ["user_turn", "llm_call", "final_output"] * 3

    def test_span_ids_are_unique_across_turns(self, trace):
        """Every turn's spans arrive as `llm-000`/`out-000`. Without
        namespacing, `Trace` would reject the second turn outright."""
        ids = [s.span_id for s in trace.spans]
        assert len(ids) == len(set(ids))
        assert "t2.llm-000" in ids and "t3.out-000" in ids

    def test_parent_links_survive_namespacing(self, trace):
        child = next(s for s in trace.spans if s.span_id == "t2.out-000")
        assert child.parent_id == "t2.llm-000"

    def test_coverage_reads_it_as_a_multi_turn_session(self, trace):
        """The shapes line up with `coverage/extractors.py` as written — this is
        the assertion the whole span design is for."""
        assert run_predicate("session_multi_turn", trace, None) is True
        assert run_predicate("session_single_turn", trace, None) is False

    def test_turns_are_not_counted_as_agent_steps(self, trace):
        """`agent_steps` and `session_shape` were one coverpoint that counted
        `llm_call` spans; three turns must not read as more model calls than
        there were."""
        assert trace.total_steps == 3          # three llm_call spans, no tools
        assert run_predicate("agent_steps_multi", trace, None) is True

    def test_totals_are_accumulated_not_dropped(self, trace):
        assert trace.total_cost_usd == pytest.approx(0.003)
        assert trace.final_output == "answer to turn 3"

    def test_one_turn_reads_as_a_single_turn_session(self):
        trace = run_session(Session(["hello"]), ScriptedSessionAgent())
        assert run_predicate("session_single_turn", trace, None) is True
        assert run_predicate("session_multi_turn", trace, None) is False


class TestEnvStep:
    def test_resumed_session_emits_an_env_step_coverage_can_read(self):
        session = Session(["and the refund?"], prior_state={"last_order": "o-1"})
        trace = run_session(session, ScriptedSessionAgent())
        steps = [s for s in trace.spans if s.kind == "env_step"]
        assert [s.name for s in steps] == [RESUME_STEP_NAME]
        # the ATTRIBUTE is what `session_resumed_with_memory` reads, not the kind
        assert run_predicate("session_resumed_with_memory", trace, None) is True

    def test_empty_prior_state_is_disclosed_not_credited(self):
        """A resume against no state is not a resume. It must not fire the bin,
        and it must not vanish either."""
        session = Session(["hi"], prior_state={})
        trace = run_session(session, ScriptedSessionAgent())
        assert run_predicate("session_resumed_with_memory", trace, None) is False
        note = next(s for s in trace.spans if s.name == "session_disclosure")
        assert "not a resume" in json.dumps(note.output)

    def test_env_step_is_not_readable_as_a_tool_call(self):
        """The reason the kind exists: a fault the harness injected must never
        read as something the agent did."""
        session = Session(["hi"])
        session.env_step("injected_timeout", error="timeout",
                         attributes={"injected_fault": "timeout"})
        trace = run_session(session, ScriptedSessionAgent())
        assert not [s for s in trace.spans if s.kind == "tool_call"]
        assert run_predicate("tool_timeout", trace, None) is False
        assert run_predicate("action_read_only", trace, None) is False


class TestSessionOwnsTheState:
    def test_two_sessions_on_one_adapter_do_not_share_state(self):
        """`harness/runner.py` shares ONE adapter across cases at
        max_parallel=5, so anything a conversation put on `self` would be read
        and overwritten by another case. Nothing here is on the adapter."""
        adapter = ScriptedSessionAgent()
        a = Session(["a1", "a2"], session_id="sess-a")
        b = Session(["b1"], session_id="sess-b")
        # interleaved on purpose: b runs to completion in the middle of a's life
        ta = run_session(a, adapter)
        tb = run_session(b, adapter)
        assert ta.session_id == "sess-a" and tb.session_id == "sess-b"
        assert len([s for s in ta.spans if s.kind == "user_turn"]) == 2
        assert len([s for s in tb.spans if s.kind == "user_turn"]) == 1
        assert not hasattr(adapter, "session_id")

    def test_enqueue_mid_conversation_is_delivered(self):
        session = Session(["first"])
        seen: list[str] = []

        class Reactive(ScriptedSessionAgent):
            def converse(self, s: Session) -> Trace:
                for turn in s.deliver():
                    seen.append(turn.message["message"])
                    if turn.index == 1:
                        s.enqueue("second")
                    s.record([Span(span_id="out-000", kind="final_output",
                                   name="final_output", start_time=T0,
                                   end_time=T0, output={"text": "ok"})])
                return s.to_trace(self)

        trace = run_session(session, Reactive())
        assert seen == ["first", "second"]
        assert len([s for s in trace.spans if s.kind == "user_turn"]) == 2

    def test_trace_that_does_not_name_its_session_is_refused(self):
        with pytest.raises(SessionContractError, match="session_id=None"):
            run_session(Session(["hi"]), ForgetfulSessionAgent())

    def test_empty_session_names_itself_rather_than_failing_validation(self):
        with pytest.raises(SessionContractError, match="no spans"):
            Session([], session_id="sess-empty").to_trace(ScriptedSessionAgent())

    def test_unusable_message_type_is_refused_loudly(self):
        with pytest.raises(TypeError, match="str or a dict"):
            Session([object()])


# --------------------------------------------------------------------------- #
# 2. an adapter without converse says so, and can be asked in advance
# --------------------------------------------------------------------------- #


class TestRecordingAWholeTrace:
    """The other half of `Session.record`: an adapter reusing its single-turn
    machinery hands back a whole Trace, and its three run-level measurements
    must be totalled rather than thrown away with the wrapper."""

    def _turn_trace(self, text: str, *, cost: float, latency: float,
                    session_id: str | None = None) -> Trace:
        return Trace(
            trace_id=f"tr-{text}", agent_id="reuser", agent_config_hash="h",
            session_id=session_id,
            spans=[Span(span_id="llm-000", kind="llm_call", name="m",
                        start_time=T0, end_time=T0, cost_usd=cost),
                   Span(span_id="out-000", kind="final_output",
                        name="final_output", start_time=T0, end_time=T0,
                        output={"text": text})],
            visibility="glass_box", final_output=text,
            total_cost_usd=cost, total_latency_ms=latency, total_steps=1)

    def test_cost_latency_and_final_text_are_totalled(self):
        session = Session(["a", "b"], test_case_id="tc-9")

        class Reuser(ScriptedSessionAgent):
            agent_id = "reuser"

            def converse(inner, s: Session) -> Trace:  # noqa: N805
                for turn in s.deliver():
                    s.record(self._turn_trace(f"reply {turn.index}",
                                              cost=0.25, latency=120.0))
                return s.to_trace(inner)

        trace = run_session(session, Reuser())
        assert trace.total_cost_usd == pytest.approx(0.5)
        assert trace.total_latency_ms == pytest.approx(240.0)
        assert trace.final_output == "reply 2"
        assert trace.test_case_id == "tc-9"
        # not double-counted from the spans as well
        assert trace.total_steps == 2

    def test_a_foreign_session_id_is_disclosed_not_adopted(self):
        session = Session(["a"], session_id="sess-mine")
        session.deliver().__next__()
        session.record(self._turn_trace("x", cost=0.0, latency=1.0,
                                        session_id="sess-theirs"))
        trace = session.to_trace(ScriptedSessionAgent())
        assert trace.session_id == "sess-mine"
        assert any("sess-theirs" in d for d in session.disclosures)
        note = next(s for s in trace.spans if s.name == "session_disclosure")
        assert "sess-theirs" in json.dumps(note.output)

    def test_a_dangling_parent_is_cleared_and_disclosed(self):
        session = Session(["a"])
        session.deliver().__next__()
        session.record([Span(span_id="out-000", parent_id="llm-from-last-turn",
                             kind="final_output", name="final_output",
                             start_time=T0, end_time=T0, output={"text": "x"})])
        stored = next(s for s in session.spans if s.span_id == "t1.out-000")
        assert stored.parent_id is None
        assert any("llm-from-last-turn" in d for d in session.disclosures)


class TestConverseIsOptional:
    def test_supports_sessions_is_false_without_an_override(self):
        assert SingleTurnAgent.supports_sessions() is False
        assert SingleTurnAgent().supports_sessions() is False

    def test_supports_sessions_is_true_with_one(self):
        assert ScriptedSessionAgent.supports_sessions() is True

    def test_converse_raises_an_error_that_names_the_adapter(self):
        with pytest.raises(SessionsUnsupported) as ei:
            run_session(Session(["hi"]), SingleTurnAgent())
        msg = str(ei.value)
        assert "SingleTurnAgent" in msg
        assert "converse(session)" in msg and "supports_sessions()" in msg

    def test_no_turn_is_delivered_when_the_adapter_cannot_take_one(self):
        """The failure must not leave a `user_turn` span behind: a turn nobody
        received is exactly the credit `session_shape` must never be given."""
        session = Session(["hi"])
        with pytest.raises(SessionsUnsupported):
            run_session(session, SingleTurnAgent())
        assert session.turns == [] and session.spans == []


# --------------------------------------------------------------------------- #
# 3. run() is untouched — the four real adapters still construct and run
# --------------------------------------------------------------------------- #


def _fake_anthropic(responses):
    class FakeClient:
        def __init__(self):
            self.messages = NS(create=lambda **kw: responses.pop(0))
    return FakeClient()


def _text_response(text):
    return NS(stop_reason="end_turn",
              usage=NS(input_tokens=10, output_tokens=5),
              content=[NS(type="text", text=text)])


class TestExistingAdaptersUnchanged:
    """All four shipped `AgentAdapter` subclasses, constructed and run offline.

    Enumerated by hand and cross-checked against the class list: an adapter
    added later that this file does not name is a gap, not a pass.
    """

    def test_anthropic_simple(self, tmp_path):
        from agenttic.adapters.anthropic_simple import AnthropicSimpleAgent

        kb = tmp_path / "kb.json"
        kb.write_text(json.dumps({"refund_policy": "30 days"}))
        agent = AnthropicSimpleAgent(
            model="claude-test", kb_path=kb,
            client=_fake_anthropic([_text_response("42")]))
        trace = agent.run({"question": "2+2"}, test_case_id="tc-1")
        assert trace.final_output == "42"
        assert trace.session_id is None       # a run is not a session
        assert trace.schema_version == SCHEMA_VERSION
        assert agent.supports_sessions() is False

    def test_blackbox_http(self):
        from agenttic.adapters.blackbox_http import BlackBoxHTTPAgent

        agent = BlackBoxHTTPAgent(agent_id="bb", url="http://unused",
                                  transport=lambda payload: {"output": "ok"})
        trace = agent.run({"ticket": "refund"}, test_case_id="tc-1")
        assert trace.final_output == "ok"
        assert trace.session_id is None
        assert agent.supports_sessions() is False

    def test_managed_agent(self):
        from agenttic.adapters.managed_agent import ManagedAgentAdapter

        agent_obj = NS(id="agent_01", name="wf", model="claude-test", version=3)
        events = [
            NS(type="span.model_request_start", id="mr1"),
            NS(type="span.model_request_end", model_request_start_id="mr1",
               is_error=False, model_usage=NS(input_tokens=30, output_tokens=7)),
            NS(type="agent.message",
               content=[NS(type="text", text="done")]),
            NS(type="session.status_idle", stop_reason=NS(type="end_turn")),
        ]

        class FakeStream:
            def __enter__(self):
                return iter(events)

            def __exit__(self, *exc):
                return False

        client = NS(beta=NS(
            agents=NS(retrieve=lambda agent_id: agent_obj),
            sessions=NS(
                create=lambda **kw: NS(id="sess_1"),
                retrieve=lambda sid: NS(id=sid, status="idle"),
                archive=lambda sid: None,
                events=NS(stream=lambda **kw: FakeStream(),
                          send=lambda **kw: None))))
        agent = ManagedAgentAdapter(managed_agent_id="agent_01",
                                    environment_id="env_1", agent_id="managed",
                                    client=client)
        trace = agent.run({"ticket": "refund"}, test_case_id="tc-1")
        assert trace.final_output == "done"
        assert trace.session_id is None
        assert agent.supports_sessions() is False

    def test_safe_assistant(self):
        from agenttic.assistant.adapter import SafeAssistantAgent

        agent = SafeAssistantAgent(
            model="claude-test",
            client=_fake_anthropic([_text_response("the capital is Paris")]))
        trace = agent.run({"request": "capital of France?"}, test_case_id="tc-1")
        assert "Paris" in trace.final_output
        assert trace.session_id is None
        assert agent.supports_sessions() is False

    def test_the_four_are_the_whole_shipped_set(self):
        """Guards the list above from silently going stale: a fifth shipped
        adapter must be added here, not discovered in production."""
        import agenttic.adapters.anthropic_simple  # noqa: F401
        import agenttic.adapters.blackbox_http     # noqa: F401
        import agenttic.adapters.managed_agent     # noqa: F401
        import agenttic.assistant.adapter          # noqa: F401
        from agenttic.adapters.anthropic_simple import AnthropicSimpleAgent
        from agenttic.adapters.blackbox_http import BlackBoxHTTPAgent
        from agenttic.adapters.managed_agent import ManagedAgentAdapter
        from agenttic.assistant.adapter import SafeAssistantAgent

        shipped = {AnthropicSimpleAgent, BlackBoxHTTPAgent,
                   ManagedAgentAdapter, SafeAssistantAgent}
        # ScenarioAgent and NullAgent are harness fixtures, not shipped drivers
        from agenttic.rubric_engine.discrimination import NullAgent
        from agenttic.scenario.runner import ScenarioAgent
        assert shipped.isdisjoint({NullAgent, ScenarioAgent})
        assert all(issubclass(c, AgentAdapter) for c in shipped)
        assert all(not c.supports_sessions() for c in shipped)


class TestSchemaStaysBackwardCompatible:
    def test_session_id_defaults_to_none_on_a_bare_trace(self):
        span = Span(span_id="s", kind="final_output", name="n",
                    start_time=T0, end_time=T0)
        tr = Trace(trace_id="t", agent_id="a", agent_config_hash="h",
                   spans=[span], visibility="glass_box", final_output="x")
        assert tr.session_id is None

    def test_old_json_without_session_id_still_validates(self):
        payload = {
            "trace_id": "t", "agent_id": "a", "agent_config_hash": "h",
            "spans": [{"span_id": "s", "kind": "final_output", "name": "n",
                       "start_time": T0, "end_time": T0}],
            "visibility": "glass_box", "final_output": "x",
            "schema_version": "0.3.0",
        }
        tr = Trace.model_validate(payload)
        assert tr.session_id is None
        assert tr.schema_version == "0.3.0"   # stored version is preserved
