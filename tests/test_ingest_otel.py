"""OTel-GenAI ingest contracts (SPEC-7 Step 35, T35.3).

Pins the four acceptance criteria: a GenAI span fixture (tool call + result
events) → a well-formed Trace with tools + I/O hashes; incomplete spans degrade
gracefully to a partial trace with a logged note (no crash, no fabricated
field); the OTLP endpoint accepts a standard collector payload and returns the
OTLP success response; and ingested live traces are excluded from batch
certification scorecards (the SPEC-1 Step 9 invariant regression).

Plus the fifth: a failure the producer DECLARED survives ingest whether or not
the producer described it — and a status the producer did not call a failure
never becomes one.
"""
from __future__ import annotations

import json
import re
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agenttic.coverage.extractors import run_predicate
from agenttic.ingest import ingest_otlp_payload, ingest_spans, parse_otlp
from agenttic.ingest import mapping
from agenttic.ingest.mapping import (
    ERROR_NO_MESSAGE,
    NO_OUTPUT_CAPTURED,
    OTEL_STATUS_ERROR,
    A_OTEL_STATUS as OTEL_STATUS_KEY,
    error_status_marker,
    status_is_error,
)
from agenttic.ingest.otel import load_span_dump
from agenttic.registry.sqlite_store import Registry
from agenttic.server.app import create_app

_FIX = Path(__file__).resolve().parent / "fixtures/ingest/otel_genai_spans.json"


def _payload() -> dict:
    return json.loads(_FIX.read_text())


# --- 1) GenAI fixture → well-formed Trace with tools + I/O hashes ----------

def test_fixture_spans_ingest_into_wellformed_trace():
    with tempfile.TemporaryDirectory() as tmp:
        reg = Registry(db_path=f"{tmp}/t.db")
        rep = ingest_otlp_payload(reg, _payload())
        assert rep["trace_count"] == 1
        assert rep["incomplete_spans"] == []
        trace = reg.get_trace("5b8efff798038103d269b633813fc60c")
        assert trace.source == "otel_ingest"
        assert trace.agent_id == "support-agent"
        # agent_config_hash preserved from the producer, not fabricated
        assert trace.agent_config_hash == "cfg-9f8e7d6c"

        tool_spans = [s for s in trace.spans if s.kind == "tool_call"]
        assert len(tool_spans) == 1
        tool = tool_spans[0]
        assert tool.input.get("tool_name") == "get_weather"
        # I/O hashes populated on both sides
        assert len(tool.input["content_sha256"]) == 64
        assert len(tool.output["content_sha256"]) == 64

        llm = [s for s in trace.spans if s.kind == "llm_call"][0]
        assert llm.tokens_in == 127 and llm.tokens_out == 42
        assert "content_sha256" in llm.input


def test_hashes_are_deterministic_and_content_bound():
    p = _payload()
    with tempfile.TemporaryDirectory() as tmp1, tempfile.TemporaryDirectory() as tmp2:
        r1 = Registry(db_path=f"{tmp1}/t.db")
        r2 = Registry(db_path=f"{tmp2}/t.db")
        ingest_otlp_payload(r1, p)
        # mutate the tool result content in the second payload
        p2 = json.loads(json.dumps(p))
        ev = p2["resourceSpans"][0]["scopeSpans"][0]["spans"][1]["events"][1]
        ev["attributes"][0]["value"]["stringValue"] = "{\"temp_f\": 999}"
        ingest_otlp_payload(r2, p2)
        t1 = r1.get_trace("5b8efff798038103d269b633813fc60c")
        t2 = r2.get_trace("5b8efff798038103d269b633813fc60c")
        h1 = [s for s in t1.spans if s.kind == "tool_call"][0].output["content_sha256"]
        h2 = [s for s in t2.spans if s.kind == "tool_call"][0].output["content_sha256"]
        assert h1 != h2  # different content → different hash


# --- 1b) An uncaptured answer is declared, never impersonated --------------
#
# Ingest hashes content instead of storing it, so a producer that never sets
# ``gen_ai.completion`` leaves no answer text behind. The old fallbacks — the
# last span's output digest, or a span's name — put a 64-hex string (or
# "invoke_agent final_output") into ``trace.final_output``, which
# ``scoring.judge._evidence_body`` renders verbatim under "AGENT FINAL OUTPUT".
# The judge then grades a hash as if it were the agent's reply.

def _final_output_span_payload(*, completion: str | None) -> dict:
    attrs = [{"key": "gen_ai.operation.name", "value": {"stringValue": "invoke_agent"}}]
    if completion is not None:
        attrs.append({"key": "gen_ai.completion", "value": {"stringValue": completion}})
    return {"resourceSpans": [{
        "resource": {"attributes": [
            {"key": "agenttic.agent_id", "value": {"stringValue": "a1"}}]},
        "scopeSpans": [{"scope": {"name": "x"}, "spans": [
            {"traceId": "fo1", "spanId": "s1", "name": "invoke_agent final_output",
             "startTimeUnixNano": "1000000000", "endTimeUnixNano": "2000000000",
             "attributes": attrs}]}],
    }]}


def _ingest_one(payload: dict, trace_id: str):
    with tempfile.TemporaryDirectory() as tmp:
        reg = Registry(db_path=f"{tmp}/t.db")
        ingest_otlp_payload(reg, payload)
        return reg.get_trace(trace_id)


def test_uncaptured_final_output_is_declared_not_a_bare_digest():
    trace = _ingest_one(_payload(), "5b8efff798038103d269b633813fc60c")
    # the fixture's producer emitted no completion anywhere
    assert trace.final_output.startswith(NO_OUTPUT_CAPTURED)
    # never a bare hash a consumer would read as the agent's answer
    assert re.fullmatch(r"[0-9a-f]{64}", trace.final_output) is None
    # the digest survives for correlation, but LABELLED as a reference
    digest = [s for s in trace.spans
              if s.kind == "tool_call"][0].output["content_sha256"]
    assert f"sha256={digest}" in trace.final_output


def test_final_output_span_without_completion_does_not_serve_its_span_name():
    trace = _ingest_one(_final_output_span_payload(completion=None), "fo1")
    assert trace.final_output.startswith(NO_OUTPUT_CAPTURED)
    assert trace.final_output != "invoke_agent final_output"
    assert "last_span=invoke_agent final_output" in trace.final_output


def test_real_completion_is_still_lifted_verbatim():
    trace = _ingest_one(_final_output_span_payload(completion="refunds take 30 days"),
                        "fo1")
    assert trace.final_output == "refunds take 30 days"
    assert not trace.final_output.startswith(NO_OUTPUT_CAPTURED)


# --- 2) Graceful degradation on incomplete spans ---------------------------

def test_incomplete_span_degrades_gracefully():
    payload = {"resourceSpans": [{
        "resource": {"attributes": [
            {"key": "agenttic.agent_id", "value": {"stringValue": "a1"}}]},
        "scopeSpans": [{"scope": {"name": "x"}, "spans": [
            {"traceId": "tt", "spanId": "s-good", "name": "chat",
             "startTimeUnixNano": "1000000000", "endTimeUnixNano": "2000000000",
             "attributes": [
                 {"key": "gen_ai.system", "value": {"stringValue": "openai"}},
                 {"key": "gen_ai.request.model", "value": {"stringValue": "gpt"}}]},
            {"traceId": "tt", "spanId": "s-bare", "name": "mystery-step",
             "startTimeUnixNano": "1000000000", "endTimeUnixNano": "2000000000",
             "attributes": []},
        ]}],
    }]}
    with tempfile.TemporaryDirectory() as tmp:
        reg = Registry(db_path=f"{tmp}/t.db")
        rep = ingest_otlp_payload(reg, payload)          # must not raise
        assert rep["trace_count"] == 1
        assert "s-bare" in rep["incomplete_spans"]
        assert "incomplete_span:s-bare" in rep["notes"]
        trace = reg.get_trace("tt")
        bare = [s for s in trace.spans if s.span_id == "s-bare"][0]
        # partial span kept, flagged, NOT fabricated (no invented tokens)
        assert bare.attributes.get("agenttic.ingest.incomplete") is True
        assert bare.tokens_in is None and bare.tokens_out is None
        assert bare.input == {} and bare.output == {}


def test_malformed_spans_never_crash():
    # missing spanId, junk sub-objects, top-level list — all tolerated
    assert parse_otlp({"resourceSpans": [None, {"scopeSpans": [None]}]}) == []
    assert parse_otlp([]) == []
    assert parse_otlp({"resourceSpans": [{"scopeSpans": [
        {"spans": [{"name": "no-id"}]}]}]}) == []


# --- 3) OTLP endpoint accepts a collector payload, returns OTLP success -----

def _app(tmp_path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        "models: {agent_default: a, judge_strong: j, judge_light: l}\n"
        "harness: {timeout_seconds: 10, max_parallel: 5, transport_retries: 1, max_steps: 10}\n"
        "scoring: {calibration_threshold: 0.8}\n"
        "live: {sample_rate: 0.05, drift_threshold: 0.15, drift_window_runs: 50}\n"
        f"paths: {{registry_db: {tmp_path / 'a.db'}, review_dir: {tmp_path / 'r'}, "
        f"calibration_dir: {tmp_path / 'c'}}}\n"
        "auth: {required: true, token: t}\n"
        "security: {login_max_attempts: 5, login_lockout_seconds: 900}\n")
    reg = Registry(db_path=str(tmp_path / "a.db"))
    return create_app(str(cfg), registry=reg), reg


def test_otlp_endpoint_accepts_collector_payload(tmp_path):
    app, reg = _app(tmp_path)
    with TestClient(app) as c:
        r = c.post("/v1/traces", headers={"Authorization": "Bearer t"},
                   json=_payload())
        assert r.status_code == 200
        # OTLP ExportTraceServiceResponse: empty partialSuccess == full success
        assert r.json() == {"partialSuccess": {}}
        trace = reg.get_trace("5b8efff798038103d269b633813fc60c")
        assert trace.source == "otel_ingest"


def test_otlp_endpoint_refuses_protobuf_clearly(tmp_path):
    app, _ = _app(tmp_path)
    with TestClient(app) as c:
        r = c.post("/v1/traces",
                   headers={"Authorization": "Bearer t",
                            "Content-Type": "application/x-protobuf"},
                   content=b"\x00\x01")
        assert r.status_code == 415
        assert "json" in r.json()["error"].lower()


# --- 4) Invariant regression: ingested live traces excluded from scorecards -

def test_ingested_traces_excluded_from_batch_scorecards():
    """The SPEC-1 Step 9 invariant: a live-ingested trace is stored as mode=live
    and can never appear in the batch trace set that certification scores."""
    with tempfile.TemporaryDirectory() as tmp:
        reg = Registry(db_path=f"{tmp}/t.db")
        ingest_otlp_payload(reg, _payload())
        agent = "support-agent"
        # batch view (what scorecards read) must be empty; live view has it.
        assert reg.traces(agent, mode="batch") == []
        live = reg.traces(agent, mode="live")
        assert [t.trace_id for t in live] == ["5b8efff798038103d269b633813fc60c"]
        # and the ingested trace self-identifies its provenance
        assert all(t.source == "otel_ingest" for t in live)


def test_ingest_does_not_write_enforcement_log():
    """Ingest observes; it must not fabricate gateway history (Hard Rule 31).
    Even a decision-bearing span is returned, not written to the enforce log."""
    payload = {"resourceSpans": [{"resource": {"attributes": [
        {"key": "agenttic.agent_id", "value": {"stringValue": "a1"}}]},
        "scopeSpans": [{"scope": {"name": "x"}, "spans": [
            {"traceId": "d1", "spanId": "s1", "name": "enforce.tool_call",
             "startTimeUnixNano": "1000000000", "endTimeUnixNano": "2000000000",
             "attributes": [
                 {"key": "enforcement.action", "value": {"stringValue": "deny"}},
                 {"key": "enforcement.lane", "value": {"stringValue": "lane1"}},
                 {"key": "enforcement.action_class", "value": {"stringValue": "write"}},
                 {"key": "gen_ai.tool.name", "value": {"stringValue": "shell.exec"}}]}]}],
    }]}
    with tempfile.TemporaryDirectory() as tmp:
        reg = Registry(db_path=f"{tmp}/t.db")
        rep = ingest_spans(reg, parse_otlp(payload))
        assert rep["decision_count"] == 1
        assert rep["decisions"][0].action == "deny"
        # nothing landed in the append-only enforcement log
        assert reg.list_enforcement_events(None, "a1") == []


def test_batch_importer_from_file():
    spans = load_span_dump(_FIX)
    assert len(spans) == 2
    with tempfile.TemporaryDirectory() as tmp:
        reg = Registry(db_path=f"{tmp}/t.db")
        rep = ingest_spans(reg, spans)
        assert rep["trace_count"] == 1


# --- 5) A declared failure survives ingest, with or without prose ----------
#
# `Status` is a FIELD on an OTLP span, not an attribute, so nothing about it
# reaches a Trace unless map_span puts it there. It used to put there only
# `status["message"]`, and only for two of the six spellings of ERROR — so
# `span.set_status(StatusCode.ERROR)` with no description (ordinary
# instrumentation, legal OTLP) arrived as Span(error=None): a call the producer
# had explicitly declared FAILED, indistinguishable from one that succeeded.
#
# Measured on a `charge_card` tool span, before the fix, for every one of
# {'code': 2} / {'code': 2, 'message': ''} / {'code': 'STATUS_CODE_ERROR'} /
# {'code': 'ERROR'} / {'code': '2'} / {'code': 'error'}: error=None,
# tool_all_ok=True. Coverage credited "every tool call succeeded" to a payment
# the producer said had failed.
#
# The other direction is checked too. Over-reporting is the worse defect here,
# so a status the producer did NOT call an error must not become one.

def _tool_span_payload(status: dict, *, tool: str = "charge_card",
                       attributes: list | None = None) -> dict:
    """One instrumented tool span carrying `status`, and nothing else to go on."""
    attrs = [{"key": "gen_ai.operation.name", "value": {"stringValue": "execute_tool"}},
             {"key": "gen_ai.tool.name", "value": {"stringValue": tool}}]
    return {"resourceSpans": [{
        "resource": {"attributes": [
            {"key": "agenttic.agent_id", "value": {"stringValue": "a1"}}]},
        "scopeSpans": [{"scope": {"name": "x"}, "spans": [
            {"traceId": "st1", "spanId": "s1", "name": f"execute_tool {tool}",
             "startTimeUnixNano": "1000000000", "endTimeUnixNano": "2000000000",
             "attributes": attrs + (attributes or []), "status": status}]}],
    }]}


def _ingest_tool_span(status: dict, **kw):
    return _ingest_one(_tool_span_payload(status, **kw), "st1").spans[0]


#: Every spelling of ``StatusCode.ERROR`` seen on the wire. The integer is the
#: proto3 enum value, the long form is the JSON enum name, and the rest come from
#: encoders that stringify or shorten it.
_ERROR_STATUSES = [
    {"code": 2},
    {"code": 2, "message": ""},
    {"code": 2, "message": "   "},
    {"code": "STATUS_CODE_ERROR"},
    {"code": "ERROR"},
    {"code": "error"},
    {"code": "2"},
]


@pytest.mark.parametrize("status", _ERROR_STATUSES)
def test_wordless_declared_failure_arrives_carrying_the_declaration(status):
    """Both channels, because different consumers read different ones: the enum
    a consumer compares, and the field a consumer displays."""
    span = _ingest_tool_span(status)
    assert span.attributes[OTEL_STATUS_KEY] == OTEL_STATUS_ERROR
    assert str(span.error).startswith(ERROR_NO_MESSAGE)


@pytest.mark.parametrize("status", _ERROR_STATUSES)
def test_wordless_declared_failure_denies_tool_all_ok(status):
    """The consequence that matters: the run stops being scored as clean.

    Asserted through the coverage predicate rather than the span, because the
    defect was never in the span alone — it was `tool_all_ok` credited to a
    failed payment, which is over-reported coverage on a product whose claim is
    an honest account of what a run did."""
    trace = _ingest_one(_tool_span_payload(status), "st1")
    assert run_predicate("tool_all_ok", trace) is False


def test_a_real_status_message_is_still_lifted_verbatim():
    """The marker is for the wordless case only; a producer that described its
    failure keeps its own words, and they stay the tool's own error report."""
    span = _ingest_tool_span({"code": 2, "message": "card declined by issuer"})
    assert span.error == "card declined by issuer"
    assert not span.error.startswith(ERROR_NO_MESSAGE)
    assert span.attributes[OTEL_STATUS_KEY] == OTEL_STATUS_ERROR


def test_the_marker_interpolates_nothing_from_the_producer():
    """Constant by construction, whichever spelling arrived.

    `coverage.extractors` substring-matches `span.error` for status codes and
    condition phrases, so echoing a producer's bytes into this field is how a
    fabricated `tool_error_5xx` would be born. It is also why the marker must not
    read as captured text: ingest hashes message content, and a synthesized
    sentence would be indistinguishable from a sentence the producer wrote."""
    markers = {_ingest_tool_span(s).error for s in _ERROR_STATUSES}
    assert markers == {error_status_marker()}


def test_the_marker_credits_no_tool_condition():
    """A wordless failure says a call failed. It does not say HOW, and coverage
    must not pretend otherwise — these four bins name a specific fault the
    environment injected, and nothing here witnessed one."""
    trace = _ingest_one(_tool_span_payload({"code": 2}), "st1")
    for bin_id in ("tool_timeout", "tool_error_5xx", "tool_rate_limited",
                   "tool_malformed_response"):
        assert run_predicate(bin_id, trace) is False, bin_id


# --- 5b) the other direction: a non-error status is not an error -----------

@pytest.mark.parametrize("status", [
    {"code": 1, "message": "completed with a note"},   # OK + prose
    {"code": 0, "message": "nothing to report"},       # UNSET + prose
    {"code": "OK", "message": "fine"},
    {"code": "STATUS_CODE_OK"},
    {"message": "a message and no code at all"},       # UNSET by omission
    {"code": True},                                    # int(True) == 1, not a code
    {},
])
def test_a_status_the_producer_did_not_call_an_error_is_not_one(status):
    """OTLP says ``Status.message`` is only meaningful on ERROR, so a message
    beside OK/UNSET tells us nothing we may act on. Reading it as a failure would
    invent one — and an invented failure is as dishonest as a hidden one.
    Verified at the span AND at the predicate, since the span is only the
    mechanism; `tool_all_ok` is the claim a reader sees."""
    span = _ingest_tool_span(status)
    assert span.error is None
    assert OTEL_STATUS_KEY not in span.attributes
    assert run_predicate("tool_all_ok", _ingest_one(
        _tool_span_payload(status), "st1")) is True


def test_status_is_error_never_infers_from_junk():
    """The shared test, directly: it recognises encodings, it does not guess."""
    assert all(status_is_error(s) for s in _ERROR_STATUSES)
    assert status_is_error({"code": 2.0}) is True   # same enum member
    for junk in (None, {}, {"code": None}, {"code": ""}, {"code": "weird"},
                 {"code": 1}, {"code": 0}, {"code": 3}, {"code": [2]},
                 {"code": True}, {"code": 2.7}, {"message": "boom"}):
        assert status_is_error(junk) is False, junk


# --- 5c) the two halves of "did this fail" may not disagree ----------------

def test_a_producers_own_flattened_status_is_never_overwritten():
    """Ingest preserves what the producer sent. If a collector already flattened
    span status onto the attribute, that value stands — the verdict still reaches
    every consumer through `error`, so declining to overwrite costs nothing."""
    span = _ingest_tool_span(
        {"code": 2},
        attributes=[{"key": OTEL_STATUS_KEY, "value": {"stringValue": "Error"}}])
    assert span.attributes[OTEL_STATUS_KEY] == "Error"   # producer's bytes
    assert str(span.error).startswith(ERROR_NO_MESSAGE)  # verdict still carried


@pytest.mark.parametrize("status", _ERROR_STATUSES)
def test_no_span_arrives_kind_error_with_an_empty_error_channel(status):
    """`infer_kind` and the error mapping share one predicate now. They did not:
    `infer_kind` accepted "ERROR" while the mapping accepted only 2 and
    "STATUS_CODE_ERROR", so a span could arrive labelled `kind="error"` carrying
    `error=None` — a failure whose own record denied it."""
    bare = {"resourceSpans": [{"resource": {"attributes": [
        {"key": "agenttic.agent_id", "value": {"stringValue": "a1"}}]},
        "scopeSpans": [{"scope": {"name": "x"}, "spans": [
            {"traceId": "k1", "spanId": "s1", "name": "step",
             "startTimeUnixNano": "1000000000", "endTimeUnixNano": "2000000000",
             "attributes": [{"key": "gen_ai.system", "value": {"stringValue": "x"}}],
             "status": status}]}],
    }]}
    span = _ingest_one(bare, "k1").spans[0]
    assert span.kind == "error"
    assert span.error


def test_a_failed_tool_call_is_still_a_tool_call():
    """WHAT the span was outranks HOW it ended. `coverage.extractors._faults`
    reads tool conditions off `tool_call` spans only, so demoting a failed tool
    span to `kind="error"` would hide the fault it was supposed to reveal."""
    assert _ingest_tool_span({"code": 2}).kind == "tool_call"


# --- 5d) the markers are package-level exports -----------------------------

def test_both_non_result_markers_are_exported_from_the_package():
    """A consumer telling "no output was captured" from "the output was this
    text", or "declared failed, wordlessly" from "the tool reported this", must
    not have to reach into `ingest.mapping` for the vocabulary."""
    import agenttic.ingest as pkg

    for name in ("NO_OUTPUT_CAPTURED", "ERROR_NO_MESSAGE", "OTEL_STATUS_ERROR",
                 "status_is_error"):
        assert name in pkg.__all__, name
        assert getattr(pkg, name) is getattr(mapping, name)
