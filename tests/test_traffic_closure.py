"""Closure over production traffic, and the honesty guard that makes it usable.

The OTel ingest already imported production traces (`source="otel_ingest"`,
`mode="live"`) and never verified them. Suite closure stalls near 20% because
nobody authors 95% of a situation space; real traffic exercises it continuously.

The trap: ingested spans come from someone else's instrumentation, so most carry
no mutation semantics. `is_write` falls back to tool-NAME hints, so an
uninstrumented `process_request` would be silently credited to
`action_risk.read_only` — a coverage credit for a question never answered. Every
tool span therefore carries a confidence, and unknown is never a read-only credit.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from agenttic.verification.traffic import (
    classify_confidence, instrumentation_fidelity, traffic_window,
    verify_traffic)
from agenttic.schema.trace import Span, Trace

T0 = datetime(2026, 7, 26, 12, 0, 0)


def _sp(i, kind, name, *, attrs=None, out=None):
    return Span(span_id=f"s{i}", kind=kind, name=name,
                start_time=T0 + timedelta(seconds=i),
                end_time=T0 + timedelta(seconds=i + 1),
                input={}, output=out or {}, attributes=attrs or {})


def _tr(i, spans, *, source="otel_ingest"):
    return Trace(trace_id=f"t{i}", agent_id="prod-bot", agent_config_hash="cfg-1",
                 test_case_id=None, visibility="glass_box", spans=spans,
                 final_output="ok", total_steps=len(spans), source=source)


def _instrumented(i):
    return _tr(i, [
        _sp(0, "llm_call", "plan"),
        _sp(1, "tool_call", "issue_refund",
            attrs={"mutating": True, "irreversible": True, "entity_id": "o1"}),
        _sp(2, "final_output", "reply", out={"text": "done"})])


def _name_hint_only(i):
    return _tr(i, [
        _sp(0, "llm_call", "plan"),
        _sp(1, "tool_call", "delete_record"),          # hint matches
        _sp(2, "final_output", "reply", out={"text": "done"})])


def _opaque(i):
    return _tr(i, [
        _sp(0, "llm_call", "plan"),
        _sp(1, "tool_call", "process_request"),        # tells us nothing
        _sp(2, "final_output", "reply", out={"text": "done"})])


# --- 1. the confidence split ------------------------------------------------ #

def test_an_explicitly_instrumented_tool_is_explicit():
    span = _sp(1, "tool_call", "anything", attrs={"mutating": True})
    assert classify_confidence(span) == "explicit"


def test_a_recognisable_tool_name_is_only_inferred():
    assert classify_confidence(_sp(1, "tool_call", "delete_record")) == "inferred"


def test_an_opaque_tool_name_is_unknown_not_read_only():
    """The whole point: silence is not a read-only guarantee."""
    assert classify_confidence(_sp(1, "tool_call", "process_request")) == "unknown"


def test_a_mutating_false_attribute_still_counts_as_explicit():
    """An explicit 'no, this does not mutate' IS evidence, unlike silence."""
    span = _sp(1, "tool_call", "process_request", attrs={"mutating": False})
    assert classify_confidence(span) == "explicit"


# --- 2. fidelity reporting ------------------------------------------------- #

def test_fidelity_counts_by_confidence_and_names_the_offenders():
    f = instrumentation_fidelity([_instrumented(1), _name_hint_only(2), _opaque(3)])
    assert f["tool_spans"] == 3
    assert f["by_confidence"] == {"explicit": 1, "inferred": 1, "unknown": 1}
    assert f["action_risk_trustable"] == round(2 / 3, 4)
    assert ("process_request", 1) in f["uninstrumented_tools"]


def test_fully_instrumented_traffic_is_fully_trustable():
    f = instrumentation_fidelity([_instrumented(i) for i in range(4)])
    assert f["action_risk_trustable"] == 1.0
    assert f["by_confidence"]["unknown"] == 0
    assert f["uninstrumented_tools"] == []


def test_ingest_incomplete_spans_are_surfaced():
    t = _tr(9, [
        _sp(0, "llm_call", "plan", attrs={"agenttic.ingest.incomplete": True}),
        _sp(1, "tool_call", "issue_refund", attrs={"mutating": True}),
        _sp(2, "final_output", "reply", out={"text": "x"})])
    assert instrumentation_fidelity([t])["incomplete_spans"] == 1


def test_ingested_traces_are_counted_separately_from_native_ones():
    f = instrumentation_fidelity(
        [_instrumented(1), _tr(2, _instrumented(2).spans, source="native")])
    assert f["n_traces"] == 2 and f["n_ingested"] == 1


# --- 3. verification over the population ----------------------------------- #

def test_traffic_verification_states_the_population_it_covers():
    """A closure figure with no stated population is an unscoped claim."""
    v = verify_traffic([_instrumented(i) for i in range(3)])
    assert v["status"] == "populated"
    assert v["population"] == "production_traffic"
    assert "production trace(s)" in v["scope_statement"]
    assert "not over an authored suite" in v["scope_statement"]


def test_traffic_verification_finds_real_violations():
    """The reason this is worth doing: production does what suites do not."""
    v = verify_traffic([_instrumented(i) for i in range(3)])
    violated = {p["assertion_id"] for p in v["assertions"]["violated_properties"]}
    assert "always_irreversible_action_confirmed" in violated
    sev = {p["assertion_id"]: p["severity"]
           for p in v["assertions"]["violated_properties"]}
    assert sev["always_irreversible_action_confirmed"] == "critical"


def test_untrustworthy_instrumentation_raises_a_warning():
    v = verify_traffic([_instrumented(1), _opaque(2), _opaque(3)])
    assert any("no usable risk class" in w for w in v["warnings"])


def test_clean_instrumentation_raises_no_instrumentation_warning():
    v = verify_traffic([_instrumented(i) for i in range(3)])
    assert not any("risk class" in w for w in v.get("warnings") or [])


def test_no_traffic_is_not_run_never_a_pass():
    v = verify_traffic([])
    assert v["status"] == "not_run"
    assert "trace_closure" not in v


def test_more_traffic_can_close_more_of_the_space():
    """The thesis: real traffic exercises situations a suite never authored."""
    narrow = verify_traffic([_instrumented(1)])
    wider = verify_traffic([
        _instrumented(1),
        _tr(2, [_sp(0, "llm_call", "plan"),
                _sp(1, "agent_decision", "confirm", attrs={"confirmed": True}),
                _sp(2, "tool_call", "issue_refund",
                    attrs={"mutating": True, "irreversible": True}),
                _sp(3, "final_output", "reply", out={"text": "done"})])])
    assert wider["trace_closure"] > narrow["trace_closure"]


# --- 4. the population comes from live traces, never batch ----------------- #

def test_traffic_window_reads_live_traces_only(tmp_path):
    """Ingested traces are stored live so they can never enter batch scorecards;
    verifying them is a different, legitimate use."""
    from agenttic.registry.sqlite_store import Registry
    reg = Registry(tmp_path / "t.db")
    reg.save_trace(_instrumented(1), mode="live")
    reg.save_trace(_tr(77, _instrumented(77).spans, source="native"), mode="batch")

    live = traffic_window(reg, agent_id="prod-bot")
    assert [t.trace_id for t in live] == ["t1"]


def test_traffic_window_limit_takes_the_most_recent(tmp_path):
    from agenttic.registry.sqlite_store import Registry
    reg = Registry(tmp_path / "t.db")
    for i in range(5):
        reg.save_trace(_instrumented(i), mode="live")
    assert len(traffic_window(reg, agent_id="prod-bot", limit=2)) == 2
