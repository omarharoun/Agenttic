"""Adapter base — the driver interface every agent must be wrapped in.

An adapter's single job: accept a test input, run the agent, and emit a
well-formed :class:`~agenttic.schema.trace.Trace`. The harness (Step 3) only
ever talks to this interface, which is what makes "any agent, any framework"
possible.
"""

from __future__ import annotations

import hashlib
import json
from abc import ABC, abstractmethod
from typing import Literal, Protocol

from agenttic.schema.trace import Span, Trace


class EscalationRequired(Exception):
    """A structured human-in-the-loop signal — NOT a failure.

    An adapter raises this when a step needs a human decision before it can
    proceed (e.g. a tool whose autonomy policy is ``human_required``). The
    harness (Step 12) catches it BEFORE any timeout/transport/generic handler,
    routes ``question``/``context`` to a :class:`HumanChannel` if one is present,
    and either re-invokes the adapter with the human's guidance or persists an
    ``ESCALATED_UNRESOLVED`` trace. It is never a dropped run.

    ``partial_trace_spans`` are the spans produced up to the escalation point,
    so the harness can preserve the work already done as evidence.
    """

    def __init__(
        self,
        question: str,
        *,
        context: dict | None = None,
        partial_trace_spans: list[Span] | None = None,
    ):
        super().__init__(question)
        self.question = question
        self.context = context or {}
        self.partial_trace_spans = partial_trace_spans or []


class HumanChannel(Protocol):
    """A human decision endpoint the harness consults on escalation.

    Implementations may be a live reviewer UI, a queue, or a scripted stub in
    tests. ``respond`` returns the human's guidance as a string; the harness
    persists it as :class:`~agenttic.schema.feedback.HumanFeedback` and feeds it
    back to the adapter as ``test_input["human_guidance"]``."""

    def respond(self, question: str, context: dict) -> str: ...


class AgentAdapter(ABC):
    """Abstract driver around an agent under test."""

    agent_id: str
    visibility: Literal["glass_box", "black_box"]

    @abstractmethod
    def describe(self) -> dict:
        """Stable description of the agent configuration (model, prompt,
        tools, ...). Used for the config hash; must be JSON-serializable
        and deterministic."""

    @abstractmethod
    def run(self, test_input: dict, *, test_case_id: str | None = None) -> Trace:
        """Execute one run and return a complete Trace. Agent mistakes must
        be captured as data (error spans), never raised (Hard Rule 5).

        An adapter MAY raise :class:`EscalationRequired` — a structured HITL
        signal (not a failure) the harness handles specially (Step 12)."""

    def config_hash(self) -> str:
        """Hash of the agent configuration; ties every trace/scorecard to the
        exact agent version that produced it."""
        payload = json.dumps(self.describe(), sort_keys=True).encode()
        return hashlib.sha256(payload).hexdigest()[:16]
