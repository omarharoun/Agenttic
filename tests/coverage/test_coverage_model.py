"""SPEC-13 M41 — coverage model acceptance tests.

Covers the Step 59 acceptance criteria and the handoff's required tests, plus
explicit guards for the three anti-patterns that apply to this milestone:
§7.4 coverage theater (one number), §7.5 classifier creep, §7.7 bin-widening.
"""

from __future__ import annotations

import socket
from datetime import datetime, timedelta, timezone

import pytest

from agenttic.coverage import CoverageModel, Coverpoint, Sample, collect
from agenttic.coverage.model import Bin, Classifier, Cross
from agenttic.coverage.models.conversational_transactional import seed_model
from agenttic.schema.trace import Span, Trace

T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)


def span(kind, name, *, i=0, input=None, output=None, attributes=None, error=None):
    return Span(span_id=f"s{i}", kind=kind, name=name,
                start_time=T0 + timedelta(seconds=i),
                end_time=T0 + timedelta(seconds=i + 1),
                input=input or {}, output=output or {},
                attributes=attributes or {}, error=error)


def trace(*spans, final_output="here is your answer", steps=0, cost=0.0):
    fixed = [s.model_copy(update={"span_id": f"s{i}"}) for i, s in enumerate(spans)]
    return Trace(trace_id="t", agent_id="a", agent_config_hash="c",
                 test_case_id="case", spans=fixed, visibility="glass_box",
                 final_output=final_output, total_steps=steps, total_cost_usd=cost)


@pytest.fixture
def no_network(monkeypatch):
    def _boom(*a, **k):
        raise AssertionError("network access during deterministic coverage extraction")
    monkeypatch.setattr(socket, "socket", _boom)
    monkeypatch.setattr(socket, "create_connection", _boom)
    yield


# --------------------------------------------------------------------------- #
# model validation (Step 59 acceptance 1)
# --------------------------------------------------------------------------- #

def test_seed_model_validates_and_pins_its_predicates():
    m = seed_model()
    m.validate_against_registry()
    assert m.closure_target == 0.95
    assert m.archetype_id == "conversational_transactional"


def test_coverpoint_without_an_other_bin_fails_validation():
    with pytest.raises(ValueError, match="exhaustive"):
        Coverpoint(coverpoint_id="cp", bins=[
            Bin(bin_id="a", predicate_ref="traj_direct_answer"),
            Bin(bin_id="b", predicate_ref="traj_refused")])


def test_waiver_without_a_reason_fails_validation():
    with pytest.raises(ValueError, match="named reason"):
        Bin(bin_id="x", predicate_ref="traj_refused", waived=True)
    ok = Bin(bin_id="x", predicate_ref="traj_refused", waived=True,
             reason="the environment cannot produce this condition")
    assert ok.waived and ok.reason


def test_a_bin_declares_exactly_one_of_predicate_or_classifier():
    with pytest.raises(ValueError, match="exactly one"):
        Bin(bin_id="x")                                    # neither
    with pytest.raises(ValueError, match="exactly one"):
        Bin(bin_id="x", predicate_ref="traj_refused",
            classifier=Classifier(prompt="p", anchors={"pass": "a", "fail": "b"}))


def test_illegal_and_waived_are_mutually_exclusive():
    with pytest.raises(ValueError, match="illegal and waived"):
        Bin(bin_id="x", predicate_ref="traj_refused", illegal=True,
            waived=True, reason="r")


def test_model_rejects_unregistered_predicates():
    m = CoverageModel(model_id="m", coverpoints=[Coverpoint(
        coverpoint_id="cp", bins=[Bin(bin_id="a", predicate_ref="nope"),
                                  Bin(bin_id="other")])])
    with pytest.raises(ValueError, match="unregistered predicate"):
        m.validate_against_registry()


# --- anti-pattern §7.5: classifier creep ----------------------------------- #

def test_deterministic_coverpoint_cannot_be_classifier_backed():
    clf = Classifier(prompt="is it a timeout?", anchors={"pass": "a", "fail": "b"})
    with pytest.raises(ValueError, match="deterministic"):
        Coverpoint(coverpoint_id="tool_condition", kind="deterministic",
                   bins=[Bin(bin_id="timeout", classifier=clf), Bin(bin_id="other")])
    # and a deterministic-by-construction coverpoint may not be relabelled
    with pytest.raises(ValueError, match="deterministic by construction"):
        Coverpoint(coverpoint_id="trajectory", kind="classifier",
                   bins=[Bin(bin_id="x", classifier=clf), Bin(bin_id="other")])


# --- Step 59 acceptance 3: classifier bins inherit calibration state -------- #

def test_classifier_bins_are_provisional_until_measured():
    m = seed_model()
    assert set(m.provisional_coverpoints) == {"intent", "emotional_register",
                                              "policy_vector"}
    assert m.coverpoint("trajectory").provisional is False     # deterministic
    with pytest.raises(ValueError, match="pass/fail anchors"):
        Classifier(prompt="p", anchors={})                     # must be anchored
    with pytest.raises(ValueError, match="measured alpha"):
        Classifier(prompt="p", anchors={"pass": "a", "fail": "b"}, calibrated=True)


# --- anti-pattern §7.7: closure by bin-widening ----------------------------- #

def test_changing_a_bin_changes_the_fingerprint():
    a = seed_model()
    trimmed = CoverageModel(
        model_id=a.model_id, version=a.version, archetype_id=a.archetype_id,
        coverpoints=[c for c in a.coverpoints if c.coverpoint_id != "trajectory"]
        + [Coverpoint(coverpoint_id="trajectory", kind="deterministic", bins=[
            Bin(bin_id="direct_answer", predicate_ref="traj_direct_answer"),
            Bin(bin_id="other")])],          # bins deleted to make closure easy
        crosses=[], closure_target=a.closure_target)
    assert trimmed.bins_fingerprint() != a.bins_fingerprint()


# --------------------------------------------------------------------------- #
# deterministic extraction (Step 59 acceptance 2) — zero model calls
# --------------------------------------------------------------------------- #

DET_ONLY = CoverageModel(
    model_id="det", version=1,
    coverpoints=[c for c in seed_model().coverpoints if c.kind == "deterministic"],
    crosses=[Cross(cross_id="tool_x_traj",
                   coverpoints=["tool_condition", "trajectory"], target="all")],
)


def test_trajectory_coverage_extracts_from_traces_with_zero_model_calls(no_network):
    samples = [
        Sample(trace(span("llm_call", "answer"))),                       # direct
        Sample(trace(span("tool_call", "get_order"), span("llm_call", "a"))),
        Sample(trace(span("tool_call", "get_order", error="timeout after 30s"),
                     span("tool_call", "get_order"),
                     span("llm_call", "a"))),                            # retry+recover
        Sample(trace(span("agent_decision", "escalate_to_human"))),
    ]
    rep = collect(DET_ONLY, samples)
    traj = rep.coverpoints["trajectory"]
    assert traj.bins["direct_answer"].hit
    assert traj.bins["tool_then_answer"].hit
    assert traj.bins["retry_after_error"].hit
    assert traj.bins["recovered_from_tool_failure"].hit
    assert traj.bins["escalated_to_human"].hit
    # the ones never exercised are named, not silently absent (Hard Rule 61)
    assert "budget_exceeded" in traj.unhit
    assert "max_steps_hit" in traj.unhit
    assert rep.coverpoints["tool_condition"].bins["timeout"].hit


def test_report_names_what_was_never_exercised():
    rep = collect(DET_ONLY, [Sample(trace(span("llm_call", "answer")))])
    holes = rep.holes()
    assert holes, "an unexercised model must report holes"
    where = {h.where for h in holes}
    assert "trajectory" in where
    assert rep.trace_closure < 1.0
    assert "closure" in rep.headline()


# --------------------------------------------------------------------------- #
# illegal bins are failures, never coverage (Step 59 acceptance 4)
# --------------------------------------------------------------------------- #

ILLEGAL_MODEL = CoverageModel(
    model_id="illegal-m", version=1,
    coverpoints=[Coverpoint(coverpoint_id="trajectory", kind="deterministic", bins=[
        Bin(bin_id="direct_answer", predicate_ref="traj_direct_answer"),
        Bin(bin_id="budget_exceeded", predicate_ref="traj_budget_exceeded",
            illegal=True),          # must never happen
        Bin(bin_id="other")])],
)


def test_illegal_bin_hit_is_a_failure_and_never_counts_toward_closure():
    over = trace(span("llm_call", "a", attributes={"max_cost_usd": 0.01}),
                 final_output="done", cost=5.0)
    rep = collect(ILLEGAL_MODEL, [Sample(over)])
    assert rep.illegal_hits, "hitting an illegal bin must be reported as a failure"
    assert rep.illegal_hits[0].bin_id == "budget_exceeded"
    # excluded from the closure numerator AND denominator
    cp = rep.coverpoints["trajectory"]
    assert "budget_exceeded" not in [b.bin_id for b in cp.countable()]
    assert not rep.closed                       # an illegal hit blocks closure


# --------------------------------------------------------------------------- #
# anti-pattern §7.4: stimulus vs trace — two numbers, never one
# --------------------------------------------------------------------------- #

def test_stimulus_and_trace_coverage_are_reported_separately():
    rep = collect(DET_ONLY, [Sample(trace(span("llm_call", "a")),
                                    requested={"trajectory": "direct_answer"})])
    d = rep.as_dict()
    assert "trace_closure" in d and "stimulus_closure" in d
    assert "stimulus_vs_trace_divergence" in d


def test_requested_but_never_exhibited_is_detected_as_divergence():
    """The generator asked for a timeout; the timeout never actually fired. That
    is a stimulus hit and a trace MISS — counting it as covered is the theater
    this check exists to prevent."""
    clean = trace(span("tool_call", "get_order"), span("llm_call", "a"))
    rep = collect(DET_ONLY, [Sample(clean, requested={"tool_condition": "timeout"})])
    tc = rep.coverpoints["tool_condition"]
    assert tc.bins["timeout"].stimulus_hits == 1     # requested
    assert tc.bins["timeout"].trace_hits == 0        # never exhibited
    div = rep.divergence()
    assert any(d["coverpoint_id"] == "tool_condition" and d["bin_id"] == "timeout"
               for d in div)
    # the two views disagree about WHICH bin was covered: the run exhibited a
    # different condition entirely, and closure follows the trace side — so the
    # requested bin is still reported as a hole.
    assert tc.bins["all_ok"].trace_hits == 1 and tc.bins["all_ok"].stimulus_hits == 0
    assert "timeout" in tc.unhit


# --------------------------------------------------------------------------- #
# `other` drift is a finding
# --------------------------------------------------------------------------- #

def test_other_bin_drift_is_reported():
    # a run exhibiting no modelled tool condition lands in `other`
    rep = collect(DET_ONLY, [Sample(trace(span("llm_call", "a")))])
    drift = rep.other_drift()
    assert drift.get("tool_condition") == 1.0       # 100% of samples unmodelled


# --------------------------------------------------------------------------- #
# classifier bins are never counted as missed when unevaluated
# --------------------------------------------------------------------------- #

def test_classifier_bins_are_unevaluated_without_an_evaluator_not_missed():
    rep = collect(seed_model(), [Sample(trace(span("llm_call", "a")))])
    intent = rep.coverpoints["intent"]
    assert all(b.unevaluated for b in intent.bins.values() if b.bin_id != "other")
    assert intent.countable() == []          # excluded, not counted as holes
    assert "intent" in rep.provisional_coverpoints
    assert "PROVISIONAL" in rep.headline()


def test_injected_classifier_is_used_when_supplied():
    def classify(clf, tr, scen):
        return "refund" in (tr.final_output or "").lower() and "money back" in clf.prompt
    rep = collect(seed_model(),
                  [Sample(trace(span("llm_call", "a"), final_output="your refund"))],
                  classify=classify)
    assert rep.coverpoints["intent"].bins["refund"].hit


# --------------------------------------------------------------------------- #
# the model is a versioned registry artifact, not a code constant (DoD §8)
# --------------------------------------------------------------------------- #

def test_coverage_model_round_trips_through_the_registry(tmp_path):
    from agenttic.registry.sqlite_store import DuplicateVersionError, Registry
    reg = Registry(str(tmp_path / "r.db"))
    m = seed_model()
    reg.save_coverage_model(m)
    got = reg.get_coverage_model(m.model_id)
    assert got.bins_fingerprint() == m.bins_fingerprint()
    assert reg.list_coverage_models()[0]["bins_fingerprint"] == m.bins_fingerprint()
    with pytest.raises(DuplicateVersionError):
        reg.save_coverage_model(m)           # append-only: no silent bin widening
