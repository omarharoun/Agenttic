"""Registry package. Step 6 replaces this with the SQLite store; until then
an in-memory implementation of the harness's TraceStore protocol."""

from __future__ import annotations

from agenttic.schema.trace import Trace


class InMemoryTraceStore:
    """Dev/test stand-in for the SQLite registry (Step 6)."""

    def __init__(self) -> None:
        self.traces: list[Trace] = []

    def save_trace(self, trace: Trace) -> None:
        self.traces.append(trace)
