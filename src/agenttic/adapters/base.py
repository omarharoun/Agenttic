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
from typing import TYPE_CHECKING, Literal

from agenttic.schema.trace import Trace

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
        pure."""

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
