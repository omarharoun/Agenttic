"""Joining a driven run to the spans the agent exported about itself.

This is the piece that makes glass-box evidence possible with NO adapter code:
the harness mints a ``gen_ai.conversation.id`` per case, the agent stamps it on
the OTel spans it exports, and the two halves — a run we drove and telemetry we
ingested — become the same event seen twice.

The property that matters most is the negative one. A join that finds nothing
must change nothing. The tempting bug is an upgrade-by-default: a black-box
trace relabelled glass-box because correlation was switched on, with no spans
behind it — a trajectory claim resting on evidence that never arrived.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from agenttic.ingest.correlate import (CONVERSATION_ID, OBSERVED_VIA,
                                       conversation_id_of, correlate,
                                       correlate_all, new_conversation_id,
                                       observed_spans_for)
from agenttic.schema.trace import Span, Trace

T0 = datetime(2026, 8, 2, 12, 0, tzinfo=timezone.utc)


def _span(name: str, kind: str = "tool_call", *, cid: str = "",
          offset: int = 0, span_id: str = "") -> Span:
    attrs = {CONVERSATION_ID: cid} if cid else {}
    return Span(span_id=span_id or uuid.uuid4().hex[:12], kind=kind, name=name,
                start_time=T0 + timedelta(seconds=offset),
                end_time=T0 + timedelta(seconds=offset), attributes=attrs)


def _driven(cid: str = "", *, visibility: str = "black_box") -> Trace:
    return Trace(trace_id=uuid.uuid4().hex, agent_id="a", agent_config_hash="h",
                 test_case_id="c1", visibility=visibility, final_output="done",
                 spans=[_span("answer", "final_output", cid=cid, offset=9)])


def _exported(cid: str, *, kinds=("tool_call",)) -> Trace:
    return Trace(trace_id=uuid.uuid4().hex, agent_id="a", agent_config_hash="h",
                 visibility="glass_box", final_output="", source="otel_ingest",
                 spans=[_span(f"exported_{k}", k, cid=cid, offset=i)
                        for i, k in enumerate(kinds)])


class TestTheJoin:
    def test_exported_spans_attach_to_the_run_that_caused_them(self):
        res = correlate(_driven("conv-1"), [_exported("conv-1")])
        assert res.observed
        assert res.attached_spans == 1
        assert any(s.name == "exported_tool_call" for s in res.trace.spans)

    def test_attached_spans_are_marked_as_the_agents_own_account(self):
        """Provenance is the product: what the harness saw and what the agent
        said about itself must stay distinguishable forever."""
        res = correlate(_driven("conv-1"), [_exported("conv-1")])
        attached = [s for s in res.trace.spans
                    if (s.attributes or {}).get(OBSERVED_VIA) == "otel"]
        assert len(attached) == 1

    def test_spans_are_kept_in_time_order(self):
        res = correlate(_driven("conv-1"),
                        [_exported("conv-1", kinds=("tool_call", "llm_call"))])
        times = [s.start_time for s in res.trace.spans]
        assert times == sorted(times)

    def test_only_this_conversations_spans_are_taken(self):
        """An exporter may batch several conversations into one OTel trace.
        Taking the whole trace would attach another case's work to this one."""
        mixed = Trace(
            trace_id=uuid.uuid4().hex, agent_id="a", agent_config_hash="h",
            visibility="glass_box", final_output="", source="otel_ingest",
            spans=[_span("mine", cid="conv-1"), _span("someone_elses", cid="conv-2")])
        res = correlate(_driven("conv-1"), [mixed])
        assert res.attached_spans == 1
        assert not any(s.name == "someone_elses" for s in res.trace.spans)

    def test_a_span_already_present_is_not_duplicated(self):
        shared = _span("same", cid="conv-1", span_id="dup1")
        driven = _driven("conv-1").model_copy(update={"spans": [shared]})
        exported = Trace(trace_id="x", agent_id="a", agent_config_hash="h",
                         visibility="glass_box", final_output="",
                         spans=[shared])
        res = correlate(driven, [exported])
        assert res.attached_spans == 0
        assert len(res.trace.spans) == 1


class TestTheNegativeCases:
    def test_finding_nothing_changes_nothing_and_says_so(self):
        res = correlate(_driven("conv-1"), [_exported("conv-OTHER")])
        assert not res.observed
        assert res.trace.visibility == "black_box", \
            "visibility was upgraded with no evidence behind it"
        assert any("no exported spans carried" in n for n in res.notes)

    def test_a_run_with_no_correlation_id_is_reported_not_guessed(self):
        res = correlate(_driven(""), [_exported("conv-1")])
        assert not res.observed
        assert any("carried no gen_ai.conversation.id" in n for n in res.notes)

    def test_no_candidates_at_all_is_safe(self):
        res = correlate(_driven("conv-1"), [])
        assert not res.observed and res.trace.visibility == "black_box"


class TestVisibilityUpgrade:
    def test_a_black_box_run_becomes_glass_box_only_with_real_tool_spans(self):
        """The point of the whole mechanism: an HTTP agent we can only send text
        to, plus its own exported spans, scores as glass box — and
        `scoring/engine.py`'s trajectory checks (four of which have no text
        fallback) get real evidence instead of a black box.
        """
        res = correlate(_driven("conv-1"), [_exported("conv-1")])
        assert res.upgraded_visibility
        assert res.trace.visibility == "glass_box"

    def test_message_only_telemetry_does_not_upgrade_visibility(self):
        """Spans with no tool calls add context but prove no trajectory."""
        res = correlate(_driven("conv-1"), [_exported("conv-1", kinds=("llm_call",))])
        assert res.observed
        assert not res.upgraded_visibility
        assert res.trace.visibility == "black_box"

    def test_an_already_glass_box_run_is_not_relabelled(self):
        res = correlate(_driven("conv-1", visibility="glass_box"),
                        [_exported("conv-1")])
        assert res.trace.visibility == "glass_box"
        assert not res.upgraded_visibility

    def test_total_steps_is_recounted_after_attaching(self):
        res = correlate(_driven("conv-1"), [_exported("conv-1")])
        assert res.trace.total_steps == 1


class TestTheRunLevelSummary:
    def test_partial_correlation_is_reported_as_partial(self):
        driven = [_driven("conv-1"), _driven("conv-2")]
        traces, summary = correlate_all(driven, [_exported("conv-1")])
        assert summary["correlated"] == 1
        assert summary["of_traces"] == 2
        assert summary["upgraded_to_glass_box"] == 1
        assert "1 of 2" in summary["note"]
        assert traces[1].visibility == "black_box"

    def test_full_correlation_says_so(self):
        driven = [_driven("conv-1")]
        _, summary = correlate_all(driven, [_exported("conv-1")])
        assert "every driven trace was matched" in summary["note"]

    def test_no_driven_traces_is_not_a_success(self):
        """Zero of zero must not read as "all correlated" — the absence of
        evidence is not evidence."""
        _, summary = correlate_all([], [])
        assert summary["correlated"] == 0
        assert "every driven trace was matched" not in summary["note"]
        assert "absence of evidence" in summary["note"]


class TestTheKeyItself:
    def test_ids_are_unique_per_case_run(self):
        """Derived-from-the-case-id would collide across trials, and trial 2
        would attach trial 1's telemetry — pass^k measuring one execution twice.
        """
        assert new_conversation_id("c1") != new_conversation_id("c1")

    def test_the_id_is_readable_off_a_trace_or_a_span(self):
        tr = _driven("conv-9")
        assert conversation_id_of(tr) == "conv-9"
        assert conversation_id_of(tr.spans[0]) == "conv-9"
        assert conversation_id_of(_driven("")) == ""

    def test_the_semconv_name_is_the_one_otel_defines(self):
        """Not an invented key: an agent instrumented to the GenAI conventions
        already emits this, so correlation needs no bespoke agreement."""
        assert CONVERSATION_ID == "gen_ai.conversation.id"

    def test_ingest_preserves_the_key_end_to_end(self):
        """`ingest/mapping.py` keeps every producer attribute, so the key
        survives the round trip from an exporter into a stored Trace."""
        from agenttic.ingest.mapping import map_span, OtelSpan

        sp = OtelSpan(trace_id="t1", span_id="s1", name="tool", kind="",
                      parent_id=None, start_ns=0, end_ns=1000,
                      attributes={"gen_ai.tool.name": "search",
                                  CONVERSATION_ID: "conv-7"},
                      events=[], status={}, resource_attributes={}, scope={})
        span, _ = map_span(sp)
        assert span.attributes[CONVERSATION_ID] == "conv-7"


def test_observed_spans_for_requires_an_id():
    assert observed_spans_for("", [_exported("conv-1")]) == []
