"""Adapter base — the driver interface every agent must be wrapped in.

An adapter's single job: accept a test input, run the agent, and emit a
well-formed :class:`~agenttic.schema.trace.Trace`. The harness (Step 3) only
ever talks to this interface, which is what makes "any agent, any framework"
possible.

Two entry points, and the second one is optional
------------------------------------------------

:meth:`AgentAdapter.run` is the original and is UNCHANGED — one dict in, one
Trace out. Four real adapters and every ad-hoc test double in the suite subclass
this, so widening its signature would be a MAJOR break for a capability most
agents under test do not have.

:meth:`AgentAdapter.converse` is the multi-turn entry point, added additively:
its default implementation raises, and :meth:`AgentAdapter.supports_sessions`
reports whether a subclass supplied one. So "this adapter can hold a
conversation" is a question a caller can ASK rather than discover by catching an
exception — which matters because ``session_shape`` coverage is a claim about
what a run exhibited, and an adapter that cannot take a second turn must not
have one attributed to it.
"""

from __future__ import annotations

import hashlib
import json
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Literal, Protocol

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

if TYPE_CHECKING:  # pragma: no cover — import guarded to keep `adapters` a leaf
    from agenttic.scenario.session import Session


class SessionsUnsupported(NotImplementedError):
    """Raised by :meth:`AgentAdapter.converse` — this agent takes one turn.

    Deliberately loud, and deliberately NOT a fallback that replays the session's
    turns through :meth:`AgentAdapter.run` one at a time. That fallback is the
    tempting one and it is the defect: ``run`` delivers a single dict with no
    memory of anything before it (see its contract below), so an agent driven
    that way answers turn three having never seen turns one and two. The trace
    would still carry three ``user_turn`` spans, ``session_multi_turn`` would be
    credited, and closure would move for a conversation the agent never had.

    Same shape and same reason as
    :class:`~agenttic.scenario.runner.ScenarioAgentMisuse`: the path that cannot
    be implemented honestly is not implemented at all, and it names the adapter
    so the caller knows which one to fix.
    """


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

        **Must be re-entrant.** ``harness.runner.run_suite`` holds ONE adapter
        object for the whole suite and calls this method from up to
        ``max_parallel`` worker threads at once (``asyncio.to_thread``). So
        anything ``run`` writes to ``self`` is shared with every other case in
        flight: the last writer wins and the other runs read a foreign case's
        state. Per-run state belongs in locals or on the returned ``Trace`` — the
        Trace is the run's record, and reading the trace back is always safe
        where reading ``self`` after the fact is not.

        The harness does NOT clone adapters, deliberately: some adapter state is
        *meant* to be shared across the suite (``BlackBoxHTTPAgent._last_call``
        is the per-agent rate-limit clock — per-case copies would multiply the
        request rate against a customer endpoint by ``max_parallel``). Genuinely
        shared state like that must be guarded by a lock, not copied.

        ``describe()``/``config_hash()`` are read concurrently too, and must stay
        pure.

        An adapter MAY raise :class:`EscalationRequired` — a structured HITL
        signal (not a failure) the harness handles specially (Step 12)."""

    # -- cancellation, optional -------------------------------------------

    def abort_run(self, test_case_id: str | None = None) -> None:
        """Stop the work started for ``test_case_id``. Default: nothing to do.

        The harness runs adapters via ``asyncio.to_thread`` and its timeout
        cancels the *await*, not the thread — the worker keeps going until the
        adapter's own deadline. For an in-process adapter that only wastes a
        thread. For an adapter that SPAWNED something, the agent keeps running
        after the harness has stopped caring: measured on a real run, two agents
        were still alive ~40 minutes later, still spending against the user's
        API key, on a suite that had already given up on them.

        So ``runner.run_suite`` calls this on timeout. Adapters that own a
        subprocess override it to kill the child; everyone else inherits a no-op.

        **Must be safe to call from another thread**, and safe to call for a case
        that already finished or never started — the harness cannot know which.
        Like ``BlackBoxHTTPAgent``'s rate-limit clock, the bookkeeping this needs
        is genuinely shared state and must be guarded by a lock rather than
        avoided (see the ``run`` contract below).
        """

    # -- multi-turn, optional ---------------------------------------------

    def converse(self, session: "Session") -> Trace:
        """Drive a whole conversation and return ONE Trace covering all of it.

        Optional. The default raises :class:`SessionsUnsupported`; an adapter
        opts in by overriding, and :meth:`supports_sessions` then reports True
        without the adapter declaring anything twice.

        **The session owns the state, not the adapter, and not ``self``.**
        ``harness.runner.run_suite`` holds ONE adapter object for the whole suite
        and enters it from up to ``max_parallel`` threads at once, so a
        conversation parked on ``self`` would be read and overwritten by every
        other case in flight — the same race the ``run`` contract below spells
        out, except that a session lives across many calls instead of one and so
        would lose to it every time. Turn history, accumulated spans and the
        session id therefore live on the
        :class:`~agenttic.scenario.session.Session` the caller passes in, which
        is per-conversation by construction. An implementation reads its turns
        from ``session.deliver()``, hands spans back with ``session.record(...)``,
        and returns ``session.to_trace(self)``.

        Agent mistakes are captured as data, never raised (Hard Rule 5) — same as
        ``run``. :class:`SessionsUnsupported` is not an agent mistake: it is the
        harness being pointed at the wrong adapter.
        """
        raise SessionsUnsupported(
            f"{type(self).__name__} cannot hold a multi-turn session: it "
            "implements run(test_input) only, which delivers one message with "
            "no memory of any turn before it. Implement "
            f"{type(self).__name__}.converse(session) — reading turns from "
            "session.deliver() and returning session.to_trace(self) — or check "
            "adapter.supports_sessions() before driving one.")

    @classmethod
    def supports_sessions(cls) -> bool:
        """Can this adapter be driven with :meth:`converse`?

        Derived from whether the subclass overrode ``converse``, rather than from
        a flag the subclass sets: a flag is a second place to state one fact, and
        the failure mode is an adapter that advertises sessions and raises, which
        a caller checking the flag would meet as a crash at turn one. Nothing to
        keep in sync means nothing to get out of sync.

        A classmethod because it is a fact about the CLASS and is read
        concurrently across cases; it touches no instance state.
        """
        return cls.converse is not AgentAdapter.converse

    def config_hash(self) -> str:
        """Hash of the agent configuration; ties every trace/scorecard to the
        exact agent version that produced it."""
        payload = json.dumps(self.describe(), sort_keys=True).encode()
        return hashlib.sha256(payload).hexdigest()[:16]
