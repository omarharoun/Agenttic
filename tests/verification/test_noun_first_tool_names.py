"""Verb-first classification only works when the verb is actually first.

``risk_class`` reads the LEADING token of a tool name and looks it up in a read
list and a write list. That rule was written against `get_order` / `issue_refund`
and it is right about those. It is silently wrong about a whole family of real
names where the object leads and the verb trails::

    wire_transfer         lead token "wire"     -> unknown
    account_delete        lead token "account"  -> unknown
    admin_delete_user     lead token "admin"    -> unknown
    payments.create_charge lead token "payments" -> unknown

``_is_irreversible`` then collapses ``unknown`` to ``False`` with no disclosure,
so an unconfirmed wire transfer is neither reported as an irreversible action nor
recorded as a tool nobody could classify. The critical assertion returns
``unexercised`` — which reads as "no irreversible action happened" — and the risk
coverpoint credits nothing dangerous.

The fix keeps the lead verb AUTHORITATIVE and only looks further when it says
nothing. That ordering is load-bearing: `view_transfer_history` and
`get_cancellation_reason` must stay reads, and they do, because their lead token
is a read verb and the scan never runs. Only a name whose lead token is in
neither list falls through to the token scan.
"""

from __future__ import annotations

import pytest

from agenttic.schema.trace import Span
from agenttic.verification.builtins import risk_class



def _tool(name: str) -> Span:
    return Span(span_id="s", kind="tool_call", name=name,
                start_time="2026-01-01T00:00:00Z",
                end_time="2026-01-01T00:00:01Z")


#: Object-first names for genuinely mutating operations.
NOUN_FIRST_WRITES = [
    "wire_transfer",
    "account_delete",
    "admin_delete_user",
    "payments_create_charge",
    "customer_update",
    "order_cancel",
    "invoice_void_and_refund",
]

#: The reason the lead verb has to keep winning. Each of these contains a write
#: verb somewhere, and each is a read.
READ_VERB_GOVERNS = [
    "view_transfer_history",
    "get_cancellation_reason",
    "fetch_charges",
    "list_deleted_accounts",
    "search_refund_policy",
    "describe_delete_permissions",
]

#: Genuinely unclassifiable. The three-valued answer exists for these, and
#: widening the rule must not turn them into guesses.
UNCLASSIFIABLE = [
    "frobnicate",
    "tool_0",
    "mcp__acme__run",
    "do_the_thing",
]


@pytest.mark.parametrize("name", NOUN_FIRST_WRITES)
def test_a_trailing_verb_still_classifies_the_write(name):
    assert risk_class(_tool(name)) == "write", name


@pytest.mark.parametrize("name", READ_VERB_GOVERNS)
def test_a_leading_read_verb_still_wins_over_a_trailing_write_verb(name):
    """The guard that makes the widening safe. `view_transfer_history` is a
    query ABOUT transfers; classifying it as a wire transfer is the exact defect
    verb-first was introduced to fix, and it must not come back through the
    fallback."""
    assert risk_class(_tool(name)) == "read", name


@pytest.mark.parametrize("name", UNCLASSIFIABLE)
def test_a_name_with_no_verb_at_all_stays_unknown(name):
    """Three-valued on purpose. `unknown` is evidence of nothing, and a fallback
    that guessed would be asserting a fact about safety nobody established."""
    assert risk_class(_tool(name)) == "unknown", name


@pytest.mark.parametrize("name", ["wire_transfer", "account_delete",
                                  "admin_delete_user"])
def test_a_noun_first_destructive_call_is_irreversible(name):
    """The consequence that matters. While these classified `unknown`,
    `_is_irreversible` returned False and `always_irreversible_action_confirmed`
    reported `unexercised` — indistinguishable, in a report, from a run where
    nothing irreversible happened."""
    from agenttic.verification.builtins import _is_irreversible

    assert _is_irreversible(_tool(name)) is True, name


def test_a_declared_semantic_still_beats_every_name_rule():
    """Declared flags are the direction this is meant to move in; no widening of
    the heuristic may override one."""
    declared_read = Span(span_id="s", kind="tool_call", name="account_delete",
                         start_time="2026-01-01T00:00:00Z",
                         end_time="2026-01-01T00:00:01Z",
                         attributes={"mutating": False})
    assert risk_class(declared_read) == "read"
