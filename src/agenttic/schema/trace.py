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

SCHEMA_VERSION = "0.5.0"  # 0.5.0: + escalation SpanKind & Trace.escalated (HITL, MINOR)
#
# 0.3.0 added the ``user_turn``/``env_step`` span kinds. Why both kinds landed in
# ONE bump: a new ``SpanKind`` member is MINOR by the rule above, and every stored
# trace stamps ``schema_version`` from this constant. Two bumps for two additive
# members would give one change two version strings and make traces written
# between them look like a distinct generation of the schema — for no benefit,
# since neither member alters how an existing trace validates.
#
# 0.4.0 adds ``Trace.session_id``, optional and defaulting to None, so every
# stored trace and every fixture stays valid unread: a trace without one is a
# single-shot run, which is what every trace written before this bump was. It is
# MINOR and not MAJOR because nothing about an existing field moved.
#
# The bump costs one line here because the fixtures read SCHEMA_VERSION rather
# than a literal. That claim was verified, not assumed, before this edit:
# ``grep -rn '0\.3\.0' tests/ src/`` returns only this module and one prose
# reference in verification/builtins.py — no fixture, JSON or YAML anywhere in
# the tree pins a version string.
#
# 0.5.0 adds the ``escalation`` span kind and ``Trace.escalated`` (HITL, Step 12).
# The local line issued these as 0.3.0 before this history was reconciled; that
# number was already spent on the ``user_turn``/``env_step`` bump above, and two
# schemas sharing a version is exactly what the rule at the top forbids. Both
# additions are MINOR — a new ``SpanKind`` member and an optional field — so this
# lands as one bump past 0.4.0 rather than reopening a spent one.

SpanKind = Literal[
    "llm_call",
    "tool_call",
    "retrieval",
    "agent_decision",
    "error",
    "final_output",
    # The COUNTERPARTY speaking — a human, or a simulated one. The only thing
    # that starts a turn, and the reason this kind exists: `session_shape`
    # coverage was counting `llm_call` spans, so one human message that provoked
    # a three-tool loop was recorded as a multi-turn session. Turns are a
    # property of who spoke, and nothing in the schema could express that.
    #
    # Adding the kind made the bin CONSTRUCTIBLE, which is not the same as
    # measured — conflating those two is the bug this whole change exists to
    # undo. A producer now exists: `scenario/session.py` emits `user_turn`, and a
    # session driven by `scenario/user.py` produces several, which the coverage
    # extractor credits to `session_multi_turn` off the trace.
    #
    # `session_shape` is STILL declared not-measurable, and the reason moved
    # rather than disappeared: measurability is declared per coverage MODEL, not
    # per sample, and the path a stored suite takes still emits no turn markers
    # at all. One instrumented batch cannot speak for an uninstrumented one. See
    # that coverpoint's own `not_measurable_reason`, and `extractors._single` for
    # the predicate-versus-reason disagreement the flag is currently hiding.
    "user_turn",
    # The environment acting on its own account: a fault injector firing a
    # timeout, seeded memory being written, a session resumed against prior
    # state. Distinct from `tool_call`, which is the agent acting ON the
    # environment — a fault the harness injected must never be readable as
    # something the agent did.
    "env_step",
    "escalation",
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
    # The conversation this run belongs to, when it belongs to one.
    #
    # ``None`` is the honest default and the reason this is optional: every trace
    # ever written by this platform is one dict delivered as one user message
    # (adapters/base.py `run`), which is not a session — it is a run that had no
    # conversation around it. Stamping those with a synthetic session id would
    # make "this run was part of a conversation" unfalsifiable, and the
    # `session_shape` coverpoint exists precisely because that distinction was
    # being guessed rather than recorded.
    #
    # Set by `scenario.session.Session.to_trace`, which owns the id. Deliberately
    # NOT a foreign key the schema enforces: an ingested trace may carry a
    # session id minted by a producer this platform has never seen, and refusing
    # it would mean dropping the one field that says the turns belong together.
    session_id: str | None = None
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
    # HITL (SPEC-2 Step 12): True when this run was escalated to a human — either
    # resolved with human guidance and completed, or persisted unresolved
    # (final_output=="ESCALATED_UNRESOLVED") when no human channel was available.
    escalated: bool = False
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
