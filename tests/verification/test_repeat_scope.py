"""A loop detector has to count inside the thing a loop happens in.

``never_repeated_identical_tool_call`` counted identical calls over the whole
trace. On a single-turn run that is the agent's one tool loop and the rule is
right. On a session it stops detecting loops and starts detecting conversations:
a customer who asks the same question in five consecutive turns gets the same
lookup five times, and an agent that answers each one has done nothing wrong.

Measured on the pre-fix build, a 5-turn session of exactly that shape::

    PRE-FIX  (trace-scoped): violation @ span 10
    POST-FIX (turn-scoped) : pass

That was the fourth false violation sessions introduced, and every one of them
spends a ``property_violation`` cap on the shape of the trace rather than on
anything the agent did.
"""

from __future__ import annotations

from agenttic.verification import evaluate

from .conftest import span, trace

REPEAT = "never_repeated_identical_tool_call"


def _v(tr):
    return evaluate(tr, assertion_ids=[REPEAT])[0]


def _asked_five_times():
    """Five turns, one identical lookup each — a conversation, not a loop."""
    spans, i = [], 0
    for _ in range(5):
        spans.append(span("user_turn", "customer", i=i)); i += 1
        spans.append(span("tool_call", "get_order", i=i,
                          input={"order_id": "o-1"})); i += 1
        spans.append(span("final_output", "final_output", i=i)); i += 1
    return trace(*spans)


def test_the_same_question_in_five_turns_is_not_a_loop():
    assert _v(_asked_five_times()).status == "pass"


def test_a_stuck_agent_inside_one_turn_is_still_caught():
    """The property's whole reason for existing. Turn-scoping must not cost it."""
    spans = [span("user_turn", "customer", i=0)] + [
        span("tool_call", "get_order", i=n, input={"order_id": "o-1"})
        for n in range(1, 6)]
    r = _v(trace(*spans))
    assert r.status == "violation"
    assert "get_order" in r.detail


def test_a_single_turn_trace_is_unaffected():
    """No ``user_turn`` span means exactly one turn, so the window is the trace
    and every verdict that existed before this change still holds."""
    spans = [span("tool_call", "get_order", i=n, input={"order_id": "o-1"})
             for n in range(5)]
    assert _v(trace(*spans)).status == "violation"


def test_three_identical_calls_in_a_turn_are_still_within_budget():
    """The limit is *more than* 3, and turn-scoping must not quietly tighten it."""
    spans = [span("user_turn", "customer", i=0)] + [
        span("tool_call", "get_order", i=n, input={"order_id": "o-1"})
        for n in range(1, 4)]
    assert _v(trace(*spans)).status == "pass"


def test_a_run_that_called_no_tools_is_unexercised():
    assert _v(trace(span("final_output", "final_output", i=0))).status == "unexercised"


def test_turns_that_call_no_tools_never_fold_into_a_pass():
    """The vacuity rule one level up: a session where no turn ever called a tool
    has proved nothing, and 'no turn violated it' is not evidence."""
    spans = [span("user_turn", "customer", i=0),
             span("final_output", "final_output", i=1),
             span("user_turn", "customer", i=2),
             span("final_output", "final_output", i=3)]
    assert _v(trace(*spans)).status == "unexercised"


def test_the_violation_names_the_turn_it_happened_in():
    spans = [span("user_turn", "customer", i=0),
             span("final_output", "final_output", i=1),
             span("user_turn", "customer", i=2)] + [
        span("tool_call", "get_order", i=n, input={"order_id": "o-1"})
        for n in range(3, 8)]
    r = _v(trace(*spans))
    assert r.status == "violation"
    assert "turn 2/2" in r.detail, r.detail
