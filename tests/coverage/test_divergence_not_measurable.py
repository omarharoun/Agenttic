"""`divergence()` was the one place still printing a hard zero for a `None` closure.

The stimulus-vs-trace divergence list answers a specific question: *the generator
asked for this corner and the run failed to deliver it.* Every row carries
``"exhibited": 0``, and that zero is a measurement — it accuses the producer of
missing a target it aimed at.

A not-measurable coverpoint cannot be aimed at. Nothing emits the evidence, so
"requested 1, exhibited 0" blames the generator for a hole in the instrumentation,
and it re-states as a flat zero the very bin whose closure the round-2 repair made
``None`` at nine other surfaces. `holes()` already skipped these coverpoints, for
exactly the same reason and in almost the same words; `divergence()` did not.

This is a GUARD, not a repair of live output: no shipped stimulus space requests a
``session_shape`` bin, because seed_space v2 deleted the dimension. But that is an
accident of another decision — a hand-built ``Sample(requested=…)`` produced the
row immediately, and nothing in the code stopped the dimension coming back. The
test is written against the hand-built sample so it keeps holding if it does.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from agenttic.coverage.collect import Sample, collect
from agenttic.coverage.model import Bin, CoverageModel, Coverpoint
from agenttic.coverage.models.baseline import baseline_model
from agenttic.coverage.models.conversational_transactional import seed_model
from agenttic.schema.trace import Span, Trace

T0 = datetime(2026, 7, 26, 12, 0, 0)


def _sp(i: int, kind: str, name: str, *, out=None) -> Span:
    return Span(span_id=f"s{i}", kind=kind, name=name,
                start_time=T0 + timedelta(seconds=i),
                end_time=T0 + timedelta(seconds=i + 1),
                input={}, output=out or {})


def _trace() -> Trace:
    return Trace(trace_id="t1", agent_id="a", agent_config_hash="c",
                 test_case_id=None, visibility="glass_box",
                 spans=[_sp(0, "llm_call", "plan"),
                        _sp(1, "final_output", "r", out={"text": "x"})],
                 final_output="ok", total_steps=2)


def _report(requested: dict[str, str]):
    return collect(baseline_model(), [Sample(trace=_trace(), requested=requested)])


class TestANotMeasurableCoverpointCannotDiverge:
    def test_a_requested_session_shape_bin_produces_no_divergence_row(self):
        rep = _report({"session_shape": "multi_turn"})
        assert rep.coverpoints["session_shape"].measurable is False
        assert [d for d in rep.divergence()
                if d["coverpoint_id"] == "session_shape"] == []

    def test_it_is_the_same_answer_holes_already_gave(self):
        """Two lists, one rule. They disagreed, and the disagreement was the bug:
        `holes()` skipped the coverpoint and `divergence()` reported it at zero."""
        rep = _report({"session_shape": "multi_turn"})
        assert [h for h in rep.holes() if h.where == "session_shape"] == []
        assert [d for d in rep.divergence()
                if d["coverpoint_id"] == "session_shape"] == []

    def test_the_serialized_report_carries_no_such_row_either(self):
        """`as_dict` is what reaches an artifact; a row filtered only in the
        method would still ship."""
        d = _report({"session_shape": "multi_turn"}).as_dict()
        assert "stimulus_vs_trace_divergence" in d      # the key still exists
        assert all(row["coverpoint_id"] != "session_shape"
                   for row in d["stimulus_vs_trace_divergence"])

    def test_the_dimension_is_still_disclosed_just_not_as_a_zero(self):
        """Skipping the row is not hiding the gap: it is named as not measurable,
        with its reason, and its bins are in the waived list. The correction moves
        the disclosure to where it is true, it does not delete it."""
        rep = _report({"session_shape": "multi_turn"})
        assert "session_shape" in rep.not_measurable
        assert rep.not_measurable["session_shape"].strip()
        assert any(b.startswith("session_shape.") for b in rep.waived_bins())


class TestAMeasurableCoverpointStillDiverges:
    def test_a_requested_tool_condition_that_never_fired_is_still_reported(self):
        """The check earns its place: this is coverage theater when it is missed —
        the generator asked for a timeout, the timeout never fired, and counting
        that as covered is the whole anti-pattern."""
        rep = _report({"tool_condition": "timeout"})
        assert any(d["coverpoint_id"] == "tool_condition"
                   and d["bin_id"] == "timeout" and d["exhibited"] == 0
                   for d in rep.divergence())

    def test_the_skip_is_measurability_not_the_absence_of_a_hit(self):
        """Guard against the fix being written as "skip anything with no trace
        hits", which would delete the divergence list's entire purpose."""
        rep = _report({"trajectory": "escalated_to_human"})
        assert rep.coverpoints["trajectory"].measurable is True
        assert any(d["coverpoint_id"] == "trajectory"
                   and d["bin_id"] == "escalated_to_human"
                   for d in rep.divergence())


# --------------------------------------------------------------------------- #
# Round 3: `measurable` was ONE of five exclusions, and the fix only knew that one
#
# `countable()` (collect.py) removes a bin from the closure denominator for five
# reasons — the coverpoint is not measurable, or the bin is `other`, illegal,
# waived, or CLASSIFIER-BACKED WITH NO EVALUATOR SUPPLIED. `divergence()` copied
# the first test and none of the other four, so it kept printing `requested 1,
# exhibited 0` for bins nothing had measured.
#
# Unlike `session_shape`, the last one is not a guard against a dimension coming
# back — it is live on every scenario run this build performs. Both shipped
# producers collect with `classify=None` on purpose ("deterministic bins only, no
# model calls" — scenario/runner.py, cli.py), so on a fitted model EVERY semantic
# bin is `unevaluated`, and every point that pins `intent` / `emotional_register`
# / `policy_vector` invented a divergence row for it.
#
# What makes that worse than a lone false row is the company it keeps: a REAL
# divergence on a deterministic dimension sits in the same list, under the same
# glyph and the same counts, so a reader cannot tell the finding from the
# fabrication. These tests assert the real one survives and the invented ones do
# not, on ONE report.
# --------------------------------------------------------------------------- #

def _fitted_trace() -> Trace:
    """A clean, ordinary run: one tool call that worked, one model call, an
    answer. Nothing about it is semantic, which is the point."""
    return Trace(trace_id="t2", agent_id="a", agent_config_hash="c",
                 test_case_id=None, visibility="glass_box",
                 spans=[_sp(0, "tool_call", "get_order", out={"ok": True}),
                        _sp(1, "llm_call", "plan"),
                        _sp(2, "final_output", "r", out={"text": "here you go"})],
                 final_output="here you go", total_steps=3)


def _fitted_report(requested: dict[str, str], *, classify=None):
    """The fitted archetype model, collected the way the product collects it."""
    return collect(seed_model(),
                   [Sample(trace=_fitted_trace(), scenario={},
                           requested=requested)],
                   classify=classify)


#: one semantic pin and one deterministic pin, exactly as a stimulus point makes
#: them. Only the deterministic one can be a finding on a `classify=None` run.
_MIXED_POINT = {"intent": "complaint",
                "emotional_register": "hostile",
                "policy_vector": "edge_of_policy",
                "data_condition": "ambiguous"}


class TestAnUnevaluatedClassifierBinCannotDiverge:
    def test_the_precondition_the_whole_defect_rests_on(self):
        """Stated as an assertion rather than a comment: with no evaluator the
        semantic bins carry a stimulus hit, are flagged unevaluated, and are out
        of the denominator. If any of that stops being true the tests below stop
        meaning what they say."""
        rep = _fitted_report(_MIXED_POINT)
        for cp_id, bin_id in [("intent", "complaint"),
                              ("emotional_register", "hostile"),
                              ("policy_vector", "edge_of_policy")]:
            cov = rep.coverpoints[cp_id]
            assert cov.measurable is True, cp_id      # NOT the round-2 exclusion
            assert cov.bins[bin_id].unevaluated is True
            assert cov.bins[bin_id].stimulus_hits == 1
            assert cov.countable() == []

    def test_no_divergence_row_is_invented_for_a_bin_nobody_evaluated(self):
        rep = _fitted_report(_MIXED_POINT)
        assert [d for d in rep.divergence()
                if d["coverpoint_id"] in ("intent", "emotional_register",
                                          "policy_vector")] == []

    def test_it_is_the_same_answer_holes_already_gave(self):
        """The two lists are one rule. `holes()` goes through `unhit` ->
        `countable()` and emits nothing for these bins, so the CDV solver is
        never aimed at them — while `divergence()` blamed the generator for
        missing them. That contradiction WAS the bug."""
        rep = _fitted_report(_MIXED_POINT)
        semantic = ("intent", "emotional_register", "policy_vector")
        assert [h for h in rep.holes() if h.where in semantic] == []
        assert [d for d in rep.divergence()
                if d["coverpoint_id"] in semantic] == []

    def test_the_one_real_finding_is_not_buried_among_invented_ones(self):
        """The acceptance criterion, stated on one report: the deterministic
        divergence is real (the point asked for ambiguous data and the run
        produced none) and it is the ONLY row. Three fabrications wearing the
        same glyph and the same counts made it unreadable."""
        rep = _fitted_report(_MIXED_POINT)
        assert rep.divergence() == [
            {"coverpoint_id": "data_condition", "bin_id": "ambiguous",
             "requested": 1, "exhibited": 0}]

    def test_the_serialized_report_carries_no_such_row_either(self):
        """`as_dict` is what is stored on the run and rendered by the CLI and the
        console; a row filtered only in the method would still ship."""
        d = _fitted_report(_MIXED_POINT).as_dict()
        assert [r["coverpoint_id"] for r in d["stimulus_vs_trace_divergence"]] \
            == ["data_condition"]


class TestTheRequestIsMovedNotDeleted:
    """Removing the row must not turn an over-report into silence. The point
    really did aim at those corners; the fact is that nobody looked."""

    def test_the_unmeasured_request_is_still_reported_with_its_reason(self):
        rows = _fitted_report(_MIXED_POINT).unmeasured_requests()
        assert [(r["coverpoint_id"], r["bin_id"]) for r in rows] == [
            ("emotional_register", "hostile"),
            ("intent", "complaint"),
            ("policy_vector", "edge_of_policy")]
        for r in rows:
            assert r["requested"] == 1
            assert "never evaluated" in r["reason"].lower()

    def test_it_reaches_the_serialized_report(self):
        d = _fitted_report(_MIXED_POINT).as_dict()
        assert [r["bin_id"] for r in d["stimulus_requested_not_measured"]] == [
            "hostile", "complaint", "edge_of_policy"]

    def test_the_two_states_do_not_look_the_same(self):
        """The acceptance criterion for this whole defect family. "we asked and
        the run did not deliver" and "we asked and nobody looked" must not be
        renderable as one string: one row carries a hard `exhibited` count and no
        reason, the other carries a reason and NO exhibited field at all —
        because there is no such number."""
        rep = _fitted_report(_MIXED_POINT)
        div, un = rep.divergence()[0], rep.unmeasured_requests()[0]
        assert div["exhibited"] == 0 and "reason" not in div
        assert "exhibited" not in un and un["reason"].strip()
        assert set(div) != set(un)

    def test_a_not_measurable_coverpoint_lands_here_too(self):
        """Round 2 removed the `session_shape` row and left the disclosure to
        `not_measurable` / `waived_bins`. It is now ALSO named here, in the list
        that answers the specific question "what happened to the corner the point
        asked for?" — one question, one place to look."""
        rep = _report({"session_shape": "multi_turn"})
        rows = [r for r in rep.unmeasured_requests()
                if r["coverpoint_id"] == "session_shape"]
        assert [r["bin_id"] for r in rows] == ["multi_turn"]
        assert "not measurable" in rows[0]["reason"]


class TestTheOtherThreeExclusionsAreExcludedToo:
    """`waived`, `illegal` and `other` are the remaining three conditions
    `countable()` applies and `divergence()` did not. No shipped model puts a
    waived or illegal bin on a MEASURABLE coverpoint today, so these are guards
    of the same kind the round-2 test was — written against a hand-built model so
    they hold when one does."""

    @staticmethod
    def _model() -> CoverageModel:
        return CoverageModel(
            model_id="cov-test-divergence-exclusions", version=1,
            coverpoints=[Coverpoint(
                coverpoint_id="shape", kind="deterministic", bins=[
                    Bin(bin_id="direct", predicate_ref="traj_direct_answer"),
                    Bin(bin_id="escalated",
                        predicate_ref="traj_escalated_to_human"),
                    Bin(bin_id="refused", predicate_ref="traj_refused",
                        waived=True,
                        reason="nothing in this build refuses a request"),
                    Bin(bin_id="over_budget",
                        predicate_ref="traj_budget_exceeded", illegal=True),
                    Bin(bin_id="other")])])

    def _rows(self, bin_id: str):
        rep = collect(self._model(),
                      [Sample(trace=_trace(), scenario={},
                              requested={"shape": bin_id})])
        return rep.divergence(), rep.unmeasured_requests()

    def test_a_waived_bin_the_point_asked_for_is_not_a_divergence(self):
        div, un = self._rows("refused")
        assert div == []
        assert [(r["bin_id"], r["reason"]) for r in un] == [
            ("refused", "nothing in this build refuses a request")]

    def test_an_illegal_bin_the_point_asked_for_is_not_a_divergence(self):
        """Not merely uncounted — INVERTED. A run that failed to produce an
        illegal corner did the right thing, so reporting it as a corner the
        generator missed is a finding pointing the wrong way."""
        div, un = self._rows("over_budget")
        assert div == []
        assert [r["bin_id"] for r in un] == ["over_budget"]
        assert "illegal" in un[0]["reason"].lower()

    def test_the_other_bin_is_not_a_coverage_target(self):
        div, un = self._rows("other")
        assert div == []
        assert [r["bin_id"] for r in un] == ["other"]

    def test_a_real_bin_on_the_same_model_still_diverges(self):
        """The guard on the guard: the exclusions above are about WHY a bin left
        the denominator, never about the absence of a trace hit."""
        div, un = self._rows("escalated")
        assert div == [{"coverpoint_id": "shape", "bin_id": "escalated",
                        "requested": 1, "exhibited": 0}]
        assert un == []


class TestSupplyingAnEvaluatorRestoresTheFinding:
    """The fix must be "the bin was never evaluated", never "classifier bins are
    exempt". Hand `collect` an evaluator and the same request on the same trace
    is a real divergence again — the semantic corner was asked for, it WAS
    looked for, and it was not there."""

    def test_an_evaluated_classifier_bin_that_never_fired_is_a_divergence(self):
        rep = _fitted_report(_MIXED_POINT,
                             classify=lambda clf, tr, scen: False)
        assert rep.coverpoints["intent"].bins["complaint"].unevaluated is False
        assert any(d["coverpoint_id"] == "intent" and d["bin_id"] == "complaint"
                   and d["exhibited"] == 0 for d in rep.divergence())

    def test_and_nothing_is_reported_as_unmeasured(self):
        rep = _fitted_report(_MIXED_POINT,
                             classify=lambda clf, tr, scen: False)
        semantic = ("intent", "emotional_register", "policy_vector")
        assert [r for r in rep.unmeasured_requests()
                if r["coverpoint_id"] in semantic] == []
