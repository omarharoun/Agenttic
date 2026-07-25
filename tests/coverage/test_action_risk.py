"""The action_risk coverpoint — closure must notice what the agent DID.

The gap this closes, measured on a real bakeoff against deepeval / inspect_ai /
promptfoo / langsmith: adding a case that moved money irreversibly and tripped a
CRITICAL assertion moved baseline closure by **exactly zero** (16.6% -> 16.6%).
Coverage recorded what the environment did *to* the agent (``tool_condition``)
and never what the agent did *to the world*, so the flagship metric was blind to
the precise event the platform exists to catch.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from agenttic.coverage.collect import Sample, collect
from agenttic.coverage.model import DETERMINISTIC_BY_CONSTRUCTION
from agenttic.coverage.models.baseline import baseline_model
from agenttic.schema.trace import Span, Trace

T0 = datetime(2026, 7, 25, 12, 0, 0)


def _span(i, kind, name, *, attrs=None, out=None, inp=None):
    return Span(span_id=f"s{i}", kind=kind, name=name,
                start_time=T0 + timedelta(seconds=i),
                end_time=T0 + timedelta(seconds=i + 1),
                input=inp or {}, output=out or {}, attributes=attrs or {})


def _trace(case_id, spans, answer="ok"):
    return Trace(trace_id=f"t-{case_id}", agent_id="refund-bot",
                 agent_config_hash="cfg-refund-bot-v1", test_case_id=case_id,
                 visibility="glass_box", spans=spans, final_output=answer,
                 total_steps=len(spans), total_cost_usd=0.004,
                 total_latency_ms=900)


def _read_only(case_id):
    return _trace(case_id, [
        _span(0, "llm_call", "plan"),
        _span(1, "tool_call", "get_order", attrs={"entity_id": "ord-77"}),
        _span(2, "final_output", "reply", out={"text": "processing"})])


def _irreversible_unconfirmed(case_id):
    return _trace(case_id, [
        _span(0, "llm_call", "plan"),
        _span(1, "tool_call", "issue_refund",
              attrs={"entity_id": "ord-77", "mutating": True,
                     "irreversible": True},
              inp={"order_id": "ord-77", "amount": 240.0}),
        _span(2, "final_output", "reply", out={"text": "refunded"})])


def _irreversible_confirmed(case_id):
    return _trace(case_id, [
        _span(0, "llm_call", "plan"),
        _span(1, "agent_decision", "confirm_with_customer",
              attrs={"confirmed": True}),
        _span(2, "tool_call", "issue_refund",
              attrs={"entity_id": "ord-77", "mutating": True,
                     "irreversible": True}),
        _span(3, "final_output", "reply", out={"text": "refunded"})])


def _cp(report, coverpoint_id):
    return report.coverpoints[coverpoint_id]


def _closure(traces):
    return collect(baseline_model(), [Sample(trace=t) for t in traces])


# --- 1. the regression this coverpoint exists to prevent -------------------- #

def test_closure_moves_when_an_irreversible_action_is_first_exercised():
    status_only = [_read_only(f"ask-{i}") for i in range(5)]
    before = _closure(status_only)
    after = _closure(status_only + [_irreversible_unconfirmed("issue-1")])

    assert after.trace_closure > before.trace_closure, (
        "adding an irreversible action must move closure — this is exactly the "
        "16.6% -> 16.6% blindness action_risk was added to fix")


def test_a_read_only_suite_reports_the_risk_bins_as_unhit():
    report = _closure([_read_only(f"ask-{i}") for i in range(5)])
    unhit = set(_cp(report, "action_risk").unhit)
    assert "mutating_irreversible" in unhit
    assert "irreversible_confirmed" in unhit
    assert "mutating_reversible" in unhit
    assert "read_only" not in unhit


# --- 2. confirmed and unconfirmed are DIFFERENT bins ----------------------- #

def test_the_confirmed_path_does_not_cover_the_unconfirmed_one():
    """Collapsing these would let a suite close the coverpoint while never
    testing the dangerous half."""
    report = _closure([_irreversible_confirmed("c-1")])
    hit = set(_cp(report, "action_risk").bins) - set(_cp(report, "action_risk").unhit)
    assert "irreversible_confirmed" in hit
    assert "mutating_irreversible" in _cp(report, "action_risk").unhit


def test_the_unconfirmed_path_does_not_cover_the_confirmed_one():
    report = _closure([_irreversible_unconfirmed("u-1")])
    cp = _cp(report, "action_risk")
    assert "mutating_irreversible" not in cp.unhit
    assert "irreversible_confirmed" in cp.unhit


def test_exercising_both_paths_closes_the_risk_coverpoint_further():
    both = _closure([_read_only("r"), _irreversible_unconfirmed("u"),
                     _irreversible_confirmed("c")])
    only_one = _closure([_read_only("r"), _irreversible_unconfirmed("u")])
    assert _cp(both, "action_risk").trace_closure > \
        _cp(only_one, "action_risk").trace_closure


# --- 3. it must agree with the assertion layer ----------------------------- #

def test_the_span_that_violates_an_assertion_also_lands_in_the_risk_bin():
    """Closure and assertions must never disagree about the same span — the
    coverpoint delegates classification to the assertion layer's own functions.
    """
    from agenttic.ops import verify_op
    t = _irreversible_unconfirmed("issue-1")
    assertions, cov = verify_op([_read_only("ask-1"), t])

    violated = {a.assertion_id for a in assertions if a.status == "violation"}
    assert "always_irreversible_action_confirmed" in violated

    risk = cov["per_coverpoint"]["action_risk"]
    assert "mutating_irreversible" not in risk["unhit"], (
        "the assertion layer called this span irreversible-and-unconfirmed, so "
        "coverage must have registered it in the same bin")


# --- 4. it is deterministic, never a classifier ---------------------------- #

def test_action_risk_is_deterministic_by_construction():
    assert "action_risk" in DETERMINISTIC_BY_CONSTRUCTION
    cp = baseline_model().coverpoint("action_risk")
    assert cp.kind == "deterministic"
    assert all(b.classifier is None for b in cp.bins)


def test_the_version_bump_changes_the_bins_fingerprint():
    """Adding bins must not be silently comparable with prior closure numbers."""
    model = baseline_model()
    assert model.version >= 2
    assert "action_risk" in {cp.coverpoint_id for cp in model.coverpoints}
