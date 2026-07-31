"""NOT MEASURABLE has to survive the trip to the artifact, or it is a relabel.

`Coverpoint.measurable` was added to the model and stopped there: nothing
serialized it, so `session_shape` was still published at 50% closure with
`single_turn` counted as a hit — in the same report whose limits paragraph said
the dimension was not measured. A new over-report, inside the fix for the
over-report.

The declaration is only worth anything at the far end of the pipeline, so these
tests start from a trace and assert on what comes out of the *artifacts*: the
coverage report dict, the run summary the console and CLI read, the signed
sign-off, and the rendered markdown. Each one is a place the old defect was
visible.

Three distinct claims are pinned, because collapsing any two of them is how this
went wrong:

* a coverpoint nothing can feed reports NO closure — `None`, never `0.0`, and
  never a percentage computed from bins that fire by default;
* it contributes NO holes — a hole is a task, and no suite can close this one;
* it is NAMED, with its reason, in every artifact — an undisclosed exclusion
  from the denominator is a silent hole (Hard Rule 61).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from agenttic.coverage.collect import Sample, collect
from agenttic.coverage.models.baseline import baseline_model
from agenttic.coverage.targets import DEFAULT_CLOSURE_TARGET
from agenttic.ops import verify_op
from agenttic.reporting.scorecard_report import render_markdown
from agenttic.schema.rubric import Criterion, Rubric
from agenttic.schema.scorecard import CriterionScore, RunScore, Scorecard
from agenttic.schema.trace import Span, Trace

T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
RUBRIC = Rubric(rubric_id="r", criteria=[
    Criterion(criterion_id="answer", description="d", scorer="judge",
              scale="binary", anchors={"pass": "p", "fail": "f"})])


def _sp(kind: str, name: str, i: int) -> Span:
    return Span(span_id=f"s{i}", kind=kind, name=name,
                start_time=T0 + timedelta(seconds=i),
                end_time=T0 + timedelta(seconds=i + 1))


def _run() -> Trace:
    """One exchange with a tool loop — the shape every stored trace has."""
    return Trace(trace_id="t", agent_id="a", agent_config_hash="cfg",
                 test_case_id="k",
                 spans=[_sp("llm_call", "messages.create", 0),
                        _sp("tool_call", "lookup_account", 1),
                        _sp("llm_call", "messages.create", 2),
                        _sp("final_output", "final_output", 3)],
                 visibility="glass_box", final_output="balance $142.50")


def _scorecard() -> Scorecard:
    runs = [RunScore(trace_id="t", test_id="c0", passed=True,
                     criterion_scores=[CriterionScore(
                         criterion_id="answer", score=1.0, scorer="judge")],
                     cost_usd=0.001, latency_ms=1200)]
    return Scorecard.aggregate(
        scorecard_id="sc", agent_id="a", suite_id="s", suite_version=1,
        rubric_id="r", rubric_version=1, run_scores=runs,
        visibility_tier="glass_box")


def _cov():
    return collect(baseline_model(), [Sample(_run())]).coverpoints["session_shape"]


# --- 1. the coverage object ------------------------------------------------- #

class TestTheReportObjectCarriesTheDeclaration:
    def test_the_flag_and_the_reason_reach_the_coverage_object(self):
        """The model is not in the report, and every artifact is built from the
        report — so a flag that stays on the model reaches nothing."""
        cp = _cov()
        assert cp.measurable is False
        assert "user_turn" in cp.not_measurable_reason

    def test_closure_is_none_not_zero_and_not_a_percentage(self):
        cp = _cov()
        assert cp.trace_closure is None
        assert cp.stimulus_closure is None
        # the specific numbers this used to publish, both wrong
        assert cp.trace_closure != 0.0
        assert cp.trace_closure != 0.5

    def test_the_bin_that_fires_by_default_still_wins_no_closure(self):
        """`session_single_turn` returns True on every trace that will ever
        exist — 0 human turns is <= 1. The raw hit is kept as data about the
        trace; what it must never become is coverage."""
        cp = _cov()
        assert cp.bins["single_turn"].hit is True
        assert cp.trace_closure is None
        assert cp.countable() == []

    def test_unhit_is_empty(self):
        """You cannot have failed to exercise what you cannot observe."""
        assert _cov().unhit == []

    def test_every_bin_is_accounted_for_in_the_waived_list(self):
        cp = _cov()
        waived = cp.waived_bins()
        assert set(waived) == {"single_turn", "multi_turn", "resumed_with_memory"}
        assert all(v.strip() for v in waived.values())
        # the bin with its own waiver keeps its own, more specific reason
        assert "not measurable" not in waived["resumed_with_memory"]
        assert "not measurable" in waived["multi_turn"]


# --- 2. the headline ------------------------------------------------------- #

class TestTheHeadlineIsUnmoved:
    def test_it_is_out_of_the_denominator_entirely(self):
        """Not averaged in as a zero, and not averaged in as a credit: the
        headline is the number it would be if the coverpoint were absent, while
        the coverpoint itself is still reported."""
        from agenttic.coverage.model import CoverageModel
        full = baseline_model()
        without = CoverageModel(
            model_id=full.model_id, version=full.version,
            coverpoints=[c for c in full.coverpoints
                         if c.coverpoint_id != "session_shape"],
            crosses=full.crosses, closure_target=full.closure_target)
        samples = [Sample(_run())]
        headline = collect(full, samples).trace_closure
        assert headline == collect(without, samples).trace_closure
        assert 0.0 < headline < 1.0                  # the others still count
        assert "session_shape" in collect(full, samples).coverpoints

    def test_it_produces_no_holes(self):
        """A hole is a target the CDV solver aims at. This one is unreachable by
        any scenario, so pointing the solver at it would waste the whole run."""
        report = collect(baseline_model(), [Sample(_run())])
        assert [h for h in report.holes() if h.where == "session_shape"] == []
        assert [h for h in report.holes() if h.where == "trajectory"]  # others do

    def test_it_is_named_in_the_report_dict(self):
        d = collect(baseline_model(), [Sample(_run())]).as_dict()
        assert "user_turn" in d["not_measurable"]["session_shape"]
        cp = d["coverpoints"]["session_shape"]
        assert cp["not_measurable"] is True
        assert cp["trace_closure"] is None          # null on the wire, not 0
        assert cp["not_measurable_reason"]
        assert "session_shape.single_turn" in d["waived_bins"]


# --- 3. the run summary every surface reads -------------------------------- #

class TestTheRunSummary:
    def test_per_coverpoint_carries_it(self):
        _a, cov = verify_op([_run()])
        cp = cov["per_coverpoint"]["session_shape"]
        assert cp["closure"] is None
        assert cp["not_measurable"] is True
        assert "user_turn" in cp["not_measurable_reason"]
        assert cp["unhit"] == []

    def test_the_summary_names_it_at_the_top_level_too(self):
        _a, cov = verify_op([_run()])
        assert "session_shape" in cov["not_measurable"]
        assert "session_shape.multi_turn" in cov["waived_bins"]

    def test_no_hole_points_at_it(self):
        _a, cov = verify_op([_run()])
        assert [h for h in cov["holes"] if h["where"] == "session_shape"] == []

    def test_the_measured_half_of_the_split_is_reported_normally(self):
        """`agent_steps` is what the old coverpoint was really counting, and it
        is measured — the split must not have made both halves silent."""
        _a, cov = verify_op([_run()])
        steps = cov["per_coverpoint"]["agent_steps"]
        assert steps["closure"] == 0.5              # multi_step hit, single not
        assert steps["not_measurable"] is False
        assert steps["unhit"] == ["single_step"]


# --- 4. the signed artifact ------------------------------------------------ #

class TestTheSignoff:
    def test_the_leg_names_the_excluded_bins_with_reasons(self):
        _a, cov = verify_op([_run()])
        leg = cov["signoff"]["coverage"]
        assert "session_shape.single_turn" in leg["waived_bins"]
        assert all(v.strip() for v in leg["waived_bins"].values())

    def test_no_session_bin_is_listed_as_never_exercised(self):
        """The bins left the denominator; claiming the suite failed to reach
        them would be the 0%-shaped version of the same lie."""
        _a, cov = verify_op([_run()])
        leg = cov["signoff"]["coverage"]
        assert not [b for b in leg["unhit_bins"] if b.startswith("session_shape.")]

    def test_the_rendered_signoff_states_the_exclusion(self):
        from agenttic.reporting.signoff_report import render
        from agenttic.schema.signoff import VerificationSignoff
        _a, cov = verify_op([_run()])
        text = render(VerificationSignoff.model_validate(cov["signoff"]))
        assert "excluded from closure" in text
        assert "session_shape" in text


# --- 5. the client deliverable --------------------------------------------- #

class TestTheRenderedReport:
    def test_it_renders_not_measurable_rather_than_crashing(self):
        """`f"{None:.0%}"` is a TypeError. The row used to format
        `cp.get('closure', 0)`, so the honest value would have taken the whole
        report down."""
        _a, cov = verify_op([_run()])
        md = render_markdown(_scorecard().model_copy(update={"coverage": cov}),
                             RUBRIC)
        row = next(ln for ln in md.splitlines() if ln.startswith("| session_shape "))
        assert "not measurable" in row
        assert "0%" not in row and "50%" not in row

    def test_the_reason_and_the_waivers_appear_in_the_deliverable(self):
        _a, cov = verify_op([_run()])
        md = render_markdown(_scorecard().model_copy(update={"coverage": cov}),
                             RUBRIC)
        assert "user_turn" in md
        assert "Excluded from closure" in md
        assert "`session_shape.resumed_with_memory`" in md

    def test_the_report_does_not_hardcode_the_closure_target(self):
        """The target is config's (Hard Rule 7). When a stored coverage blob
        predates the field, the renderer's fallback must be the ONE definition —
        a second literal in the renderer is a second thing to forget to change."""
        import inspect

        import agenttic.reporting.scorecard_report as m
        assert "0.95" not in inspect.getsource(m)
        md = render_markdown(
            _scorecard().model_copy(update={"coverage": {
                "model_ref": "m", "trace_closure": 0.1, "closed": False}}),
            RUBRIC)
        assert f"target {DEFAULT_CLOSURE_TARGET:.0%}" in md


# --- 6. the target comes from config, not from the cwd --------------------- #

class TestConfigThreading:
    def test_verify_op_takes_the_target_from_the_cfg_it_is_handed(self):
        _a, cov = verify_op([_run()], cfg={"coverage": {"closure_target": 0.6}})
        assert cov["closure_target"] == 0.6
        assert cov["signoff"]["coverage"]["closure_target"] == 0.6

    def test_the_default_still_applies_with_no_cfg(self):
        _a, cov = verify_op([_run()])
        assert cov["closure_target"] == DEFAULT_CLOSURE_TARGET

    def test_a_nonsense_configured_target_does_not_take_the_run_down(self):
        """A coverage model that cannot be built is a run with no coverage at
        all — strictly worse than one measured against the documented default."""
        _a, cov = verify_op([_run()], cfg={"coverage": {"closure_target": "loads"}})
        assert cov["closure_target"] == DEFAULT_CLOSURE_TARGET


# --- 7. the schema prerequisite ------------------------------------------- #

class TestTheBinIsConstructibleButStillNotProduced:
    def test_a_user_turn_span_can_exist(self):
        """Before the bump, `SpanKind` had no `user_turn`, so the bin was
        UNCONSTRUCTIBLE — `session_single_turn` was True by construction on every
        trace that could ever be built."""
        Span(span_id="s", kind="user_turn", name="customer",
             start_time=T0, end_time=T0)

    def test_constructible_is_not_measured(self):
        """The distinction the whole change rests on. Adding the kind does not
        add a producer, so the coverpoint stays declared not-measurable — and
        will until a session runner emits one."""
        assert baseline_model().coverpoint("session_shape").measurable is False

    def test_a_trace_that_does_have_two_human_turns_scores_the_bin(self):
        """...and when a producer exists, the predicate is already right: no
        second correction is needed, only the flag."""
        from agenttic.coverage.extractors import run_predicate
        t = Trace(trace_id="t2", agent_id="a", agent_config_hash="cfg",
                  test_case_id="k",
                  spans=[_sp("user_turn", "customer", 0),
                         _sp("llm_call", "messages.create", 1),
                         _sp("user_turn", "customer", 2),
                         _sp("llm_call", "messages.create", 3)],
                  visibility="glass_box", final_output="done")
        assert run_predicate("session_multi_turn", t) is True

    def test_env_step_exists_for_what_the_environment_does_on_its_own(self):
        """A fault the harness injected must have somewhere to live that is not
        `tool_call` — an injected timeout is not something the agent did."""
        Span(span_id="s", kind="env_step", name="inject:timeout",
             start_time=T0, end_time=T0)

    def test_an_unknown_span_kind_is_still_refused(self):
        with pytest.raises(ValueError):
            Span(span_id="s", kind="telepathy", name="x",
                 start_time=T0, end_time=T0)
