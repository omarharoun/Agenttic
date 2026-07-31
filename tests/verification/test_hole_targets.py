"""M45 — the solver's target list, which used to make the loop WORSE than random.

``ops.cdv_op``'s write-up measured coverage-directed generation losing to plain
unbiased random by 2 to 5 points of closure on every seed it tried. The cause was
entirely in ``holes_to_targets``:

* ``CoverageReport.holes()`` gives every cross cell the same structural rank
  (3.0) and breaks the tie alphabetically, so the head of the list is
  ``all_ok×budget_exceeded``, ``all_ok×direct_answer``, … — cells whose only
  steerable component is ``tool_condition=all_ok``, the bin a suite gets free.
* ``holes_to_targets`` emitted one target per hole, so those cells filled a batch
  of 10 by themselves (``targets[i % len(targets)]``), and every biased round in
  every seed aimed at ``all_ok``.
* Everything it could not aim at — ``trajectory``, ``agent_steps``,
  ``action_risk``, four of the six baseline coverpoints — was dropped by a bare
  ``continue``, so nothing downstream could see that the loop had given up on
  most of its own hole list.

These tests pin the three rules that fixed it. They deliberately do NOT assert
that biasing beats random: that is a measured claim about a whole run, it lives
in the module docstring with its table, and an assertion here would be a worse
version of it. What is pinned is the mechanism.

Offline throughout, under ``no_network``.
"""

from __future__ import annotations

import pytest

from agenttic.coverage.collect import (
    BinCoverage, CoverageReport, CoverpointCoverage, CrossCoverage, Sample,
    collect)
from agenttic.coverage.model import Bin, CoverageModel, Coverpoint, Cross
from agenttic.coverage.models.baseline import baseline_model
from agenttic.schema.trace import Trace
from agenttic.stimulus.space import BinRef, Dimension, ScenarioSpace
from agenttic.stimulus.spaces.conversational_transactional import seed_space
from agenttic.verification import cdv
from agenttic.verification.cdv import (
    Budget, ExecutionResult, HoleTargets, holes_to_targets, run_until_closure)
from tests.verification.conftest import span, trace

pytestmark = pytest.mark.usefixtures("no_network")

CFG = {"coverage": {"closure_target": 0.95}}


def _clean_run() -> Trace:
    """One successful tool call and an answer: ``tool_condition=all_ok`` and
    ``trajectory=tool_then_answer``, and nothing else."""
    return trace(span("tool_call", "lookup_order", i=0, output={"status": "ok"}),
                 span("final_output", "final_output", i=1,
                      output={"text": "here you go"}))


# --------------------------------------------------------------------------- #
# a toy model + space, small enough that every number below is countable by hand
# --------------------------------------------------------------------------- #

def _toy_model() -> CoverageModel:
    return CoverageModel(
        model_id="cov-toy", version=1,
        coverpoints=[
            Coverpoint(coverpoint_id="tool_condition", bins=[
                Bin(bin_id="all_ok", predicate_ref="tool_all_ok"),
                Bin(bin_id="timeout", predicate_ref="tool_timeout"),
                Bin(bin_id="other")]),
            # An OUTPUT of the run. No space can ask for it — which is the whole
            # reason the disclosure exists.
            Coverpoint(coverpoint_id="trajectory", bins=[
                Bin(bin_id="direct_answer", predicate_ref="traj_direct_answer"),
                Bin(bin_id="tool_then_answer",
                    predicate_ref="traj_tool_then_answer"),
                Bin(bin_id="other")]),
        ],
        crosses=[Cross(cross_id="tool_x_trajectory",
                       coverpoints=["tool_condition", "trajectory"],
                       target="all")],
        closure_target=0.95)


def _toy_space() -> ScenarioSpace:
    """``tool_condition`` is a knob; ``trajectory`` is not. That asymmetry is the
    real one — ``derive_space`` refuses to make an output coverpoint a
    dimension (``stimulus/derive.py``, OUTPUT_ONLY_COVERPOINTS)."""
    return ScenarioSpace(space_id="space-toy", version=1, dimensions=(
        Dimension("tool_condition", ("all_ok", "timeout")),))


def _after_one_clean_run(model=None, space=None) -> HoleTargets:
    model = model or _toy_model()
    report = collect(model, [Sample(trace=_clean_run())])
    return holes_to_targets(report, model, space or _toy_space())


# --------------------------------------------------------------------------- #
# (a) a bin the runs already exhibited is not a target
# --------------------------------------------------------------------------- #


class TestACoveredBinIsNotWorthAimingAt:
    def test_an_exhibited_bin_is_never_a_bin_target(self):
        """``all_ok`` was exhibited by the one run, so it is not a bin hole and
        no bin hole names it. ``timeout`` was not, so it is.

        This is the rule stated at its narrowest, where it is exact: a target
        derived from a BIN hole names a bin the runs have not exhibited.
        """
        model = _toy_model()
        report = collect(model, [Sample(trace=_clean_run())])
        assert report.coverpoints["tool_condition"].bins["all_ok"].hit
        assert report.coverpoints["tool_condition"].unhit == ["timeout"]

        bin_holes = [h for h in report.holes() if h.kind == "bin"]
        assert {(h.where, h.what) for h in bin_holes} == {
            ("tool_condition", "timeout"), ("trajectory", "direct_answer")}

    def test_a_bin_hole_naming_an_exhibited_bin_is_disclosed_not_aimed_at(self):
        """The guard on the entry point.

        ``holes()`` derives bin holes from ``cp.unhit``, so it cannot itself hand
        ``holes_to_targets`` a hit bin — but ``holes_to_targets`` takes a
        ``CoverageReport``, and ``CoverageReport._scored`` says in as many words
        that one can be built without a model. Rather than assert the guard
        through a report shape that cannot occur, this drives the decision
        function it guards: given a hole naming a bin the report records as hit,
        the answer is "no target" plus a reason, never a pin.
        """
        model = _toy_model()
        report = collect(model, [Sample(trace=_clean_run())])
        hit = cdv.Hole("bin", "tool_condition", "all_ok", True, rank=2.0)
        refs, why = cdv._aim_at(hit, report, model, _toy_space())
        assert refs is None
        assert "already been exhibited" in why

        unhit = cdv.Hole("bin", "tool_condition", "timeout", True, rank=2.0)
        assert cdv._aim_at(unhit, report, model, _toy_space())[0] == [
            BinRef("tool_condition", "timeout")]

    def test_the_covered_bin_is_last_and_the_empty_one_is_first(self):
        """The end-to-end ordering, on the toy model.

        ``all_ok`` survives as a target at all because it is the only steerable
        half of the unhit ``all_ok×direct_answer`` cell — dropping it outright
        abandons the cross, and measured over 21 seeds that is worse (mean 0.7217
        against 0.7342). It is demoted rather than dropped.

        Note what this test does NOT establish: here the plain summed rank
        already orders the two the same way, because an exhibited bin loses both
        its own bin hole and one cross cell. The discount is isolated by
        :meth:`test_a_heavily_drawn_bin_is_demoted_below_a_lighter_target`.
        """
        ht = _after_one_clean_run()
        assert ht.targets, "nothing to aim at"
        assert ht.targets[0] == [BinRef("tool_condition", "timeout")]
        assert ht.targets[-1] == [BinRef("tool_condition", "all_ok")]

    def test_the_order_follows_the_hit_count_and_not_the_name(self):
        """Not alphabetical luck: with `all_ok` unexhibited and `timeout`
        exhibited the order inverts."""
        model = _toy_model()
        timeout_run = trace(
            span("tool_call", "lookup_order", i=0, error="timeout"),
            span("tool_call", "lookup_order", i=1, output={"status": "ok"}),
            span("final_output", "final_output", i=2, output={"text": "ok"}))
        report = collect(model, [Sample(trace=timeout_run)])
        assert report.coverpoints["tool_condition"].bins["timeout"].hit
        assert not report.coverpoints["tool_condition"].bins["all_ok"].hit
        ht = holes_to_targets(report, model, _toy_space())
        assert ht.targets[0] == [BinRef("tool_condition", "all_ok")]
        assert ht.targets[-1] == [BinRef("tool_condition", "timeout")]

    def test_a_heavily_drawn_bin_is_demoted_below_a_lighter_target(self):
        """The discount, isolated — the one case where "serves the most holes"
        and "is worth aiming at" disagree.

        ``a1`` is the steerable half of TEN unhit cross cells and ``a2`` of six,
        so by summed rank alone (30 against 18) ``a1`` leads. But 20 runs already
        exhibited ``a1`` and only 3 exhibited ``a2``: pinning ``a1`` buys a draw
        the suite is already making twenty times over, which is the shape of the
        original defect (39 of 60 scenarios spent on a bin hit 36 times). Divided
        by what was already drawn the order inverts — 30/21 = 1.43 against
        18/4 = 4.5 — and the loop aims where the batch is not already going.

        Built by hand rather than collected: producing this shape from real
        traces would need a run in which the MORE exhibited bin also has more
        unhit cells, and the arithmetic of a product cross makes that awkward to
        stage without obscuring what is being tested.
        """
        model = CoverageModel(
            model_id="cov-rank", version=1,
            coverpoints=[
                Coverpoint(coverpoint_id="dim_a", bins=[
                    Bin(bin_id="a1", predicate_ref="tool_all_ok"),
                    Bin(bin_id="a2", predicate_ref="tool_timeout"),
                    Bin(bin_id="other")]),
                Coverpoint(coverpoint_id="dim_b", bins=[
                    Bin(bin_id="b1", predicate_ref="traj_direct_answer"),
                    Bin(bin_id="other")]),
            ],
            crosses=[Cross(cross_id="x", coverpoints=["dim_a", "dim_b"],
                           target="all")],
            closure_target=0.95)

        report = CoverageReport(model_ref=model.ref(), bins_fingerprint="f",
                                n_samples=23)
        for cp_id, hits in (("dim_a", {"a1": 20, "a2": 3}), ("dim_b", {"b1": 1})):
            cov = CoverpointCoverage(cp_id, "deterministic", True, False)
            for b, n in hits.items():
                cov.bins[b] = BinCoverage(bin_id=b, trace_hits=n)
            report.coverpoints[cp_id] = cov
        report.crosses["x"] = CrossCoverage(
            "x", ["dim_a", "dim_b"],
            target_combos=([("a1", f"b{i}") for i in range(10)]
                           + [("a2", f"b{i}") for i in range(6)]),
            hit_combos=set())

        # no bin holes: both dim_a bins are exhibited, so the ONLY thing ordering
        # these two targets is the cross weight and the draw count.
        assert [h.kind for h in report.holes()] == ["cross"] * 16
        ht = holes_to_targets(report, model, ScenarioSpace(
            space_id="space-rank", version=1,
            dimensions=(Dimension("dim_a", ("a1", "a2")),)))
        assert ht.targets == [[BinRef("dim_a", "a2")], [BinRef("dim_a", "a1")]]


# --------------------------------------------------------------------------- #
# (b) duplicate target-sets collapse
# --------------------------------------------------------------------------- #


class TestDuplicateTargetSetsCollapse:
    def test_eight_cross_cells_with_one_steerable_axis_are_one_target(self):
        """The measured defect, on the real baseline model.

        ``baseline_model`` v3 crosses ``tool_condition`` (6 bins) with
        ``trajectory`` (9 bins); ``trajectory`` is an output, so every
        ``all_ok×*`` cell reduces to the single pin ``tool_condition=all_ok``.
        Eight cells, one target — not eight entries that fill a batch of 10 on
        their own and push ``rate_limited`` off the end of the list.
        """
        model = baseline_model(cfg=CFG)
        report = collect(model, [Sample(trace=_clean_run())])
        all_ok_cells = [h for h in report.holes()
                        if h.kind == "cross" and h.what.startswith("all_ok×")]
        assert len(all_ok_cells) == 8, [h.what for h in all_ok_cells]

        ht = holes_to_targets(report, model, seed_space())
        pins = [t for t in ht.targets if t == [BinRef("tool_condition", "all_ok")]]
        assert len(pins) == 1, ht.targets

    def test_no_two_targets_pin_the_same_thing(self):
        model = baseline_model(cfg=CFG)
        report = collect(model, [Sample(trace=_clean_run())])
        ht = holes_to_targets(report, model, seed_space())
        keys = [tuple(sorted((r.dim_id, r.value) for r in t)) for t in ht.targets]
        assert len(keys) == len(set(keys))

    def test_the_rounds_advance_through_a_target_list_longer_than_the_batch(
            self, monkeypatch):
        """Criterion 4. ``targets[i % len(targets)]`` over ``range(batch_size)``
        can only ever reach the first ``batch_size`` entries, so with 15 targets
        and a batch of 5 the last 10 were never requested however long the loop
        ran. The cursor advances across rounds instead of restarting at 0.
        """
        values = tuple(f"v{i:02d}" for i in range(15))
        space = ScenarioSpace(space_id="space-wide", version=1,
                              dimensions=(Dimension("intent", values),))
        model = _toy_model()
        wide = HoleTargets([[BinRef("intent", v)] for v in values], [])
        monkeypatch.setattr(cdv, "holes_to_targets", lambda *a, **k: wide)

        res = run_until_closure(
            space, model, lambda scn: ExecutionResult(trace=_clean_run()),
            Budget(max_scenarios=20, max_dollars=10.0, max_rounds=4),
            seed=1, batch_size=5)

        # round 0 is unbiased by construction; rounds 1..3 pin 5 targets each,
        # and the cursor carries the offset forward, so the three batches sweep
        # the whole list exactly once.
        aimed = {label for r in res.rounds for label in r.targeted}
        assert aimed == {f"intent={v}" for v in values}, sorted(aimed)
        # the decisive half: entries 5..14 are the ones `targets[i % len]` over
        # `range(5)` could never reach, however many rounds ran.
        assert {f"intent={v}" for v in values[5:]} <= aimed
        assert {s.point["intent"] for s in res.scenarios} >= set(values[5:])


# --------------------------------------------------------------------------- #
# (c) an un-aimable hole is DISCLOSED, never dropped
# --------------------------------------------------------------------------- #


class TestUnaimableHolesAreDisclosed:
    def test_a_hole_on_an_output_coverpoint_comes_back_with_a_reason(self):
        """The bare ``continue`` is the defect. ``trajectory=direct_answer`` is a
        real hole that no batch this loop generates can be aimed at, and saying
        so is a different statement from "3 holes remaining"."""
        ht = _after_one_clean_run()
        stated = {(u.where, u.what): u.reason for u in ht.unaimable}
        assert ("trajectory", "direct_answer") in stated
        assert "not a dimension" in stated[("trajectory", "direct_answer")]
        assert "output of the run" in stated[("trajectory", "direct_answer")]

    def test_every_hole_is_either_aimed_at_or_disclosed(self):
        """Nothing falls between the two lists. This is the invariant the
        ``continue`` broke: a hole that is neither a target nor a disclosure has
        left the system without anything recording that it did.

        Asserted against ``holes_to_targets``'s OWN output. The first version of
        this test rebuilt both sides by calling ``cdv._aim_at`` in a loop and then
        checked their union covered every hole — which is a tautology of the loop
        that just built them, and would have held even if ``holes_to_targets``
        returned nothing at all. It tested that ``_aim_at`` returns either refs or
        ``None``, which is its type signature.
        """
        model = baseline_model(cfg=CFG)
        space = seed_space()
        report = collect(model, [Sample(trace=_clean_run())])
        ht = holes_to_targets(report, model, space)

        disclosed = {(u.where, u.what) for u in ht.unaimable}
        # every BinRef the returned target sets actually pin
        pinned = {(r.dim_id, r.value) for tgt in ht.targets for r in tgt}
        dims = {d.dim_id for d in space.dimensions}

        for h in report.holes():
            key = (h.where, h.what)
            if key in disclosed:
                continue
            # not disclosed: some steerable component of it must be pinned by a
            # target this call returned, or the hole left without a record
            parts = ({(h.where, h.what)} if h.kind == "bin"
                     else {(cp, v) for cp, v in zip(
                         next(x for x in model.crosses
                              if x.cross_id == h.where).coverpoints,
                         h.what.split("×"))})
            steerable = {p for p in parts if p[0] in dims}
            assert steerable & pinned, (
                f"{key} is neither disclosed nor aimed at — it left the system "
                f"with nothing recording that it did (steerable={steerable})")

        assert disclosed, "nothing was disclosed — the fixture stopped biting"
        assert ht.targets, "nothing was aimed at — the fixture stopped biting"

    def test_the_disclosure_reaches_the_result_dict(self):
        """A fact the report does not carry reaches no artifact. ``as_dict`` is
        what the CLI and the scorecard render, so the disclosure has to be in
        it, next to ``holes_remaining`` and readable against it."""
        space = _toy_space()
        res = run_until_closure(
            space, _toy_model(),
            lambda scn: ExecutionResult(trace=_clean_run()),
            Budget(max_scenarios=6, max_dollars=10.0, max_rounds=2),
            seed=2, batch_size=3)
        payload = res.as_dict()
        assert "unaimable_holes" in payload
        rows = payload["unaimable_holes"]
        assert rows and all(r["reason"] for r in rows)
        assert ("trajectory", "direct_answer") in {(r["where"], r["what"])
                                                   for r in rows}
        # it names a SUBSET of what is still open, never something invented
        open_now = {(h["where"], h["what"]) for h in payload["holes_remaining"]}
        assert {(r["where"], r["what"]) for r in rows} <= open_now

    def test_the_control_arm_discloses_the_same_limitation(self):
        """Criterion 5 — ``bias=False`` still draws unbiased, but being unable to
        steer ``trajectory`` is a property of the (report, model, space) triple,
        not of whether aiming was switched on. A control run that stayed silent
        about it would make the disclosure look like a side effect of biasing."""
        common = dict(space=_toy_space(), coverage_model=_toy_model(),
                      execute=lambda scn: ExecutionResult(trace=_clean_run()),
                      budget=Budget(max_scenarios=6, max_dollars=10.0,
                                    max_rounds=2), seed=2, batch_size=3)
        biased = run_until_closure(bias=True, **common)
        control = run_until_closure(bias=False, **common)
        assert not any(r.biased for r in control.rounds)
        assert [(u.where, u.what) for u in control.unaimable_holes] == \
               [(u.where, u.what) for u in biased.unaimable_holes]

    def test_the_control_arm_still_draws_unbiased(self):
        """The control arm is the only thing that makes "aiming works" a checkable
        claim, so nothing in this change may touch how it samples."""
        common = dict(space=seed_space(), coverage_model=baseline_model(cfg=CFG),
                      execute=lambda scn: ExecutionResult(trace=_clean_run()),
                      budget=Budget(max_scenarios=20, max_dollars=10.0,
                                    max_rounds=2), seed=7, batch_size=10)
        control = run_until_closure(bias=False, **common)
        assert [s.point for s in control.scenarios] == [
            s.point for s in run_until_closure(bias=False, **common).scenarios]
        assert all(r.targeted == [] for r in control.rounds)
