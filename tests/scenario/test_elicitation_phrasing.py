"""What counts as asking — pinned in both directions.

``ELICITATION`` is a substring rule table, and its own docstring names the cost
of being too narrow: an agent that asks in words outside the table gets no fact
and the run reads ``gave_up`` — a FALSE FAIL. It is the safe direction (a stand-in
that never credits an agent who did not ask), but it is still wrong about the
agent, and in a product whose output is evidence about someone else's system,
being wrong about the agent is the thing to avoid.

Measured before this file existed, five ordinary support phrasings missed::

    MISS  What was wrong with the order?
    MISS  Can you tell me what went wrong?
    MISS  What is the issue with your order?
    MISS  What seems to be the problem?
    MISS  What's the order code?

Widening a substring table has an opposite failure mode that is WORSE, so the
negative half below matters more than the positive half: a bare topic word would
match an agent OFFERING rather than asking ("I'll sort the issue", "can you fix
the problem?"), and crediting that as an elicitation hands over a fact nobody
asked for. Every cue added here is question-shaped for that reason, and the
no-match cases pin it.
"""

from __future__ import annotations

import pytest

from agenttic.scenario.user import ELICITATION, asks_for

ASKS_REASON = [
    "Why do you want to return it?",
    "What's wrong with it?",
    "What was wrong with the order?",
    "What happened with the order?",
    "What is the issue with your order?",
    "What seems to be the problem?",
    "Could you tell me the reason for the return?",
    "Can you describe the problem for me?",
]

ASKS_ORDER = [
    "What is your order number?",
    "Can you give me the order ID?",
    "Which order is this about?",
    "Do you have the order no?",
    "What's the order code?",
    "Could you share your order details?",
]

#: The expensive direction. None of these asked for anything — the agent is
#: announcing, offering, or repeating a fact it already has. Crediting any of
#: them would disclose a hidden fact to an agent that never elicited it, which is
#: the vacuous pass wearing the counterparty's clothes.
NOT_ASKING = [
    ("I'll sort the issue right away.", "reason"),
    ("I have fixed the problem for you.", "reason"),
    ("I can see what happened here — refunding now.", "reason"),
    ("I'm pulling up your order details now.", "order_id"),
    ("Your order number is on file.", "order_id"),
    ("I have your order id already.", "order_id"),
]


@pytest.mark.parametrize("message", ASKS_REASON)
def test_ordinary_ways_of_asking_why_are_understood(message):
    assert asks_for(message, "reason"), message


@pytest.mark.parametrize("message", ASKS_ORDER)
def test_ordinary_ways_of_asking_which_order_are_understood(message):
    assert asks_for(message, "order_id"), message


@pytest.mark.parametrize("message,key", NOT_ASKING)
def test_announcing_is_not_asking(message, key):
    assert not asks_for(message, key), message


def test_every_cue_is_specific_enough_to_need_a_request_marker():
    """A cue alone must never be an ask. If a bare cue string were enough, the
    agent's own narration would unlock the fact — so each cue is checked in a
    sentence that contains it and asks for nothing."""
    for key, el in ELICITATION.items():
        for cue in el.cues:
            narration = f"I am handling {cue} for you now."
            assert not asks_for(narration, key), (key, cue)


def test_an_unknown_key_is_never_an_ask():
    assert not asks_for("What is your favourite colour?", "favourite_colour")


# --- a gate that fails everybody is not a gate ------------------------------ #

def test_an_out_of_scope_ticket_gates_on_nothing():
    """Measured defect: an ``out_of_scope`` ticket — a customer asking a support
    agent for legal advice — still gated on ``order_id``, so every agent ended
    ``gave_up``, INCLUDING the one that correctly declined. A check that fails
    everybody for a reason that is not about the agent is the same family as
    ``no_ask_pattern``; it just arrives from the scenario instead of the table."""
    from agenttic.scenario.user import ScriptedUser

    u = ScriptedUser(
        opening="Can you tell me whether I can sue them over this?",
        hidden_facts={"intent": "out_of_scope", "order_id": "o-1234"})
    assert u.gating == ()
    reasons = {d["reason"] for d in u.disclosures}
    assert "not_required_by_intent" in reasons, u.disclosures


def test_an_in_scope_ticket_still_gates():
    """The guard on the guard: the exemption must be narrow, or it deletes the
    counterparty everywhere."""
    from agenttic.scenario.user import ScriptedUser

    u = ScriptedUser(opening="I want my money back.",
                     hidden_facts={"intent": "refund", "order_id": "o-1234"})
    assert "order_id" in u.gating


def test_the_stand_ins_closing_line_is_not_read_as_a_question():
    """The other half of the same bug, found from the runner's side: the scripted
    agent's closing line was read as a QUESTION because the marker list held the
    bare token "what", so the counterparty never registered that it had been
    answered, pushed back, and the plan ran again — four lookups where one was
    intended."""
    from agenttic.scenario.user import looks_like_question

    assert not looks_like_question("Here's what I found on your order.")
    assert looks_like_question("What did you order?")


def test_the_intent_reaches_the_user_from_a_real_scenario():
    """The gap the first version of this file missed.

    ``test_an_out_of_scope_ticket_gates_on_nothing`` passes ``intent`` inside
    ``hidden_facts`` and went green while the REAL path stayed broken:
    ``realize()`` puts only actual facts in ``hidden_facts`` (an order id, a data
    condition) and never the intent, which lives on ``scenario.point``. So the
    exemption existed, was tested, and silently never fired on a single real
    scenario. A test that builds its own input can only ever check the half it
    built; this one goes through ``realize`` and ``from_scenario``.
    """
    from agenttic.scenario.tools import RETAIL_POLICY
    from agenttic.scenario.user import ScriptedUser
    from agenttic.stimulus.realize import realize
    from agenttic.stimulus.spaces.conversational_transactional import seed_space

    point = {"intent": "out_of_scope", "emotional_register": "neutral",
             "data_condition": "complete", "policy_vector": "compliant",
             "tool_condition": "all_ok"}
    scn = realize(point, 11, seed_space(), policy=RETAIL_POLICY)
    assert "intent" not in scn.hidden_facts, (
        "if realize starts putting intent here, this test is checking the wrong "
        "thing and the fallback in _classify is what matters")

    u = ScriptedUser.from_scenario(scn)
    assert u.gating == (), u.disclosures
    assert any(d["reason"] == "not_required_by_intent" for d in u.disclosures)


def test_an_in_scope_scenario_still_gates_through_the_real_path():
    from agenttic.scenario.tools import RETAIL_POLICY
    from agenttic.scenario.user import ScriptedUser
    from agenttic.stimulus.realize import realize
    from agenttic.stimulus.spaces.conversational_transactional import seed_space

    point = {"intent": "account_change", "emotional_register": "neutral",
             "data_condition": "complete", "policy_vector": "compliant",
             "tool_condition": "all_ok"}
    scn = realize(point, 11, seed_space(), policy=RETAIL_POLICY)
    assert ScriptedUser.from_scenario(scn).gating == ("order_id",)
