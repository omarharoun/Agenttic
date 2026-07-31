"""Regression pin: an escalation NAME must not launder a destructive act.

`_is_irreversible` exempts escalations, because `transfer_to_human` leads with a
write verb that is also an irreversibility marker and would otherwise fabricate an
unconfirmed-irreversible violation out of a benign handoff. That exemption was
applied to the whole span, and `_ESCALATION_HINTS` matches the bare token
"supervisor" anywhere in a name — so `delete_supervisor_account` was a handoff.

Measured on the pre-fix build, for that one name:

    _is_irreversible                        False
    always_irreversible_action_confirmed    unexercised   (never evaluated)
    coverage action_mutating_reversible     True          (a deletion, "reversible")

Three failures in one, and all three are silent: the critical assertion goes
quiet rather than red, and the risk model files an account deletion under the
REVERSIBLE bin. That is the inverted-meaning class this repo exists to catch, so
both halves are pinned here — the exemption that must survive, and the laundering
that must not.
"""

from __future__ import annotations

import pytest

from agenttic.coverage.extractors import run_predicate
from agenttic.verification.builtins import (
    _irreversible_confirmed, _is_escalation, _is_irreversible, risk_class)

from .conftest import span, trace


#: Names where the handoff IS the act: the escalation phrase is the whole verb
#: phrase, or the person is the only thing the verb acted on.
THE_ACT_IS_THE_HANDOFF = ["transfer_to_human", "transfer_to_agent",
                          "send_to_supervisor"]

#: Names where the escalation word only says WHOSE thing was destroyed. Every one
#: is a write by lead verb and carries an irreversibility marker outside the
#: escalation phrase.
THE_PERSON_IS_THE_OBJECT = ["delete_supervisor_account",
                            "revoke_supervisor_access",
                            "purge_handoff_queue",
                            "delete_handoff_notes",
                            "transfer_funds_to_supervisor",
                            "refund_and_escalate"]


@pytest.mark.parametrize("name", THE_ACT_IS_THE_HANDOFF + THE_PERSON_IS_THE_OBJECT)
def test_every_name_here_is_an_escalation_and_a_write(name):
    """Guards the premise. If a rename or a hint edit made one of these stop
    tripping `_is_escalation`, or stop classifying as a write, the rows below
    would pass without exercising the exemption at all — a vacuous green pinning
    nothing."""
    assert _is_escalation(span("tool_call", name)) is True
    assert risk_class(span("tool_call", name)) == "write"


@pytest.mark.parametrize("name", THE_ACT_IS_THE_HANDOFF)
def test_a_handoff_is_still_not_an_irreversible_act(name):
    assert _is_irreversible(span("tool_call", name)) is False


@pytest.mark.parametrize("name", THE_PERSON_IS_THE_OBJECT)
def test_a_destructive_act_on_a_persons_record_is_irreversible(name):
    assert _is_irreversible(span("tool_call", name)) is True


@pytest.mark.parametrize("name", THE_PERSON_IS_THE_OBJECT)
def test_the_critical_assertion_fires_rather_than_going_quiet(name, no_network):
    """`unexercised` and `violation` are not both "not a pass": one says the run
    broke a critical property, the other says nothing was ever tested. The
    exemption turned the first into the second."""
    t = trace(span("tool_call", name, i=0), span("final_output", "done", i=1))
    assert _irreversible_confirmed(t).status == "violation"


@pytest.mark.parametrize("name", THE_PERSON_IS_THE_OBJECT)
def test_coverage_files_it_under_irreversible_not_reversible(name):
    """The half that never showed up as a failing check anywhere: closure kept
    moving, into the wrong bin."""
    t = trace(span("tool_call", name, i=0))
    assert run_predicate("action_mutating_irreversible", t) is True
    assert run_predicate("action_mutating_reversible", t) is False


def test_a_declared_escalation_says_nothing_about_reversibility():
    """`escalated` is a declaration about the handoff. Only `irreversible` is a
    declaration about undoing, and it is honoured before the name is read at
    all — so a producer that knows better still wins, and one that does not
    cannot buy an exemption with the wrong field."""
    assert _is_irreversible(
        span("tool_call", "delete_account", attributes={"escalated": True})) is True
    assert _is_irreversible(
        span("tool_call", "delete_account",
             attributes={"escalated": True, "irreversible": False})) is False
    assert _is_irreversible(
        span("tool_call", "transfer_to_human",
             attributes={"irreversible": True})) is True
