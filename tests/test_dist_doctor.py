"""SPEC-8 T44.2 — `agenttic doctor` reports success on a valid span stream and a
specific, actionable failure on a malformed one (and probes a target endpoint).
"""
from __future__ import annotations

import tempfile

from ascore.ingest.doctor import (
    diagnose_payload,
    probe_payload,
    probe_target,
)
from ascore.ingest.emit import SpanEmitter
from ascore.ingest.mapping import ingest_otlp_payload
from ascore.ingest.otel import otlp_success_response
from ascore.registry.sqlite_store import Registry


def _good_payload() -> dict:
    em = SpanEmitter("doc-agent", scope_name="fixture", sink=[])
    llm = em.emit_llm_call(system="openai", model="gpt", prompt="hi",
                           completion="hello", input_tokens=3, output_tokens=2)
    em.emit_tool_call(tool_name="search", arguments={"q": "x"}, result="r",
                      parent_id=llm)
    em.flush()
    return em.sink[0]


# --- diagnose a captured span stream ---------------------------------------
def test_diagnose_reports_success_on_valid_stream():
    rep = diagnose_payload(_good_payload())
    assert rep["ok"] is True
    assert rep["spans"] == 2
    assert rep["traces"] == 1
    assert rep["llm_calls"] == 1
    assert rep["tool_calls"] == 1
    assert "doc-agent" in rep["agents"]
    assert rep["problems"] == []


def test_diagnose_reports_actionable_failure_on_malformed():
    # not an OTLP envelope at all
    rep = diagnose_payload({"totally": "wrong"})
    assert rep["ok"] is False
    assert rep["problems"]
    joined = " ".join(rep["problems"]).lower()
    assert "no spans" in joined or "could not parse" in joined


def test_diagnose_flags_non_genai_spans_but_still_parses():
    # a span with no gen_ai.* attributes -> parses, but flagged (NOT ASSESSED)
    payload = {"resourceSpans": [{
        "resource": {"attributes": []},
        "scopeSpans": [{"scope": {"name": "x"}, "spans": [{
            "traceId": "a" * 32, "spanId": "b" * 16, "name": "misc",
            "startTimeUnixNano": "1", "endTimeUnixNano": "2",
            "attributes": [], "events": [], "status": {"code": 1},
        }]}],
    }]}
    rep = diagnose_payload(payload)
    assert rep["ok"] is True                 # spans arrived and parsed
    assert rep["llm_calls"] == 0 and rep["tool_calls"] == 0
    assert any("gen_ai" in p or "NOT ASSESSED" in p for p in rep["problems"])


# --- probe a target endpoint (poster injected; no live network) ------------
def _ingest_poster(url, payload, auth_header):
    """A poster that runs the real ingest against a temp Registry, mimicking a
    healthy /v1/traces endpoint's OTLP response."""
    with tempfile.TemporaryDirectory() as tmp:
        reg = Registry(db_path=f"{tmp}/t.db")
        rep = ingest_otlp_payload(reg, payload)
        rejected = sum(1 for n in rep.get("notes", [])
                       if n.startswith(("skipped_span:", "empty_trace:")))
    return 200, otlp_success_response(rejected=rejected)


def test_probe_target_success_via_real_ingest():
    rep = probe_target("https://example/v1/traces", poster=_ingest_poster)
    assert rep["ok"] is True
    assert rep["status"] == 200


def test_probe_target_reports_unreachable():
    def boom(url, payload, auth_header):
        raise ConnectionError("refused")

    rep = probe_target("https://nope/v1/traces", poster=boom)
    assert rep["ok"] is False
    assert any("could not reach" in p for p in rep["problems"])


def test_probe_target_reports_protobuf_rejection():
    def poster_415(url, payload, auth_header):
        return 415, {"error": "protobuf not supported"}

    rep = probe_target("https://x/v1/traces", poster=poster_415)
    assert rep["ok"] is False
    assert any("json" in p.lower() for p in rep["problems"])


def test_probe_target_reports_rejected_spans():
    def poster_partial(url, payload, auth_header):
        return 200, {"partialSuccess": {"rejectedSpans": "1",
                                        "errorMessage": "1 span could not be mapped"}}

    rep = probe_target("https://x/v1/traces", poster=poster_partial)
    assert rep["ok"] is False
    assert rep.get("rejected") == 1


def test_probe_payload_is_valid_and_ingestible():
    rep = diagnose_payload(probe_payload())
    assert rep["ok"] is True and rep["llm_calls"] == 1
