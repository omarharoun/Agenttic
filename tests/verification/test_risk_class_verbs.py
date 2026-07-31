"""`read` is a safety claim, so an ambiguous verb must not be able to make it.

`coverage/extractors._action_read_only` credits the `action_risk=read_only` bin
from `builtins.is_read`, and its wording is "the run touched tools and EVERY ONE
of them is known not to have changed anything". The verb-first rewrite briefly
put `resolve` in `_READ_VERBS`, so a run whose only tool call was
`resolve_dispute` landed in that bin: a guarantee issued on the strength of a
word that reads two ways.

These tests pin the asymmetry rather than the word list. A verb that decides its
meaning by its object is `unknown`, and `unknown` is refused by the bin.
"""

from __future__ import annotations

import pytest

from agenttic.coverage.extractors import PREDICATES
from agenttic.verification.builtins import (
    _AMBIGUOUS_VERBS, _READ_VERBS, _WRITE_VERBS, is_irreversible, is_read,
    is_write, risk_class)

from .conftest import span, trace


def _tool(name: str, **attrs):
    return span("tool_call", name, i=0, attributes=attrs or None)


def _run(*names: str):
    spans = [span("tool_call", n, i=i) for i, n in enumerate(names)]
    return trace(*spans, span("final_output", "final_output", i=len(names)))


# --------------------------------------------------------------------------- #
# the defect itself
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("name", [
    "resolve_dispute",      # closes a chargeback dispute
    "resolve_ticket",       # closes a support ticket
    "resolve_incident",
    "verify_email",         # marks an address verified
    "select_seat",          # holds inventory
    "export_secrets",       # metrics/injection_detect calls this exfiltration
])
def test_ambiguous_verb_cannot_earn_the_read_only_claim(name):
    """The bin says "known not to have changed anything". Nothing here is known."""
    assert risk_class(_tool(name)) == "unknown", name
    assert is_read(_tool(name)) is False, name
    assert PREDICATES["action_read_only"](_run(name)) is False, name


def test_one_ambiguous_call_forfeits_the_bin_for_the_whole_run():
    """ALL of them, not most — a single unclassifiable call makes "changed
    nothing" unprovable for the run, so an otherwise clean read run loses it."""
    assert PREDICATES["action_read_only"](_run("get_order", "lookup_kb")) is True
    assert PREDICATES["action_read_only"](
        _run("get_order", "lookup_kb", "resolve_dispute")) is False


def test_ambiguity_is_not_promoted_to_a_write_either():
    """`unknown` is evidence of nothing in BOTH directions: crediting
    `mutating_reversible` off `resolve_*` would fabricate a risky event the same
    way `read_only` fabricated a safe one."""
    tr = _run("resolve_dispute")
    assert PREDICATES["action_mutating_reversible"](tr) is False
    assert PREDICATES["action_mutating_irreversible"](tr) is False
    assert is_write(_tool("resolve_dispute")) is False
    assert is_irreversible(_tool("resolve_dispute")) is False


def test_instrumentation_settles_what_the_name_cannot():
    """The escape hatch, and the direction the design is meant to move in: a
    declared semantic outranks the verb, so an ambiguous verb is a reason to
    instrument the tool, not a reason to guess."""
    assert risk_class(_tool("resolve_dispute", mutating=True)) == "write"
    assert risk_class(_tool("resolve_dispute", mutating=False)) == "read"
    assert PREDICATES["action_read_only"](
        trace(_tool("resolve_dispute", mutating=False),
              span("final_output", "final_output", i=1))) is True


# --------------------------------------------------------------------------- #
# invariants that keep the fix from being undone by a later edit
# --------------------------------------------------------------------------- #

def test_the_three_verb_sets_are_disjoint():
    """`_AMBIGUOUS_VERBS` is consulted first, so a verb listed in two sets would
    make one listing dead code — and the dead one would be the safety-relevant
    listing. Fail loudly instead."""
    assert _AMBIGUOUS_VERBS & _READ_VERBS == frozenset()
    assert _AMBIGUOUS_VERBS & _WRITE_VERBS == frozenset()
    assert _READ_VERBS & _WRITE_VERBS == frozenset()


def test_plain_reads_still_earn_the_bin():
    """The refusal has to stay narrow. Demoting every borderline verb would turn
    an over-report into an under-report of the same size, and `read_only` would
    be unreachable for every uninstrumented suite in existence."""
    for name in ("get_order", "list_orders", "search_kb", "check_balance",
                 "describe_account", "download_invoice", "validate_address"):
        assert risk_class(_tool(name)) == "read", name
        assert PREDICATES["action_read_only"](_run(name)) is True, name


def test_writes_are_unaffected():
    for name in ("issue_refund", "delete_account", "cancel_order", "update_order"):
        assert risk_class(_tool(name)) == "write", name
        assert PREDICATES["action_read_only"](_run(name)) is False, name
