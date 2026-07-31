"""The producer for ``data_condition=ambiguous`` and ``=contradictory``.

:mod:`agenttic.scenario.world` cites **this file** by name — "both of which
``tests/scenario/test_ambiguous_data.py`` asserts directly" — and until now the
file did not exist. A docstring claiming a verification that was never written
is the defect this product exists to report, one level up: the citation read as
evidence and nothing stood behind it.

What is actually on trial is the anti-fabrication property, not the feature. Two
of ``data_condition``'s bins had no producer at all, so the CDV loop requested
them 14-24 times per seed and got nothing back (the table in ``world.py``).
``_WORLD_SHAPES`` is the producer. The hazard in adding one is that the easy
implementation — read ``hidden_facts["data_condition"]`` and make the world match
— credits the bin for what the GENERATOR ASKED rather than for what the world
did, which is the one move coverage in this repo is not allowed to make.

So the load-bearing tests here are the two crossed cases:

* an ``ambiguous`` POINT against a seed whose world is intact exhibits NOTHING;
* a ``complete`` POINT against a seed whose world has a duplicate exhibits the
  ambiguity anyway.

Together those say the credit is a fact about the world and never about the
request. Everything else in this file is scaffolding for those two.

Offline by construction — no API key, no socket.
"""

from __future__ import annotations

import socket
import uuid

import pytest

from agenttic.registry.sqlite_store import Registry
# through the package's own re-exports, as tests/scenario/test_world.py does —
# a missing re-export should fail here rather than three tests later.
from agenttic.scenario import (
    RETAIL_POLICY,
    ScenarioEnvironment,
    install_scenario_enforcement,
    seed_world,
)
from agenttic.scenario.tools import data_condition_of
from agenttic.stimulus.realize import realize
from agenttic.stimulus.spaces.conversational_transactional import seed_space

LOOKUP = "lookup_order"

#: Wide enough that a producer firing at the table's 1-in-6 would be found many
#: times over, so "not found in this range" is evidence of absence rather than
#: of a short search.
SEEDS = range(64)


@pytest.fixture(scope="module")
def reg(tmp_path_factory) -> Registry:
    return Registry(str(tmp_path_factory.mktemp("amb") / "reg.db"))


@pytest.fixture
def no_network(monkeypatch):
    def _boom(*a, **k):
        raise AssertionError("network access attempted by the scenario world")
    monkeypatch.setattr(socket, "socket", _boom)
    monkeypatch.setattr(socket, "create_connection", _boom)
    yield


def _scenario(seed: int, **overrides):
    """A RealizedScenario off the offline template path, never hand-built."""
    point = {"intent": "status", "emotional_register": "neutral",
             "data_condition": "complete", "tool_condition": "all_ok",
             "policy_vector": "compliant"}
    point.update(overrides)
    return realize(point, seed, seed_space(), policy=RETAIL_POLICY, client=None)


def _env(reg: Registry, scenario) -> ScenarioEnvironment:
    agent_id = f"amb-agent-{uuid.uuid4().hex[:8]}"
    gw, sess = install_scenario_enforcement(reg, agent_id)
    return ScenarioEnvironment(scenario, gateway=gw, session_id=sess.session_id)


def _condition_exhibited(reg: Registry, scenario) -> str | None:
    """What looking the target order up ACTUALLY exhibited.

    Read through :func:`data_condition_of`, the same derivation the span stamp
    uses, so this test cannot pass by a route the product does not take.
    """
    env = _env(reg, scenario)
    call = env.call(LOOKUP, {"order_id": scenario.env_seed["order_id"]})
    return data_condition_of(call.output)


def _seeds_by_condition(reg: Registry, **overrides) -> dict[str | None, list[int]]:
    out: dict[str | None, list[int]] = {}
    for s in SEEDS:
        scn = _scenario(s, **overrides)
        out.setdefault(_condition_exhibited(reg, scn), []).append(s)
    return out


# --------------------------------------------------------------------------- #
# the producer exists at all
# --------------------------------------------------------------------------- #


class TestTheTwoBinsHaveAProducer:
    def test_both_degraded_conditions_are_reachable(self, reg, no_network):
        """The gap this producer closed: before `_WORLD_SHAPES`, nothing in the
        platform could make two orders match or a record disagree with itself,
        so both bins were permanently unreachable and every draw landed in
        `report.divergence()` as requested-but-never-exhibited."""
        found = _seeds_by_condition(reg)
        assert found.get("ambiguous"), (
            "no seed in 0..63 produced two matching orders — "
            "`data_condition=ambiguous` has no producer again")
        assert found.get("contradictory"), (
            "no seed in 0..63 produced a record disagreeing with its carrier "
            "feed — `data_condition=contradictory` has no producer again")

    def test_most_worlds_are_still_intact(self, reg, no_network):
        """A degraded shape on every seed would make the CLEAN case the
        unreachable one — the same defect pointing the other way."""
        found = _seeds_by_condition(reg)
        assert len(found.get(None, [])) > len(found.get("ambiguous", [])), (
            "degraded worlds outnumber intact ones; `complete` is now the "
            "condition that cannot be exhibited")

    def test_the_shape_is_stable_for_a_seed(self, reg, no_network):
        """Same seed, same world — or none of this is replayable evidence."""
        scn = _scenario(3)
        assert _condition_exhibited(reg, scn) == _condition_exhibited(reg, scn)


# --------------------------------------------------------------------------- #
# THE POINT OF THE FILE: credited from the world, never from the request
# --------------------------------------------------------------------------- #


class TestTheCreditComesFromTheWorldAndNotTheRequest:
    def test_asking_for_ambiguous_does_not_make_the_world_ambiguous(
            self, reg, no_network):
        """An `ambiguous` POINT against a seed whose world is intact exhibits
        NOTHING — the first of the two sentences `world.py` promises this file
        asserts.

        If this fails, `seed_world` has started reading the point, and the bin
        is being credited for what the generator asked. That is the failure this
        whole file exists for: it would make `data_condition` closure a report
        on the stimulus rather than on the run.
        """
        intact = _seeds_by_condition(reg).get(None) or []
        assert intact, "no intact world to test against"

        for seed in intact[:12]:
            asked = _scenario(seed, data_condition="ambiguous")
            assert asked.point["data_condition"] == "ambiguous"   # really asked
            assert _condition_exhibited(reg, asked) is None, (
                f"seed {seed}: the point asked for `ambiguous` and the world "
                "produced it — the request is now creating the evidence")

    def test_a_complete_point_still_exhibits_an_ambiguity_the_world_has(
            self, reg, no_network):
        """The crossed case, and the stronger half: a `complete` POINT against a
        seed whose world has a duplicate exhibits the ambiguity anyway.

        A world that only degraded when asked would pass the test above by never
        degrading at all. This one fails in that case.
        """
        ambiguous_seeds = _seeds_by_condition(reg).get("ambiguous") or []
        assert ambiguous_seeds, "no ambiguous world to test against"

        for seed in ambiguous_seeds:
            asked_clean = _scenario(seed, data_condition="complete")
            assert asked_clean.point["data_condition"] == "complete"
            assert _condition_exhibited(reg, asked_clean) == "ambiguous", (
                f"seed {seed}: the world holds two matching orders and a "
                "`complete` point suppressed the finding — the request is "
                "deciding what the run is allowed to exhibit")

    def test_the_same_holds_for_contradictory(self, reg, no_network):
        contradictory = _seeds_by_condition(reg).get("contradictory") or []
        assert contradictory, "no contradictory world to test against"

        for seed in contradictory:
            asked_clean = _scenario(seed, data_condition="complete")
            assert _condition_exhibited(reg, asked_clean) == "contradictory"


# --------------------------------------------------------------------------- #
# the two documented carve-outs
# --------------------------------------------------------------------------- #


class TestTheCarveOuts:
    def test_an_absent_target_is_never_shaped(self, reg, no_network):
        """`entity_not_found` declares the target absent, and manufacturing a
        match for an order the scenario says does not exist would break the one
        thing that condition asserts."""
        for seed in list(SEEDS)[:24]:
            absent = _scenario(seed, data_condition="entity_not_found")
            store = seed_world(absent)
            order_id = absent.env_seed["order_id"]
            assert order_id not in store.snapshot()["orders"]
            env = _env(reg, absent)
            call = env.call(LOOKUP, {"order_id": order_id})
            assert call.output is None and call.error, (
                f"seed {seed}: the target was supposed to be absent")

    def test_the_credit_is_structural_and_not_a_word_match(self, reg,
                                                            no_network):
        """`tools.py` is explicit that the payload must NOT return an error with
        the word "ambiguous" in it, "because a bin credited [by] whatever
        happened to say the word" is the substring-matching family this repo
        keeps finding. So the evidence must be countable structure — the length
        of `matches`, the `agrees_with_record` comparison — and the word must be
        absent from the payload entirely.
        """
        by = _seeds_by_condition(reg)
        for seed in (by.get("ambiguous") or [])[:6]:
            env = _env(reg, _scenario(seed))
            call = env.call(LOOKUP,
                            {"order_id": _scenario(seed).env_seed["order_id"]})
            blob = str(call.output).lower()
            assert "ambiguous" not in blob, (
                "the payload says the word; a reader cannot tell a structural "
                "finding from a string that happens to contain it")
            assert len(call.output["matches"]) > 1      # the actual evidence

        for seed in (by.get("contradictory") or [])[:6]:
            env = _env(reg, _scenario(seed))
            call = env.call(LOOKUP,
                            {"order_id": _scenario(seed).env_seed["order_id"]})
            blob = str(call.output).lower()
            assert "contradict" not in blob
            assert call.output["carrier"]["agrees_with_record"] is False

    def test_an_undecidable_comparison_credits_nothing(self):
        """The vacuity rule inside the derivation itself: `agrees_with_record is
        None` is an undecidable comparison, and an undecidable comparison is not
        evidence of consistency. It must credit NEITHER condition rather than
        defaulting into `complete`."""
        assert data_condition_of(
            {"matches": [{"order_id": "o-1"}],
             "carrier": {"agrees_with_record": None}}) is None
        assert data_condition_of({"matches": [{"order_id": "o-1"}]}) is None
        assert data_condition_of(None) is None
        # and the positive controls, so the Nones above are not vacuous
        assert data_condition_of(
            {"matches": [{"order_id": "o-1"}, {"order_id": "o-2"}]}) == "ambiguous"
        assert data_condition_of(
            {"carrier": {"agrees_with_record": False}}) == "contradictory"
