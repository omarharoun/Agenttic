"""A cross cannot launder a not-measurable coverpoint back into the headline.

`session_shape` is declared not measurable and reports closure `None`: nothing
emits a `user_turn` span, so `session_single_turn` returning True is evidence of
absent instrumentation, not of a single-turn session. Round 2 got the coverpoint
out of the headline. It did not get its BINS out — a cross is a set of bin
combinations, and a cross closure is averaged into the same headline:

    Cross(cross_id="x", coverpoints=["session_shape", "agent_steps"])
      -> hit combos [("single_turn", "multi_step")]
      -> cross closure 0.25, straight into the headline
      -> plus three "unhit" combinations, ranked at the top of the hole list the
         CDV solver aims at, none of which any scenario can ever reach.

A quarter of a dimension credited from a predicate that has never seen evidence,
and the coverpoint still honestly reporting `None` two lines above it.

That no shipped model declares such a cross is a coincidence, not a safeguard, so
the refusal is pinned here against a model constructed to do exactly that. Two
resolutions were available — refuse, or collect and exclude with a reason — and
refusal is the one that holds: `collect` sees only bin ids and cannot tell a
laundered combination from a real one, whereas the model knows which axes have no
producer. The same validator refuses the mirror-image defect, an axis whose real
bins are all waived, which reports 0.0 over an empty target set.
"""

from __future__ import annotations

import pytest

from agenttic.coverage.collect import Sample, collect
from agenttic.coverage.model import Bin, CoverageModel, Coverpoint, Cross
from agenttic.coverage.models.baseline import baseline_model
from agenttic.coverage.models.conversational_transactional import seed_model
from tests.coverage.test_tool_condition_provenance import span, trace


def _with_crosses(model: CoverageModel, crosses: list[Cross]) -> CoverageModel:
    return CoverageModel(
        model_id=model.model_id, version=model.version,
        archetype_id=model.archetype_id, coverpoints=model.coverpoints,
        crosses=crosses, closure_target=model.closure_target)


class TestTheModelRefusesTheCross:
    def test_a_cross_naming_a_not_measurable_coverpoint_is_refused(self):
        with pytest.raises(ValueError, match="not measurable"):
            _with_crosses(baseline_model(), [
                Cross(cross_id="x", coverpoints=["session_shape", "agent_steps"],
                      target="all")])

    def test_the_refusal_quotes_the_coverpoint_s_own_reason(self):
        """An error that only said "refused" would send the author to delete the
        axis without learning that the producer is the missing piece. The reason
        is already written on the coverpoint; the message carries it rather than
        restating it."""
        with pytest.raises(ValueError) as e:
            _with_crosses(baseline_model(), [
                Cross(cross_id="x", coverpoints=["agent_steps", "session_shape"],
                      target="all")])
        msg = str(e.value)
        assert "session_shape" in msg
        assert "user_turn" in msg                      # the coverpoint's reason
        assert "make it measurable first" in msg       # and the way out

    def test_an_explicit_target_list_is_refused_too(self):
        """The defect is the axis, not the target mode — an explicit combination
        list naming the same bins credits exactly the same thing."""
        with pytest.raises(ValueError, match="not measurable"):
            _with_crosses(baseline_model(), [
                Cross(cross_id="x", coverpoints=["session_shape", "agent_steps"],
                      target=[{"session_shape": "single_turn",
                               "agent_steps": "multi_step"}])])

    def test_the_seed_model_is_refused_the_same_way(self):
        """Not a baseline-only rule."""
        with pytest.raises(ValueError, match="not measurable"):
            _with_crosses(seed_model(), [
                Cross(cross_id="session_x_intent",
                      coverpoints=["session_shape", "intent"], target="all")])


class TestTheMeasuredCrossesStillBuild:
    def test_the_shipped_models_still_validate(self):
        """The rule must refuse the laundering cross and nothing else."""
        assert baseline_model().crosses
        assert len(seed_model().crosses) == 5

    def test_a_cross_over_two_measured_coverpoints_is_fine(self):
        m = _with_crosses(baseline_model(), [
            Cross(cross_id="steps_x_traj",
                  coverpoints=["agent_steps", "trajectory"], target="all")])
        rep = collect(m, [Sample(trace(span("tool_call", "get_order"),
                                       span("llm_call", "messages.create", i=1)))])
        assert 0.0 < rep.crosses["steps_x_traj"].closure < 1.0


class TestTheHeadlineIsWhatWasBeingProtected:
    def test_no_cross_can_move_the_headline_using_session_shape(self):
        """The end-to-end statement of the defect: with the cross refused, there
        is no construction that gets `session_single_turn` into the number."""
        samples = [Sample(trace(span("tool_call", "get_order"),
                                span("llm_call", "messages.create", i=1)))]
        rep = collect(baseline_model(), samples)
        assert rep.coverpoints["session_shape"].trace_closure is None
        assert not any("session_shape" in x.coverpoints
                       for x in rep.crosses.values())
        assert not [h for h in rep.holes() if "single_turn" in h.what]


class TestTheMirrorImageDefect:
    """An axis with no countable bins reports 0.0 over an empty target set — the
    under-reporting twin, and the same root cause: a cross axis with nothing to
    score."""

    def test_an_all_waived_axis_is_refused(self):
        waived_out = Coverpoint(
            coverpoint_id="nothing_left", kind="deterministic", bins=[
                Bin(bin_id="a", predicate_ref="traj_refused", waived=True,
                    reason="the harness cannot produce this condition"),
                Bin(bin_id="other")])
        base = baseline_model()
        with pytest.raises(ValueError, match="no countable bins"):
            CoverageModel(
                model_id=base.model_id, version=base.version,
                coverpoints=list(base.coverpoints) + [waived_out],
                crosses=[Cross(cross_id="y",
                               coverpoints=["nothing_left", "agent_steps"],
                               target="all")],
                closure_target=base.closure_target)
