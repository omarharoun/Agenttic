"""A coverpoint nothing can feed must say so.

There are three ways a coverage model can treat a dimension no producer in the
system emits evidence for, and two of them are lies:

* credit it — what `session_shape` did, reporting every tool loop as a
  multi-turn session;
* score it 0% — reads as "the suite never got there", which is a finding a
  generator can be told to fix, and this one cannot be fixed by any suite;
* declare it not measurable, in words. That is a confession, and it is the only
  one of the three that is true.

`Coverpoint.measurable` is the third option, and it carries the same rule as
`Bin.waived`: declaring it requires a named reason (Hard Rule 61).
"""

from __future__ import annotations

import pytest

from agenttic.coverage.model import (
    DETERMINISTIC_BY_CONSTRUCTION, Bin, CoverageModel, Coverpoint)
from agenttic.coverage.models.baseline import baseline_model


def _cp(**kw) -> Coverpoint:
    kw.setdefault("bins", [Bin(bin_id="a", predicate_ref="traj_direct_answer"),
                           Bin(bin_id="other")])
    kw.setdefault("coverpoint_id", "cp")
    return Coverpoint(**kw)


class TestTheDeclarationIsMandatory:
    def test_a_not_measurable_coverpoint_needs_a_named_reason(self):
        with pytest.raises(ValueError, match="named reason"):
            _cp(measurable=False)
        ok = _cp(measurable=False, not_measurable_reason="no producer emits it")
        assert ok.not_measurable_reason

    def test_a_measurable_coverpoint_may_not_carry_the_reason(self):
        """Otherwise the two fields drift and the report has to guess."""
        with pytest.raises(ValueError, match="not_measurable_reason"):
            _cp(not_measurable_reason="looks unmeasurable but is not declared")

    def test_not_measurable_implies_not_required(self):
        """One decision, not two: a dimension nothing can feed cannot be
        required to close, and if the two could drift a coverpoint would end up
        in the headline holding a bin its predicate returns True for by
        default."""
        cp = _cp(measurable=False, not_measurable_reason="r", required=True)
        assert cp.required is False


class TestSessionShapeDeclaresItself:
    def test_session_shape_is_not_measurable_with_a_reason(self):
        cp = baseline_model().coverpoint("session_shape")
        assert cp.measurable is False
        assert "user_turn" in cp.not_measurable_reason
        assert cp.required is False

    def test_resumed_with_memory_is_waived_with_a_reason(self):
        cp = baseline_model().coverpoint("session_shape")
        b = cp.bin("resumed_with_memory")
        assert b.waived is True and b.reason.strip()
        assert b.bin_id not in [x.bin_id for x in cp.countable_bins()]

    def test_it_is_kept_rather_than_deleted(self):
        """Deleting the coverpoint would make the gap invisible; the product's
        claim is an account of what was never exercised, so the dimension has to
        keep being named."""
        assert baseline_model().coverpoint("session_shape") is not None


class TestItLeavesTheHeadlineAlone:
    def test_session_shape_is_excluded_from_closure_not_scored_zero(self):
        """The headline must be identical with and without a coverpoint that
        cannot be measured — neither credited nor counted as a zero."""
        from agenttic.coverage.collect import Sample, collect
        from tests.coverage.test_tool_condition_provenance import span, trace

        full = baseline_model()
        without = CoverageModel(
            model_id=full.model_id, version=full.version,
            coverpoints=[c for c in full.coverpoints
                         if c.coverpoint_id != "session_shape"],
            crosses=full.crosses, closure_target=full.closure_target)
        samples = [Sample(trace(span("tool_call", "get_order"),
                                span("llm_call", "messages.create", i=1)))]

        assert (collect(full, samples).trace_closure
                == collect(without, samples).trace_closure)


class TestTheSplitIsVisible:
    def test_agent_steps_is_a_deterministic_coverpoint_of_its_own(self):
        cp = baseline_model().coverpoint("agent_steps")
        assert cp is not None
        assert cp.kind == "deterministic"
        assert [b.bin_id for b in cp.bins] == ["single_step", "multi_step", "other"]
        assert "agent_steps" in DETERMINISTIC_BY_CONSTRUCTION

    def test_measurability_is_in_the_bins_fingerprint(self):
        """Flipping `session_shape` back into the headline once a simulated user
        exists is exactly as consequential as adding a bin, so it must be as
        visible — an approved diff, never a silent edit."""
        model = baseline_model()
        before = model.bins_fingerprint()
        flipped = CoverageModel(
            model_id=model.model_id, version=model.version,
            coverpoints=[
                c if c.coverpoint_id != "session_shape"
                else Coverpoint(coverpoint_id=c.coverpoint_id,
                                description=c.description, kind=c.kind,
                                bins=c.bins)          # measurable again
                for c in model.coverpoints],
            crosses=model.crosses, closure_target=model.closure_target)
        assert flipped.bins_fingerprint() != before

    def test_the_split_changed_the_fingerprint(self):
        """Closure figures from before the split measured a different space.
        The version bump and the fingerprint are how that stays visible."""
        assert baseline_model().version >= 3
