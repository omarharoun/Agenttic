"""What `session_shape` is allowed to claim.

The product's whole claim is an honest account of what a run never exercised. A
coverpoint that *over*-reports is therefore worse than one that reports nothing:
it converts an untested situation into a covered one, silently, and every
closure figure downstream inherits the lie.

`session_shape` did exactly that. `_turns()` counted `llm_call` spans, so an
agent that received ONE human message and made three tool calls — one model call
per tool-use iteration — was recorded as `multi_turn`. That is multi-*step*.
Nobody spoke twice.

These tests pin the distinction. There were no tests over any `session_*`
predicate before this file, which is how the mislabel survived.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from agenttic.coverage.extractors import run_predicate
from agenttic.schema.trace import Span, Trace

T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)


def span(kind, name, *, i=0, attributes=None):
    return Span(span_id=f"s{i}", kind=kind, name=name,
                start_time=T0 + timedelta(seconds=i),
                end_time=T0 + timedelta(seconds=i + 1),
                attributes=attributes or {})


def trace(*spans, final_output="done"):
    fixed = [s.model_copy(update={"span_id": f"s{i}"}) for i, s in enumerate(spans)]
    return Trace(trace_id="t", agent_id="a", agent_config_hash="c",
                 test_case_id="case", spans=fixed, visibility="glass_box",
                 final_output=final_output)


def one_human_message_three_tool_calls() -> Trace:
    """A single-shot run against the reference agent, as it really looks.

    `AnthropicSimpleAgent.run` seeds exactly one user message and then loops:
    each iteration emits an `llm_call` span and, while the model keeps asking for
    tools, a `tool_call` span. Three tools means four model calls. One human
    turn."""
    return trace(
        span("llm_call", "messages.create", i=0),
        span("tool_call", "lookup_kb", i=1),
        span("llm_call", "messages.create", i=2),
        span("tool_call", "calculator", i=3),
        span("llm_call", "messages.create", i=4),
        span("tool_call", "lookup_kb", i=5),
        span("llm_call", "messages.create", i=6),
        span("final_output", "answer", i=7),
    )


class TestAgentStepsIsNotSessionShape:
    def test_tool_loop_is_multi_step(self):
        """Four model calls in one exchange is multi-step. That much is true."""
        t = one_human_message_three_tool_calls()
        assert run_predicate("agent_steps_multi", t) is True
        assert run_predicate("agent_steps_single", t) is False

    def test_tool_loop_is_not_multi_turn(self):
        """...and it is NOT multi-turn. Nobody spoke twice.

        This is the assertion the old `session_multi_turn` failed: it counted
        `llm_call` spans and credited multi-turn coverage to a single exchange.
        """
        t = one_human_message_three_tool_calls()
        assert run_predicate("session_multi_turn", t) is False

    def test_single_exchange_is_single_turn(self):
        t = one_human_message_three_tool_calls()
        assert run_predicate("session_single_turn", t) is True

    def test_a_direct_answer_is_single_turn_and_single_step(self):
        t = trace(span("llm_call", "messages.create", i=0),
                  span("final_output", "answer", i=1))
        assert run_predicate("session_single_turn", t) is True
        assert run_predicate("agent_steps_single", t) is True
        assert run_predicate("agent_steps_multi", t) is False


class TestRealTurnsCount:
    def test_two_human_turns_is_multi_turn(self):
        """A second human message — the only thing that makes a session."""
        t = trace(
            span("user_turn", "customer", i=0),
            span("llm_call", "messages.create", i=1),
            span("user_turn", "customer", i=2),
            span("llm_call", "messages.create", i=3),
            span("final_output", "answer", i=4),
        )
        assert run_predicate("session_multi_turn", t) is True
        assert run_predicate("session_single_turn", t) is False

    def test_one_human_turn_with_many_steps_stays_single_turn(self):
        """The two axes are independent: many steps, one turn."""
        t = trace(
            span("user_turn", "customer", i=0),
            span("llm_call", "messages.create", i=1),
            span("tool_call", "lookup_kb", i=2),
            span("llm_call", "messages.create", i=3),
            span("final_output", "answer", i=4),
        )
        assert run_predicate("session_single_turn", t) is True
        assert run_predicate("agent_steps_multi", t) is True


class TestResumedWithMemory:
    def test_span_name_alone_does_not_prove_resumption(self):
        """A tool that happens to be *called* `memory_lookup` is not evidence
        that a session was resumed against prior state.

        The old predicate substring-matched span names for "memory"/"resume",
        so any agent with a memory tool silently claimed the bin — and, because
        `session_multi_turn` excluded resumed traces, silently lost the turn
        bins too."""
        t = trace(
            span("user_turn", "customer", i=0),
            span("llm_call", "messages.create", i=1),
            span("tool_call", "memory_lookup", i=2),
            span("final_output", "answer", i=3),
        )
        assert run_predicate("session_resumed_with_memory", t) is False
        assert run_predicate("session_single_turn", t) is True

    def test_declared_resumption_is_honoured(self):
        """The harness declares it explicitly, or it did not happen."""
        t = trace(
            span("user_turn", "customer", i=0, attributes={"resumed": True}),
            span("llm_call", "messages.create", i=1),
            span("final_output", "answer", i=2),
        )
        assert run_predicate("session_resumed_with_memory", t) is True


class TestSessionShapeReasoningPinned:
    """F5 (do-not-regress): the session_shape coverpoint must keep REFUSING to
    credit single_turn on an uninstrumented trace — crediting it 'would turn
    missing instrumentation into a result' — and resumed_with_memory must stay
    waived with its reason recorded inline. This is the platform's clearest
    demonstration of its own thesis; it must survive refactors."""

    def test_single_turn_not_credited_at_zero_reasoning(self):
        from agenttic.coverage.models.conversational_transactional import SESSION_SHAPE
        assert SESSION_SHAPE.measurable is False
        reason = SESSION_SHAPE.not_measurable_reason.lower()
        assert "missing instrumentation into a result" in reason
        assert "true at zero" in reason  # session_single_turn is <= 1, True at 0

    def test_resumed_with_memory_waiver_reason_inline(self):
        from agenttic.coverage.models.conversational_transactional import SESSION_SHAPE
        bin_ = next(b for b in SESSION_SHAPE.bins if b.bin_id == "resumed_with_memory")
        assert bin_.waived is True
        assert (bin_.reason or "").strip()  # the waiver reason travels inline
