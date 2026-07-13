"""SPEC-8 T42.2 — @instrument / session() capture and honest partial degradation.

A decorated custom function emits a canonical run that the existing ingest
pipeline accepts, its output is unchanged, and — because an opaque callable's
internal tool calls can't be observed — the run is marked ``partial`` with a
logged reason and NO fabricated tool trajectory (Hard Rule 39).
"""
from __future__ import annotations

import asyncio
import logging
import tempfile

import pytest

import agenttic
from agenttic.ingest.mapping import ingest_otlp_payload, spans_to_traces
from agenttic.ingest.otel import parse_otlp
from agenttic.registry.sqlite_store import Registry


def _run(sink):
    traces, _d, _r = spans_to_traces(parse_otlp(sink[0]))
    assert len(traces) == 1
    return traces[0]


def test_decorated_function_emits_pipeline_ingestible_run():
    sink: list = []

    @agenttic.instrument(agent_id="refund-bot", sink=sink)
    def agent(query: str) -> str:
        return f"handled: {query}"

    out = agent("refund please")
    assert out == "handled: refund please"          # output unchanged

    # The emitted OTLP payload flows through the real ingest pipeline.
    with tempfile.TemporaryDirectory() as tmp:
        reg = Registry(db_path=f"{tmp}/t.db")
        rep = ingest_otlp_payload(reg, sink[0])
        assert rep["trace_count"] == 1
        tid = rep["saved_trace_ids"][0]
        saved = reg.get_trace(tid)
        assert saved.source == "otel_ingest"
        assert saved.agent_id == "refund-bot"
        assert saved.final_output == "handled: refund please"


def test_partial_trajectory_is_marked_and_never_fabricated(caplog):
    sink: list = []

    @agenttic.instrument(agent_id="opaque", sink=sink)
    def agent(query: str) -> str:
        # calls some hidden LLM/tool client we can't see into
        return "done"

    with caplog.at_level(logging.DEBUG, logger="agenttic"):
        agent("go")

    run = _run(sink)
    span = run.spans[0]
    assert span.attributes.get("agenttic.trajectory") == "partial"
    reason = span.attributes.get("agenttic.trajectory.reason", "")
    assert "partial" in reason and "never fabricated" in reason
    # honest degradation: NO invented tool-call spans
    assert not [s for s in run.spans if s.kind == "tool_call"]


def test_context_manager_matches_decorator_canonical_run():
    dec_sink: list = []
    sess_sink: list = []

    @agenttic.instrument(agent_id="equiv", sink=dec_sink)
    def agent(query: str) -> str:
        return "answer:" + query

    agent("q1")

    with agenttic.session(agent_id="equiv", sink=sess_sink, input="q1") as run:
        run.output = "answer:q1"

    d = _run(dec_sink)
    s = _run(sess_sink)
    assert d.agent_id == s.agent_id == "equiv"
    assert d.final_output == s.final_output == "answer:q1"
    assert [x.kind for x in d.spans] == [x.kind for x in s.spans]
    assert (d.spans[0].attributes.get("agenttic.trajectory")
            == s.spans[0].attributes.get("agenttic.trajectory") == "partial")


def test_async_instrument_is_behavior_identical_and_partial():
    sink: list = []

    @agenttic.instrument(agent_id="async-bot", sink=sink)
    async def agent(query: str) -> dict:
        return {"q": query, "n": len(query)}

    direct = asyncio.run(_call_async_direct(query="hello"))
    wrapped = asyncio.run(agent("hello"))
    assert wrapped == direct
    run = _run(sink)
    assert run.spans[0].attributes.get("agenttic.trajectory") == "partial"


async def _call_async_direct(*, query: str) -> dict:
    return {"q": query, "n": len(query)}


def test_session_on_exception_emits_partial_with_error_reason_and_propagates():
    sink: list = []
    with pytest.raises(RuntimeError):
        with agenttic.session(agent_id="boomer", sink=sink, input="x") as run:
            run.output = "should-not-be-recorded"
            raise RuntimeError("kaboom")

    r = _run(sink)
    span = r.spans[0]
    assert span.attributes.get("agenttic.trajectory") == "partial"
    assert "RuntimeError" in span.attributes.get("agenttic.trajectory.reason", "")
    # output is not recorded when the body failed (no fabricated success)
    assert r.final_output != "should-not-be-recorded"


def test_instrument_bare_and_parameterized_forms_both_work():
    # bare @instrument (no sink -> no-op, but behavior-identical)
    @agenttic.instrument
    def bare(q):
        return q.upper()

    assert bare("hi") == "HI"

    # parameterized
    sink: list = []

    @agenttic.instrument(agent_id="p", sink=sink)
    def param(q):
        return q.lower()

    assert param("HI") == "hi"
    assert _run(sink).agent_id == "p"
