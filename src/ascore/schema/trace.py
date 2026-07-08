"""Trace schema — the keystone contract of the platform.

SCHEMA VERSIONING RULE
----------------------
``SCHEMA_VERSION`` uses semver:

* **MAJOR** bump: a field is removed/renamed, or its type/semantics change.
  All stored traces of older majors require migration before scoring.
* **MINOR** bump: a new optional field or a new ``Span.kind`` value is added.
* **PATCH** bump: docstring/validation-message changes only.

Any change to this module MUST bump ``SCHEMA_VERSION`` and update all test
fixtures in the same commit (Hard Rule 1 in SPEC.md).

Field naming follows OpenTelemetry GenAI semantic conventions where one
exists (e.g. token counts, span timing).
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator

SCHEMA_VERSION = "0.2.0"  # 0.2.0: + optional Trace.source provenance (MINOR)

SpanKind = Literal[
    "llm_call",
    "tool_call",
    "retrieval",
    "agent_decision",
    "error",
    "final_output",
]


class Span(BaseModel):
    """One observable step inside an agent run (UVM: a monitored transaction)."""

    span_id: str
    parent_id: str | None = None
    kind: SpanKind
    name: str
    start_time: datetime
    end_time: datetime
    input: dict = Field(default_factory=dict)
    output: dict = Field(default_factory=dict)
    error: str | None = None
    tokens_in: int | None = None
    tokens_out: int | None = None
    cost_usd: float | None = None
    attributes: dict = Field(default_factory=dict)

    @model_validator(mode="after")
    def _end_not_before_start(self) -> "Span":
        if self.end_time < self.start_time:
            raise ValueError(
                f"span {self.span_id}: end_time precedes start_time"
            )
        return self


class Trace(BaseModel):
    """A complete agent run: ordered spans plus run-level aggregates."""

    trace_id: str
    agent_id: str
    agent_config_hash: str
    test_case_id: str | None = None  # None => live/production trace
    spans: list[Span] = Field(default_factory=list)
    visibility: Literal["glass_box", "black_box"]
    final_output: str
    total_cost_usd: float = 0.0
    total_latency_ms: float = 0.0
    total_steps: int = 0
    # Provenance of the trace. "native" = produced by Agenttic's own scanner;
    # "otel_ingest" = imported from an external OTel-GenAI bus (SPEC-7 Step 35).
    # Ingested traces are additionally stored as mode="live" so they can never
    # enter batch certification scorecards (SPEC-1 Step 9 invariant).
    source: str = "native"
    schema_version: str = SCHEMA_VERSION

    @model_validator(mode="after")
    def _consistency(self) -> "Trace":
        if self.visibility == "glass_box" and not self.spans:
            raise ValueError(
                f"trace {self.trace_id}: glass_box trace must contain spans"
            )
        span_ids = [s.span_id for s in self.spans]
        if len(span_ids) != len(set(span_ids)):
            raise ValueError(f"trace {self.trace_id}: duplicate span_id")
        return self
