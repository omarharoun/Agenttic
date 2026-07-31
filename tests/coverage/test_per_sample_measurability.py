"""Measurability is a fact about a RUN, and it used to be declared per model.

The contradiction this closes was written down in the source, in two files, by
the same change:

* ``extractors.session_single_turn`` is ``_human_turns(trace) <= 1``, which is
  TRUE for a trace with zero turn markers — so an uninstrumented run is credited
  single-turn;
* the coverpoint's own ``not_measurable_reason`` says the opposite in words: *"a
  trace with no turn markers is evidence of absent instrumentation, not of a
  single-turn session."*

Both statements are true, about different traces. The per-model ``measurable``
flag could not say so, because it answers once for a whole batch — and it hid the
disagreement rather than resolving it, since an excluded coverpoint's predicate
never reaches a number. That stopped being free when ``scenario/session.py``
started emitting ``user_turn``: some batches are instrumented now and some are
not, and one flag has to be wrong about one of them.

Two repairs were tried and rejected before this one, and both are pinned here as
NEGATIVE tests so they are not re-attempted:

* tightening the predicate to ``== 1`` — with neither turn bin firing, an
  uninstrumented trace lands in ``other``, whose drift is reported as *"the model
  is missing a dimension"*. The model is fine; the run is uninstrumented. Two
  different findings (:class:`TestUninstrumentedIsNotModelDrift`);
* flipping the flag measurable — an ordinary suite run then reports
  ``session_shape`` at 0% closure on a dimension its harness cannot emit
  (:class:`TestAnUninstrumentedBatchIsUnchanged`).

What replaces them: ``Coverpoint.measurable_when`` names a registered gate, and
``collect`` asks it per sample. Closure is computed over the samples that carried
the instrumentation, the count that did not is reported beside it, and a gate no
sample passes leaves the coverpoint reading exactly as a flatly not-measurable
one did.

The gate is declared on a model built HERE, not on the shipped one. Adding
``measurable_when="session_turns_instrumented"`` to
``models/conversational_transactional.py``'s ``SESSION_SHAPE`` is a one-line
declaration in a file this change does not own; these tests exercise the
mechanism end to end against a model that declares it, so the declaration is the
only thing left to make.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone

import pytest

from agenttic.coverage.collect import Sample, collect
from agenttic.coverage.extractors import (
    MEASURABILITY, PREDICATES, UnknownMeasurabilityError, is_measurable,
    run_predicate)
from agenttic.coverage.model import Bin, CoverageModel, Coverpoint, Cross
from agenttic.coverage.models.baseline import baseline_model
from agenttic.coverage.models.conversational_transactional import seed_model
from agenttic.schema.trace import Span, Trace

T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
TARGET = 0.95           # explicit, so no test here depends on the cwd's config
GATE = "session_turns_instrumented"


# --------------------------------------------------------------------------- #
# fixtures — two run shapes, one instrumented and one not
# --------------------------------------------------------------------------- #

def sp(kind: str, name: str, i: int, **kw) -> Span:
    return Span(span_id=f"s{i}", kind=kind, name=name,
                start_time=T0 + timedelta(seconds=i),
                end_time=T0 + timedelta(seconds=i + 1), **kw)


def trace(*spans: Span, tid: str = "t", final_output: str = "balance $142.50"):
    fixed = [s.model_copy(update={"span_id": f"{tid}-{i}"})
             for i, s in enumerate(spans)]
    return Trace(trace_id=tid, agent_id="a", agent_config_hash="cfg",
                 test_case_id="k", spans=fixed, visibility="glass_box",
                 final_output=final_output)


def ticket(i: int = 0) -> Trace:
    """A stored-suite run: one dict handed to the agent once, a tool loop, done.

    The shape every trace on the standard path has, and the reason the coverpoint
    was declared not measurable in the first place — nothing here records who
    spoke.
    """
    return trace(sp("llm_call", "messages.create", 0),
                 sp("tool_call", "lookup_account", 1),
                 sp("llm_call", "messages.create", 2),
                 sp("final_output", "final_output", 3), tid=f"ticket{i}")


def conversation(i: int = 0, turns: int = 2) -> Trace:
    """A session driven by ``scenario/user.py``: the counterparty spoke ``turns``
    times, and the session stamped a ``user_turn`` span before each delivery."""
    spans: list[Span] = []
    for t in range(turns):
        spans.append(sp("user_turn", "customer", len(spans)))
        spans.append(sp("llm_call", "messages.create", len(spans)))
    spans.append(sp("final_output", "final_output", len(spans)))
    return trace(*spans, tid=f"convo{i}")


# --------------------------------------------------------------------------- #
# models — the shipped one, and the same one with the gate declared
# --------------------------------------------------------------------------- #

REASON = ("a run that records no `user_turn` span cannot be read for session "
          "shape: a trace with no turn markers is evidence of absent "
          "instrumentation, not of a single-turn session")


def gated_session_shape(**overrides) -> Coverpoint:
    src = baseline_model(closure_target=TARGET).coverpoint("session_shape")
    kw = dict(coverpoint_id=src.coverpoint_id, description=src.description,
              kind=src.kind, bins=src.bins, measurable=False,
              not_measurable_reason=REASON, measurable_when=GATE)
    kw.update(overrides)
    return Coverpoint(**kw)


def gated_model(cp: Coverpoint | None = None,
                crosses: list[Cross] | None = None) -> CoverageModel:
    """``baseline_model`` with ``session_shape`` gated — the one-line declaration
    this mechanism is waiting on, made here so the mechanism can be measured."""
    base = baseline_model(closure_target=TARGET)
    cp = cp or gated_session_shape()
    return CoverageModel(
        model_id=base.model_id, version=base.version,
        archetype_id=base.archetype_id,
        coverpoints=[cp if c.coverpoint_id == "session_shape" else c
                     for c in base.coverpoints],
        crosses=base.crosses if crosses is None else crosses,
        closure_target=base.closure_target)


def session_cp(model: CoverageModel, traces):
    return collect(model, [Sample(t) for t in traces]).coverpoints["session_shape"]


# --------------------------------------------------------------------------- #
# 1. the gate reads instrumentation, and only instrumentation
# --------------------------------------------------------------------------- #

class TestTheGate:
    def test_a_ticket_carries_no_turn_markers(self):
        assert is_measurable(GATE, ticket()) is False

    def test_one_turn_marker_is_enough(self):
        """A conversation that delivered exactly one message emits exactly one
        marker. It is single-turn ON EVIDENCE, which is the whole distinction —
        the same bin, reached for a reason instead of by default."""
        one = conversation(turns=1)
        assert is_measurable(GATE, one) is True
        assert run_predicate("session_single_turn", one) is True

    def test_a_conversation_is_measurable(self):
        assert is_measurable(GATE, conversation(turns=3)) is True

    def test_a_tool_named_after_the_span_kind_is_not_a_turn(self):
        """The gate reads ``Span.kind``, never a span NAME.

        Name-matching here would hand the gate to any agent that names a tool
        after the thing being measured — the exact defect that let a
        ``memory_lookup`` tool claim `session_resumed_with_memory`, and the
        substring family the review keeps finding.
        """
        t = trace(sp("tool_call", "user_turn", 0),
                  sp("tool_call", "fetch_user_turn_history", 1),
                  sp("llm_call", "summarize_user_turns", 2),
                  sp("retrieval", "user_turn_faq", 3))
        assert is_measurable(GATE, t) is False

    def test_an_env_step_is_not_the_counterparty_speaking(self):
        """`env_step` is the environment acting on its own account. A fault
        injector firing is not a human turn, and a gate that accepted any
        non-agent span would credit one."""
        assert is_measurable(GATE, trace(sp("env_step", "inject:timeout", 0),
                                         sp("llm_call", "messages.create", 1))
                             ) is False

    def test_an_unregistered_gate_is_a_loud_error(self):
        with pytest.raises(UnknownMeasurabilityError):
            is_measurable("no_such_gate", ticket())


class TestTheTwoRegistriesAreSeparate:
    """A gate and a bin predicate have identical shapes, so a model naming one
    where it means the other would run forever and return plausible booleans."""

    def test_a_gate_is_not_a_bin_predicate(self):
        assert GATE in MEASURABILITY
        assert GATE not in PREDICATES

    def test_a_bin_predicate_is_not_a_gate(self):
        assert "session_single_turn" in PREDICATES
        assert "session_single_turn" not in MEASURABILITY

    def test_naming_a_bin_predicate_as_a_gate_is_refused_at_validation(self):
        m = gated_model(gated_session_shape(measurable_when="traj_refused"))
        with pytest.raises(ValueError, match="unregistered measurability gate"):
            m.validate_against_registry()

    def test_collect_refuses_it_too(self):
        """``collect`` validates first, so the error cannot be deferred to a
        wrong number in a report."""
        m = gated_model(gated_session_shape(measurable_when="traj_refused"))
        with pytest.raises(ValueError, match="unregistered measurability gate"):
            collect(m, [Sample(ticket())])


# --------------------------------------------------------------------------- #
# 2. declaring the gate
# --------------------------------------------------------------------------- #

class TestTheDeclaration:
    def test_a_gated_coverpoint_may_not_also_claim_to_be_measurable(self):
        """`measurable=True` beside a gate would put the coverpoint in the
        headline unconditionally and then quietly narrow its denominator — a
        closure over a subset, presented as one over the batch."""
        with pytest.raises(ValueError, match="decided per sample"):
            gated_session_shape(measurable=True, not_measurable_reason="")

    def test_a_gate_still_requires_the_named_reason(self):
        """Hard Rule 61 is unchanged: a sample that fails the gate leaves the
        denominator, and a hole is never silent."""
        with pytest.raises(ValueError, match="named reason"):
            gated_session_shape(not_measurable_reason="")

    def test_an_empty_gate_name_is_refused(self):
        with pytest.raises(ValueError, match="measurable_when"):
            gated_session_shape(measurable_when="   ")

    def test_the_model_level_flag_stays_false(self):
        """The floor, not the verdict. A caller that never looks at the gate —
        ``stimulus/derive.py`` builds a space from the MODEL, not from a report —
        keeps seeing the conservative answer."""
        cp = gated_session_shape()
        assert cp.measurable is False
        assert cp.required is False
        assert cp.measurable_when == GATE

    def test_a_cross_naming_a_gated_axis_is_still_refused(self):
        """Per-sample measurability fixes the coverpoint's own number and does
        nothing for a cross: combinations are built from raw per-sample hits
        across all axes at once, so an uninstrumented sample still contributes
        ``single_turn`` there. The laundering route is open exactly as before."""
        with pytest.raises(ValueError, match="not measurable"):
            gated_model(crosses=[Cross(cross_id="x",
                                       coverpoints=["session_shape",
                                                    "agent_steps"],
                                       target="all")])


# --------------------------------------------------------------------------- #
# 3. the fingerprint does not churn
# --------------------------------------------------------------------------- #

def _legacy_fingerprint(model: CoverageModel) -> str:
    """``bins_fingerprint()`` exactly as it was built before ``measurable_when``
    existed.

    Replicated rather than pinned as a hex literal on purpose: a literal would
    go stale the day anybody legitimately adds a bin to a shipped model, and this
    equality keeps proving the thing that matters — that a model declaring no
    gate hashes byte-for-byte the way it always did, so no stored scorecard is
    invalidated by the field's mere existence.
    """
    payload = [
        {"cp": c.coverpoint_id, "kind": c.kind, "measurable": c.measurable,
         "bins": sorted(
             [{"id": b.bin_id, "pred": b.predicate_ref,
               "classifier": bool(b.classifier), "illegal": b.illegal,
               "waived": b.waived} for b in c.bins],
             key=lambda d: d["id"])}
        for c in sorted(model.coverpoints, key=lambda c: c.coverpoint_id)
    ]
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


class TestTheFingerprint:
    def test_an_ungated_model_hashes_exactly_as_it_did_before(self):
        """The constraint the whole mechanism had to fit inside: churn here
        invalidates every stored scorecard, and it would be churn recording a
        change these models did not make."""
        for m in (baseline_model(closure_target=TARGET),
                  seed_model(closure_target=TARGET)):
            assert m.bins_fingerprint() == _legacy_fingerprint(m)

    def test_declaring_a_gate_does_change_it(self):
        """It is the same act as flipping ``measurable``, made conditional — a
        diff a human approves, never a silent edit."""
        before = baseline_model(closure_target=TARGET).bins_fingerprint()
        after = gated_model().bins_fingerprint()
        assert after != before
        assert after != _legacy_fingerprint(gated_model())

    def test_two_different_gates_hash_differently(self):
        """The gate NAME is in the payload, not merely the fact of one: swapping
        the rule that decides admissibility changes what closure means."""
        other = gated_model(gated_session_shape(measurable_when="__probe__"))
        assert other.bins_fingerprint() != gated_model().bins_fingerprint()


# --------------------------------------------------------------------------- #
# 4. an uninstrumented batch is bit-for-bit what it is today
# --------------------------------------------------------------------------- #

class TestAnUninstrumentedBatchIsUnchanged:
    """The rejected repair, pinned: flipping the flag measurable would report
    ``session_shape`` at 0% closure to every customer whose harness cannot emit a
    turn. A gate no sample passes must read exactly as the flat declaration did.
    """

    def test_the_coverpoint_still_reads_not_measurable(self):
        cp = session_cp(gated_model(), [ticket(i) for i in range(4)])
        assert cp.measurable is False
        assert cp.required is False
        assert cp.trace_closure is None
        assert cp.stimulus_closure is None
        assert cp.unhit == []
        assert cp.countable() == []

    def test_the_raw_hit_is_still_visible_and_still_credits_nothing(self):
        """`session_single_turn` fires on every trace ever written. That fact is
        kept as data about the batch — hiding it would make the defect
        unobservable — and it reaches no number."""
        cp = session_cp(gated_model(), [ticket(i) for i in range(4)])
        assert cp.bins["single_turn"].trace_hits == 4
        assert cp.bins["single_turn"].measurable_hits == 0
        assert cp.exhibited(cp.bins["single_turn"]) == 0

    def test_every_bin_is_still_named_in_the_waived_list_with_a_reason(self):
        cp = session_cp(gated_model(), [ticket()])
        waived = cp.waived_bins()
        assert set(waived) == {"single_turn", "multi_turn", "resumed_with_memory"}
        assert all(v.strip() for v in waived.values())

    def test_the_disclosure_is_still_in_as_dict_and_still_names_the_reason(self):
        d = collect(gated_model(), [Sample(ticket())]).as_dict()
        assert "user_turn" in d["not_measurable"]["session_shape"]
        cp = d["coverpoints"]["session_shape"]
        assert cp["not_measurable"] is True
        assert cp["trace_closure"] is None          # null on the wire, not 0
        assert cp["not_measurable_reason"].strip()
        assert "session_shape.single_turn" in d["waived_bins"]

    def test_it_produces_no_holes_and_no_partial_disclosure(self):
        rep = collect(gated_model(), [Sample(ticket(i)) for i in range(4)])
        assert [h for h in rep.holes() if h.where == "session_shape"] == []
        assert rep.partial_measurability() == {}
        assert "measured on part of the batch" not in rep.headline()

    def test_the_headline_is_the_number_it_is_today(self):
        """The load-bearing before/after: an existing single-turn-only batch must
        produce the SAME ``trace_closure``. Asserted as an equality against the
        shipped model rather than a pinned decimal, so it keeps proving the
        invariant if a predicate elsewhere legitimately moves both sides. It was
        0.2077 on this fixture when written.
        """
        batch = [Sample(ticket(i)) for i in range(4)]
        shipped = collect(baseline_model(closure_target=TARGET), batch)
        gated = collect(gated_model(), batch)
        assert gated.trace_closure == shipped.trace_closure
        assert gated.stimulus_closure == shipped.stimulus_closure
        assert gated.other_drift() == shipped.other_drift()
        assert [(h.where, h.what) for h in gated.holes()] == \
               [(h.where, h.what) for h in shipped.holes()]
        assert 0.0 < gated.trace_closure < 1.0      # the others still count


# --------------------------------------------------------------------------- #
# 5. an instrumented batch reports a real closure
# --------------------------------------------------------------------------- #

class TestAnInstrumentedBatchIsMeasured:
    def test_it_becomes_measurable_and_reports_a_number(self):
        cp = session_cp(gated_model(), [conversation(i) for i in range(3)])
        assert cp.measurable is True
        assert cp.n_measurable == 3 and cp.n_unmeasurable == 0
        # two countable bins (resumed_with_memory is waived); multi_turn hit
        assert cp.trace_closure == 0.5
        assert cp.unhit == ["single_turn"]

    def test_a_single_turn_conversation_credits_single_turn_on_evidence(self):
        cp = session_cp(gated_model(), [conversation(i, turns=1)
                                        for i in range(3)])
        assert cp.measurable is True
        assert cp.bins["single_turn"].measurable_hits == 3
        assert cp.trace_closure == 0.5
        assert cp.unhit == ["multi_turn"]

    def test_it_enters_the_headline_rather_than_reporting_a_number_nothing_uses(
            self):
        """``required`` and ``measurable`` are one decision. A gated coverpoint
        that reported a closure the headline ignored would be decorative — the
        mechanism has to have teeth on the batch that earns it."""
        batch = [Sample(conversation(i)) for i in range(3)]
        gated = collect(gated_model(), batch)
        shipped = collect(baseline_model(closure_target=TARGET), batch)
        assert gated.coverpoints["session_shape"].required is True
        assert gated.trace_closure != shipped.trace_closure

    def test_it_now_produces_a_hole_the_solver_can_actually_aim_at(self):
        """The other half of teeth: `multi_turn` unhit on an instrumented batch
        is a corner a scenario CAN reach, so it is a task and not a confession."""
        rep = collect(gated_model(), [Sample(conversation(i, turns=1))
                                      for i in range(3)])
        assert [h.what for h in rep.holes() if h.where == "session_shape"] \
            == ["multi_turn"]


# --------------------------------------------------------------------------- #
# 6. a mixed batch — the case one flag could never describe
# --------------------------------------------------------------------------- #

class TestAMixedBatch:
    """Three of forty runs instrumented and forty of forty are different facts
    about the same closure number, and only one of them is worth acting on."""

    def batch(self, instrumented: int = 3, tickets: int = 37):
        return ([Sample(conversation(i)) for i in range(instrumented)]
                + [Sample(ticket(i)) for i in range(tickets)])

    def test_closure_is_computed_over_the_instrumented_subset_only(self):
        """The anti-pollution assertion. Raw `single_turn` fires on all 37
        tickets; not one of them may reach the number, so the bin reads UNHIT
        while its raw counter reads 37."""
        cp = collect(gated_model(), self.batch()).coverpoints["session_shape"]
        assert cp.measurable is True
        assert cp.bins["single_turn"].trace_hits == 37
        assert cp.bins["single_turn"].measurable_hits == 0
        assert cp.bins["multi_turn"].measurable_hits == 3
        assert cp.trace_closure == 0.5
        assert cp.unhit == ["single_turn"]

    def test_the_count_it_could_not_measure_is_reported_beside_the_number(self):
        rep = collect(gated_model(), self.batch())
        cp = rep.coverpoints["session_shape"]
        assert (cp.n_measurable, cp.n_unmeasurable) == (3, 37)
        assert rep.n_samples == 40
        assert rep.partial_measurability() == {
            "session_shape": {"measured_over": 3, "not_measurable_samples": 37,
                              "samples": 40, "reason": REASON}}

    def test_the_disclosure_reaches_the_serialized_report(self):
        """``as_dict`` is what every artifact is built from; a count that lives
        only on the object reaches nothing."""
        d = collect(gated_model(), self.batch()).as_dict()
        cp = d["coverpoints"]["session_shape"]
        assert cp["trace_closure"] == 0.5
        assert cp["not_measurable"] is False       # a number exists
        assert cp["samples_measured_over"] == 3
        assert cp["samples_not_measurable"] == 37
        assert d["partially_measurable"]["session_shape"][
            "not_measurable_samples"] == 37

    def test_the_headline_says_which_fraction_the_number_rests_on(self):
        head = collect(gated_model(), self.batch()).headline()
        assert "measured on part of the batch" in head
        assert "session_shape on 3 of 40" in head

    def test_a_fully_instrumented_batch_discloses_nothing_extra(self):
        """There is nothing to confess when every sample was readable, and a
        disclosure printed unconditionally is noise that teaches a reader to skip
        it."""
        rep = collect(gated_model(), [Sample(conversation(i)) for i in range(3)])
        assert rep.partial_measurability() == {}
        assert "measured on part of the batch" not in rep.headline()
        assert rep.as_dict()["partially_measurable"] == {}


# --------------------------------------------------------------------------- #
# 7. the OTHER rejected repair: uninstrumented is not model drift
# --------------------------------------------------------------------------- #

class TestUninstrumentedIsNotModelDrift:
    """Tightening `session_single_turn` to ``== 1`` was rejected because with
    neither turn bin firing an uninstrumented trace lands in ``other``, and
    ``other_drift`` reports that as *"the model is missing a dimension"*. The
    model is fine; the run is uninstrumented. This class proves the gate keeps
    the two findings apart, using a coverpoint whose bins really do all miss on
    an uninstrumented trace — which is exactly what ``== 1`` would have made
    `session_shape` into.
    """

    def turns_only(self, **overrides) -> Coverpoint:
        kw = dict(coverpoint_id="session_shape", kind="deterministic",
                  bins=[Bin(bin_id="multi_turn",
                            predicate_ref="session_multi_turn"),
                        Bin(bin_id="other")],
                  measurable=False, not_measurable_reason=REASON,
                  measurable_when=GATE)
        kw.update(overrides)
        return Coverpoint(**kw)

    def test_an_ungated_version_reports_the_drift_the_repair_was_rejected_for(
            self):
        """The control arm. Without the gate the very same coverpoint blames the
        model for 100% drift on a batch of ordinary tickets."""
        ungated = self.turns_only(measurable=True, not_measurable_reason="",
                                  measurable_when=None)
        rep = collect(gated_model(ungated), [Sample(ticket(i)) for i in range(4)])
        assert rep.other_drift()["session_shape"] == 1.0

    def test_the_gate_keeps_it_out_of_the_drift_number(self):
        rep = collect(gated_model(self.turns_only()),
                      [Sample(ticket(i)) for i in range(4)])
        assert "session_shape" not in rep.other_drift()
        assert rep.coverpoints["session_shape"].other_hits == 0

    def test_drift_on_a_mixed_batch_is_over_the_measurable_subset(self):
        """A gated coverpoint's drift denominator is the runs it could read, not
        the batch — otherwise the number is diluted by runs it never looked at,
        which is the same undisclosed-denominator error pointing the other way.
        Two of four conversations exhibit no modelled bin; the 37 tickets are not
        evidence either way.
        """
        batch = ([Sample(conversation(i, turns=1)) for i in range(2)]   # -> other
                 + [Sample(conversation(9 + i)) for i in range(2)]      # multi
                 + [Sample(ticket(i)) for i in range(37)])
        rep = collect(gated_model(self.turns_only()), batch)
        assert rep.coverpoints["session_shape"].other_hits == 2
        assert rep.other_drift()["session_shape"] == 0.5     # 2 of 4, not 2 of 41


# --------------------------------------------------------------------------- #
# 8. nothing ungated moved
# --------------------------------------------------------------------------- #

class TestUngatedCoverpointsAreUntouched:
    def test_the_two_counters_agree_everywhere_there_is_no_gate(self):
        """The invariant that makes the whole change safe: an ungated coverpoint
        increments both counters together, so every number it has ever reported
        is computed from the same evidence it always was."""
        rep = collect(gated_model(), [Sample(conversation(0)),
                                      Sample(ticket(0)), Sample(ticket(1))])
        for cp in rep.coverpoints.values():
            if cp.measurable_per_sample:
                continue
            for b in cp.bins.values():
                assert b.measurable_hits == b.trace_hits, \
                    f"{cp.coverpoint_id}.{b.bin_id}"
            assert (cp.n_measurable, cp.n_unmeasurable) == (0, 0)

    def test_a_hand_built_report_still_reads_its_raw_hits(self):
        """``CoverpointCoverage`` is constructed by hand outside ``collect``
        (``tests/verification/test_hole_targets.py``). Those objects have no
        per-sample counters at all, and must keep scoring off ``trace_hits``."""
        from agenttic.coverage.collect import BinCoverage, CoverpointCoverage

        cov = CoverpointCoverage("dim", "deterministic", True, False)
        cov.bins["a"] = BinCoverage(bin_id="a", trace_hits=3)
        cov.bins["b"] = BinCoverage(bin_id="b", trace_hits=0)
        cov.bins["other"] = BinCoverage(bin_id="other")
        assert cov.measurable_per_sample is False
        assert cov.trace_closure == 0.5
        assert cov.unhit == ["b"]

    def test_the_shipped_models_are_unaffected_until_they_declare_a_gate(self):
        for m in (baseline_model(closure_target=TARGET),
                  seed_model(closure_target=TARGET)):
            assert all(c.measurable_when is None for c in m.coverpoints)


# --------------------------------------------------------------------------- #
# 9. an illegal hit needs evidence too
# --------------------------------------------------------------------------- #

class TestIllegalHitsFollowTheSameRule:
    def illegal_model(self) -> CoverageModel:
        """`multi_turn` declared illegal — an agent that lets a session run on
        when it should have closed it. Contrived, and the point: a FAILURE
        credited from a predicate firing by default is not a failure anyone can
        act on."""
        cp = Coverpoint(
            coverpoint_id="session_shape", kind="deterministic",
            bins=[Bin(bin_id="single_turn", predicate_ref="session_single_turn"),
                  Bin(bin_id="multi_turn", predicate_ref="session_multi_turn",
                      illegal=True),
                  Bin(bin_id="other")],
            measurable=False, not_measurable_reason=REASON, measurable_when=GATE)
        return gated_model(cp)

    def test_an_illegal_bin_hit_on_an_unreadable_run_is_not_a_failure(self):
        rep = collect(self.illegal_model(), [Sample(ticket(i)) for i in range(3)])
        assert rep.illegal_hits == []
        assert rep.coverpoints["session_shape"].bins["multi_turn"].trace_hits == 0

    def test_an_illegal_bin_hit_on_a_readable_run_still_is_one(self):
        """The guard against the rule being written as "gated bins never fail"."""
        rep = collect(self.illegal_model(),
                      [Sample(conversation(0)), Sample(ticket(0))])
        assert [(i.coverpoint_id, i.bin_id, i.count) for i in rep.illegal_hits] \
            == [("session_shape", "multi_turn", 1)]
        assert rep.closed is False
