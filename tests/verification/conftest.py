"""Shared trace builders + the network-block fixture for verification tests."""

from __future__ import annotations

import socket
from datetime import datetime, timedelta, timezone

import pytest

from agenttic.schema.trace import Span, Trace

T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)


def span(kind: str, name: str, *, i: int = 0, input: dict | None = None,
         output: dict | None = None, attributes: dict | None = None,
         error: str | None = None) -> Span:
    return Span(span_id=f"s{i}", kind=kind, name=name,  # type: ignore[arg-type]
                start_time=T0 + timedelta(seconds=i),
                end_time=T0 + timedelta(seconds=i + 1),
                input=input or {}, output=output or {},
                attributes=attributes or {}, error=error)


def trace(*spans: Span, final_output: str = "done",
          visibility: str = "glass_box", test_case_id: str | None = "case-1") -> Trace:
    fixed = [s.model_copy(update={"span_id": f"s{i}"}) for i, s in enumerate(spans)]
    return Trace(trace_id="t-1", agent_id="agent-1", agent_config_hash="cfg",
                 test_case_id=test_case_id, spans=fixed,
                 visibility=visibility,  # type: ignore[arg-type]
                 final_output=final_output)


@pytest.fixture
def no_network(monkeypatch):
    """Any attempt to open a socket fails loudly. Assertion evaluation must be
    able to run under this (SPEC-13 handoff §6: enforce with a network-block
    test)."""
    def _boom(*a, **k):
        raise AssertionError("network access attempted during assertion evaluation")
    monkeypatch.setattr(socket, "socket", _boom)
    monkeypatch.setattr(socket, "create_connection", _boom)
    yield
