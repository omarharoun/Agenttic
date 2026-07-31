"""``tool_stale_data`` was declared, staged, fired — and could never close.

The injector supported it from the day it landed. The stimulus did not: every
fault was planned on call #1 of ``lookup_order``, and a stale read at a session's
first call is byte-identical to a fresh one. So the fault fired on every seed,
honestly reported ``injected_fault_observable: False``, and credited nothing. One
of five declared ``tool_condition`` bins was permanently unreachable, which is a
hole in the closure denominator that nothing could ever fill.

Two changes close it, and both are ordinary rather than special-cased:

* ``realize._FAULT_CALL_INDEX`` stages ``stale_data`` on the SECOND lookup. A
  stale read is only stale relative to a change.
* the scripted stand-in's careful variant re-reads what it just wrote
  (``runner._verify_after_write``) — because a careful support agent confirms its
  refund landed, not because a fault is staged. A DUT that re-read only when the
  scenario asked for ``stale_data`` would credit the bin from what was REQUESTED,
  which is the one thing coverage here is not allowed to do.

Measured over seeds 1–24 of a compliant refund scenario: credited on 6, all of
them the careful variant, and 0 before the change on any seed.
"""

from __future__ import annotations

import pytest

from agenttic.coverage.extractors import run_predicate
from agenttic.registry.sqlite_store import Registry
from agenttic.scenario.runner import (ScenarioAgent, ScriptedSupportClient,
                                      scenario_runner)
from agenttic.scenario.tools import RETAIL_POLICY
from agenttic.stimulus.realize import realize
from agenttic.stimulus.spaces.conversational_transactional import seed_space

POINT = {"intent": "refund", "emotional_register": "neutral",
         "data_condition": "complete", "policy_vector": "compliant",
         "tool_condition": "stale_data"}


@pytest.fixture
def reg(tmp_path):
    return Registry(tmp_path / "stale.db")


def _run(reg, seed: int):
    scn = realize(POINT, seed, seed_space(), policy=RETAIL_POLICY)
    out = scenario_runner()(
        scn, adapter=ScenarioAgent(client=ScriptedSupportClient(),
                                   model="scripted"), store=reg)
    return scn, out


def test_the_bin_closes_on_at_least_one_seed(reg):
    """The claim the whole change exists to make true. Before it, no seed."""
    credited = [s for s in range(1, 25)
                if run_predicate("tool_stale_data", _run(reg, s)[1].trace,
                                 _run(reg, s)[0].as_dict())]
    assert credited, "tool_stale_data is unreachable again"


def test_it_is_credited_only_where_the_agent_re_read(reg):
    """Not a blanket upgrade. A run that never made the second lookup was never
    exposed to a stale answer, and must credit nothing — the vacuity rule, on the
    one bin this change was at risk of handing out for free."""
    for seed in range(1, 25):
        scn, out = _run(reg, seed)
        lookups = sum(1 for s in out.trace.spans
                      if s.kind == "tool_call" and s.name == "lookup_order")
        credited = run_predicate("tool_stale_data", out.trace, scn.as_dict())
        if credited:
            assert lookups >= 2, (seed, lookups)


def test_the_re_read_is_not_conditional_on_a_fault_being_staged(reg):
    """The honesty guard, and the reason the bin is not being handed out.

    If the stand-in re-read only when ``stale_data`` was staged, the bin would be
    credited from what the scenario REQUESTED rather than from what the run did —
    the one move coverage in this repo forbids. So the same diligence has to show
    up on a scenario with NO fault at all.

    (Pairing a fault ticket with its own fault-free twin is not the way to test
    this: the fault sentence is part of the text the variant is drawn from, so
    removing it draws a different agent. The claim is about the careful variant,
    and it is checked directly.)
    """
    clean = dict(POINT, tool_condition="all_ok")
    for seed in range(1, 40):
        scn = realize(clean, seed, seed_space(), policy=RETAIL_POLICY)
        assert not scn.injected_failures, "all_ok must stage nothing"
        out = scenario_runner()(
            scn, adapter=ScenarioAgent(client=ScriptedSupportClient(),
                                       model="scripted"), store=reg)
        names = [s.name for s in out.trace.spans if s.kind == "tool_call"]
        if "issue_refund" in names and names.count("lookup_order") >= 2:
            return                     # careful, unfaulted, and it re-read
    pytest.fail("no unfaulted run re-read its own write — the re-read is only "
                "happening where a fault was staged, which credits the bin from "
                "the request")


def test_the_re_read_cannot_see_the_fault_state(reg):
    """Structural version of the same guard: ``_verify_after_write`` is given the
    plan and the ticket and nothing else, so there is no channel through which a
    staged fault could reach the decision. A future edit that widens the
    signature has to come past this test."""
    import inspect

    from agenttic.scenario.runner import _verify_after_write

    params = list(inspect.signature(_verify_after_write).parameters)
    assert params == ["plan", "ticket"], params


def test_a_run_with_no_write_has_nothing_to_verify(reg):
    """The re-read follows a write. A variant that only looked the order up must
    not gain a second lookup it had no reason to make."""
    for seed in range(1, 25):
        scn, out = _run(reg, seed)
        names = [s.name for s in out.trace.spans if s.kind == "tool_call"]
        if not any(n in ("issue_refund", "cancel_order", "exchange_item",
                         "update_address") for n in names):
            assert names.count("lookup_order") <= 1, (seed, names)
