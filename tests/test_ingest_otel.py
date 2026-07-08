"""OTel-GenAI ingest contracts (SPEC-7 Step 35, T35.3).

Pins the four acceptance criteria: a GenAI span fixture (tool call + result
events) → a well-formed Trace with tools + I/O hashes; incomplete spans degrade
gracefully to a partial trace with a logged note (no crash, no fabricated
field); the OTLP endpoint accepts a standard collector payload and returns the
OTLP success response; and ingested live traces are excluded from batch
certification scorecards (the SPEC-1 Step 9 invariant regression).
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

from ascore.ingest import ingest_otlp_payload, ingest_spans, parse_otlp
from ascore.ingest.otel import load_span_dump
from ascore.registry.sqlite_store import Registry
from ascore.server.app import create_app

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
