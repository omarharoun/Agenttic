"""What ``realize()`` may write into ``injected_failures`` (P4).

The field's history is the reason this file is careful. It was
``[] if tools == "all_ok" else [tools]`` — the REQUESTED bin, copied verbatim
under a name that says "injected" — and ``coverage.extractors`` read it as an
authority on what HAPPENED. So a point drawn as `timeout` credited the timeout
bin off any unrelated failure. P0 emptied it rather than keep lying with it, and
left the specification: fill it from something that records WHICH CALL it failed.

Two properties are pinned here, and they are the whole of the field's rescue:

* it carries ATTRIBUTION — kind, tool, call of that tool — so it can be matched
  against a call, which a bare bin name never could;
* it describes what an injector will actually stage, verified against the plan
  ``scenario.faults.plan_faults`` independently derives for the same scenario.

Nothing here asserts that the field is credited. It is not, and that is the point
— see ``tests/coverage/test_injected_fault_stamp.py``.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

from agenttic.stimulus.realize import FAULT_KINDS, realize
from agenttic.stimulus.spaces.conversational_transactional import seed_space

POINT = {"intent": "refund", "emotional_register": "neutral",
         "data_condition": "complete", "policy_vector": "compliant"}


def scenario(condition: str, seed: int = 11):
    return realize({**POINT, "tool_condition": condition}, seed=seed,
                   space=seed_space())


class TestTheVocabularyCannotDrift:
    def test_the_kinds_are_exactly_the_degraded_tool_condition_bins(self):
        """A kind with no bin is a fault nothing can be credited for; a bin with
        no kind is a hole the CDV solver aims at forever. That second one is what
        P4 exists to end, so it is worth a test that fails in both directions."""
        values = set(seed_space().dimension("tool_condition").values)
        assert set(FAULT_KINDS) == values - {"all_ok"}

    def test_the_planner_and_the_injector_name_the_kinds_identically(self):
        """One vocabulary, spelled the same in both packages — so a fired fault
        can be written next to a planned one without a translation table, and a
        translation table is where a request quietly becomes an injection."""
        from agenttic.scenario.faults import FAULT_KINDS as INJECTOR_KINDS

        assert set(FAULT_KINDS) == set(INJECTOR_KINDS)


class TestTheFieldCarriesAPlan:
    def test_all_ok_plans_nothing(self):
        scn = scenario("all_ok")
        assert scn.injected_failures == []
        assert "fault_plan" not in scn.env_seed, (
            "a world that fails when nobody asked it to is a flaky fixture")

    @pytest.mark.parametrize("kind", FAULT_KINDS)
    def test_every_degraded_condition_plans_one_attributed_fault(self, kind):
        """The call index is per-kind, and this assertion used to read ``1`` for
        all five.

        That was written before its consequence for ``stale_data`` had been
        measured, and the consequence was that the bin could never close: a stale
        read at the session's first call is byte-identical to a fresh one, so the
        fault fired on every seed, honestly reported
        ``injected_fault_observable: False``, and credited nothing. A spec that
        makes one of its own five declared conditions unreachable is the spec
        that is wrong, so it was re-stated per kind rather than the behaviour bent
        to satisfy it — see ``realize._FAULT_CALL_INDEX`` for the argument and the
        cost.
        """
        expected = 2 if kind == "stale_data" else 1
        scn = scenario(kind)
        assert scn.injected_failures == [
            {"kind": kind, "tool": "lookup_order", "call_index": expected}]

    def test_stale_data_is_staged_after_a_write_and_the_others_are_not(self):
        """Pinned as its own statement so the reason survives: every other fault
        is visible against nothing, and this one is only visible against a
        change."""
        assert scenario("stale_data").injected_failures[0]["call_index"] == 2
        for kind in (k for k in FAULT_KINDS if k != "stale_data"):
            assert scenario(kind).injected_failures[0]["call_index"] == 1, kind

    @pytest.mark.parametrize("kind", FAULT_KINDS)
    def test_no_entry_is_a_bare_bin_name(self, kind):
        """The shape IS the fix. A string in this list cannot be matched to a
        call, so it can only ever be read as intent — which is how the field
        came to credit a bin off an unrelated failure."""
        for entry in scenario(kind).injected_failures:
            assert isinstance(entry, dict), entry
            assert set(entry) == {"kind", "tool", "call_index"}

    @pytest.mark.parametrize("kind", FAULT_KINDS)
    def test_the_plan_names_the_tool_the_ticket_names(self, kind):
        """The ticket says "The order-lookup tool …" for every fault condition.
        A plan that failed a different tool would put the world and the prompt
        into disagreement, and every downstream reading inherits that."""
        scn = scenario(kind)
        assert "order-lookup tool" in scn.text
        assert scn.injected_failures[0]["tool"] == "lookup_order"

    def test_the_request_is_still_recorded_separately(self):
        """The request does not need this field to survive, and must not borrow
        it: it is in ``point``, it reaches coverage as ``requested``, and it is
        what produces the divergence row when the plan never fires."""
        scn = scenario("rate_limited")
        assert scn.point["tool_condition"] == "rate_limited"
        assert scn.env_seed["requested_tool_condition"] == "rate_limited"


class TestThePlanIsTheOneThatGetsStaged:
    """A field that describes a fault nobody stages is the write-only hook this
    rescue keeps deleting. These pin it to the injector's own derivation."""

    @pytest.mark.parametrize("kind", FAULT_KINDS)
    def test_the_injector_derives_the_same_fault(self, kind):
        from agenttic.scenario.faults import plan_faults

        scn = scenario(kind)
        derived = [{"kind": f.kind, "tool": f.tool, "call_index": f.call_index}
                   for f in plan_faults(scn).faults]
        assert derived == scn.injected_failures

    @pytest.mark.parametrize("kind", FAULT_KINDS)
    def test_the_plan_survives_being_reduced_to_a_dict(self, kind):
        """Two modules agreeing today is not two modules that cannot disagree
        tomorrow, so the scenario STATES its plan rather than leaving the
        injector to re-derive one. It has to state it somewhere a serialized
        scenario still has — every caller downstream of the registry holds the
        reduced form, and a frozen regression must replay the fault that caught
        the bug, not a fresh guess at one."""
        from agenttic.scenario.faults import plan_faults

        class Reduced:
            """A scenario that has been through ``as_dict()``: no attributes
            left, only what storage kept."""
            def __init__(self, d):
                self.env_seed = d["env_seed"]
                self.seed = d["seed"]
                self.scenario_id = d["scenario_id"]

        scn = scenario(kind)
        assert scn.env_seed["fault_plan"] == {"faults": scn.injected_failures}
        derived = [{"kind": f.kind, "tool": f.tool, "call_index": f.call_index}
                   for f in plan_faults(Reduced(scn.as_dict())).faults]
        assert derived == scn.injected_failures

    def test_all_ok_leaves_the_injector_nothing_to_read(self):
        from agenttic.scenario.faults import plan_faults

        assert plan_faults(scenario("all_ok")).faults == ()


class TestReproducibility:
    def test_the_plan_survives_the_serialization_the_registry_stores(self):
        scn = scenario("stale_data")
        assert json.loads(json.dumps(scn.as_dict()))["injected_failures"] == \
            scn.injected_failures

    def test_the_plan_is_identical_across_interpreter_processes(self):
        """``realize()`` already carries one scar from ``hash()`` being salted
        per interpreter (see its own comment on the order id). A plan derived
        with it would fail a different call in every process and no replay would
        hold, so the plan is derived from the point alone and this proves it.
        """
        prog = ("import json;"
                "from agenttic.stimulus.realize import realize;"
                "from agenttic.stimulus.spaces.conversational_transactional "
                "import seed_space;"
                f"p=dict({POINT!r}, tool_condition='timeout');"
                "print(json.dumps(realize(p, 7, seed_space())"
                ".injected_failures))")
        out = {subprocess.run([sys.executable, "-c", prog], check=True,
                              capture_output=True, text=True,
                              env={**os.environ, "PYTHONHASHSEED": str(i)}
                              ).stdout.strip()
               for i in (1, 2, 3)}
        assert out == {'[{"kind": "timeout", "tool": "lookup_order", '
                       '"call_index": 1}]'}, out
