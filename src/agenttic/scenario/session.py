"""Multi-turn sessions — the object that owns conversation state (P3).

Until this module, one run was one dict delivered as one user message
(``adapters/base.py`` ``run``). There was no session object and no return channel
for state, so ``session_shape`` coverage had nothing to read: the coverpoint is
declared ``measurable=False`` in
``coverage/models/conversational_transactional.py`` with the reason "nothing
emits a ``user_turn`` span". This is the producer that reason was waiting for.

What a Session is, and who owns it
----------------------------------

**The session owns the state. The adapter never does.**
``harness/runner.py:137`` holds ONE adapter object for a whole suite and enters
it from up to ``max_parallel`` threads at once, so per-run state on ``self`` is
already a live race — the last writer wins and the other cases read a foreign
run's state. A conversation is per-run state that lives across many calls instead
of inside one, so it would lose that race every time and lose it silently. Turn
history, accumulated spans, the session id, the cost and latency totals and the
disclosure log therefore all live here, on an object the caller creates per
conversation. An adapter's :meth:`~agenttic.adapters.base.AgentAdapter.converse`
is handed one and writes nothing to ``self``.

The session, not the adapter, emits the turn spans
--------------------------------------------------

:meth:`Session.deliver` is a generator: it appends the ``user_turn`` span and
*then* yields the message. An adapter cannot take a turn without the span
existing, and cannot mint a span for a turn it was never handed. That is
deliberate — ``session_multi_turn`` is a claim about what a run EXHIBITED, and
leaving the marker to the adapter would make it a claim about what the adapter
chose to report.

What coverage actually reads — verified, not assumed
----------------------------------------------------

``coverage/extractors.py`` was read before a span here was designed. It reads:

* ``_human_turns`` counts spans with ``kind == "user_turn"`` — one per delivered
  message, which is exactly what :meth:`Session.deliver` emits. Three turns give
  ``session_multi_turn``; one gives ``session_single_turn``.
* ``session_resumed_with_memory`` reads the span ATTRIBUTE ``resumed`` /
  ``memory_seeded`` from *any* span (``_attr``), not a span kind. So a session
  constructed with ``prior_state`` stamps ``resumed=True`` on the ``env_step``
  span that records the seeding; the kind alone would credit nothing.

**Nothing in ``extractors.py`` reads ``kind == "env_step"``.** The kind is
coverage-inert today: it exists so an environment action is not readable as
something the agent did (a ``tool_call``), and it earns its place here for that
reason and no other. It is recorded and reported as a mismatch rather than
worked around by mislabelling an environment step as a tool call.

Two things this module cannot do from here, both in files it does not own:

* ``SESSION_SHAPE.measurable`` is still ``False`` in
  ``coverage/models/conversational_transactional.py``. Until it is flipped,
  ``collect.py`` reports every bin of that coverpoint as "not measurable" no
  matter what a trace carries — so a session run still will not move closure.
* ``resumed_with_memory`` in that same model is ``waived=True``. ``prior_state``
  gives it an honest producer for the first time; un-waiving it is that file's
  call.

Offline by construction
-----------------------

No network, no wall clock, no unseeded randomness — except the session id, which
is a uuid unless the caller supplies one. Span timestamps come from the world's
``EPOCH`` plus a per-event tick, so a scripted session replays to the same bytes.
Nothing here needs an API key; :func:`run_session` against a scripted adapter
runs under a network block.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Iterable, Iterator

from agenttic.adapters.base import AgentAdapter
from agenttic.scenario.env import EPOCH
from agenttic.schema.trace import SCHEMA_VERSION, Span, Trace

#: The fixed span NAME every user turn carries. Deliberately a constant token and
#: never the message text: ``verification/builtins.py`` classifies a span by its
#: name (``_lead_verb``/``_IRREVERSIBLE_MARKERS``), so a turn named "cancel my
#: order please" would read as an irreversible write the agent performed. The
#: message travels in ``Span.input``, which those classifiers do not read for
#: risk.
USER_TURN_NAME = "user_turn"

#: The env_step recorded when a session is resumed against prior state. Its
#: ATTRIBUTE is what ``session_resumed_with_memory`` reads; the name is for a
#: human.
RESUME_STEP_NAME = "session_resumed"


class SessionContractError(ValueError):
    """An adapter returned a Trace that does not belong to the session it was
    given.

    Reachable, not defensive: an implementation that assembles its own
    :class:`~agenttic.schema.trace.Trace` instead of calling
    :meth:`Session.to_trace` produces one with ``session_id=None``, and that
    trace would be stored as a single-shot run while three ``user_turn`` spans
    inside it said otherwise. Stamping the id on silently would hide exactly the
    bug worth seeing.
    """


@dataclass(frozen=True)
class Turn:
    """One message from the counterparty, and the span that records it."""

    #: 1-based position in the conversation.
    index: int
    #: the normalized message dict handed to the agent
    message: dict
    #: the ``user_turn`` span this turn produced, by id
    span_id: str


class Session:
    """One conversation: its id, its turns, and every span they produced.

    Construct with the messages the counterparty will send, drive it with
    :func:`run_session`, and read the result off :meth:`to_trace`. Messages may
    also be added mid-conversation with :meth:`enqueue`, which is what a
    simulated user reacting to the agent's last answer needs.
    """

    def __init__(self, messages: Iterable[str | dict] = (), *,
                 session_id: str | None = None,
                 prior_state: dict | None = None,
                 test_case_id: str | None = None,
                 epoch: datetime = EPOCH) -> None:
        self.session_id = session_id or f"sess-{uuid.uuid4().hex[:16]}"
        self.test_case_id = test_case_id
        #: state this session was resumed against, or ``None`` for a fresh one
        self.prior_state = dict(prior_state) if prior_state is not None else None
        self.turns: list[Turn] = []
        self.spans: list[Span] = []
        #: things this session could not represent faithfully, in the caller's
        #: words. Never empty-and-ignored: :meth:`to_trace` writes them onto the
        #: trace as well, so they survive being persisted and read back.
        self.disclosures: list[str] = []

        self._queue: list[dict] = [self._normalize(m) for m in messages]
        self._epoch = epoch
        self._tick = 0
        self._env_n = 0
        self._ids: set[str] = set()
        self._cost_usd = 0.0
        self._latency_ms = 0.0
        self._final = ""

        if self.prior_state:
            self.env_step(RESUME_STEP_NAME,
                          output={"seeded_keys": sorted(self.prior_state)},
                          # what `session_resumed_with_memory` reads (extractors
                          # `_attr`), and the only reason this step exists
                          attributes={"resumed": True, "memory_seeded": True})
        elif self.prior_state is not None:
            # An empty dict is not prior state. Crediting `resumed_with_memory`
            # for a resume against nothing is the vacuity rule inverted, so the
            # step is not emitted — and the caller is told, rather than left to
            # wonder why the bin never fired.
            self.disclosures.append(
                "prior_state was empty, so no session_resumed step was emitted: "
                "a resume against no state is not a resume")

    # -- turns -------------------------------------------------------------

    def enqueue(self, message: str | dict) -> None:
        """Queue another counterparty message. Delivered by the next
        :meth:`deliver` iteration, so a simulated user can react to what the
        agent just said."""
        self._queue.append(self._normalize(message))

    @property
    def pending(self) -> int:
        """Messages queued and not yet delivered."""
        return len(self._queue)

    def deliver(self) -> Iterator[Turn]:
        """Yield each queued message, emitting its ``user_turn`` span FIRST.

        A generator rather than a list so a message enqueued mid-conversation is
        picked up by the same loop; the queue is drained as it goes, so calling
        it twice does not replay a turn.
        """
        while self._queue:
            yield self._open(self._queue.pop(0))

    def _open(self, message: dict) -> Turn:
        index = len(self.turns) + 1
        span_id = self._claim(f"turn-{index:03d}")
        t = self._clock()
        self.spans.append(Span(
            span_id=span_id, kind="user_turn", name=USER_TURN_NAME,
            start_time=t, end_time=t,
            input=dict(message), attributes={"turn_index": index}))
        turn = Turn(index=index, message=dict(message), span_id=span_id)
        self.turns.append(turn)
        return turn

    # -- the environment acting on its own account -------------------------

    def env_step(self, name: str, *, input: dict | None = None,
                 output: dict | None = None, attributes: dict | None = None,
                 error: str | None = None) -> Span:
        """Record something the ENVIRONMENT did, not something the agent did.

        Separate from a ``tool_call`` on purpose (see ``schema/trace.py``): a
        fault the harness injected, or state the harness seeded, must never be
        readable as an action the agent took. Returns the span so a caller can
        assert on it.
        """
        self._env_n += 1
        span_id = self._claim(f"env-{self._env_n:03d}")
        t = self._clock()
        span = Span(span_id=span_id, kind="env_step", name=name,
                    start_time=t, end_time=t,
                    input=dict(input or {}), output=dict(output or {}),
                    error=error, attributes=dict(attributes or {}))
        self.spans.append(span)
        return span

    # -- what the agent produced -------------------------------------------

    def record(self, produced: Trace | Iterable[Span]) -> list[Span]:
        """Accumulate the spans one turn produced. Returns them as stored.

        Accepts a whole :class:`~agenttic.schema.trace.Trace` — which is what an
        adapter reusing its own single-turn machinery already has — or a bare
        span list. A Trace contributes its cost, its measured latency and its
        final text as well; taking only its spans would drop three measurements
        the session is supposed to total.

        **Span ids are namespaced per turn, and parents are rewritten with
        them.** An adapter that numbers its spans ``llm-000`` restarts at
        ``llm-000`` on turn two, and ``Trace`` rejects a duplicate ``span_id``
        outright — so a session that merely concatenated would fail to build at
        turn two, with a message about duplicate ids rather than about turns.
        Ids become ``t2.llm-000``; ``parent_id`` is remapped through the same
        batch so the tree survives. A parent naming a span outside its own batch
        cannot be remapped (the id is ambiguous across turns), so it is cleared
        and DISCLOSED rather than left pointing at whatever now holds that id.
        """
        if isinstance(produced, Trace):
            spans = list(produced.spans)
            self._cost_usd += produced.total_cost_usd
            self._latency_ms += produced.total_latency_ms
            if (produced.final_output or "").strip():
                self._final = produced.final_output
            if produced.session_id not in (None, self.session_id):
                self.disclosures.append(
                    f"recorded a trace stamped session_id "
                    f"{produced.session_id!r}, which is not this session "
                    f"({self.session_id!r}); its spans were kept and the "
                    "foreign id was not")
        else:
            spans = list(produced)
            # No trace-level totals exist for a bare span list, so cost is summed
            # from the spans and latency stays 0 — an unmeasured latency is 0,
            # not an invented one.
            self._cost_usd += sum(s.cost_usd or 0.0 for s in spans)

        prefix = f"t{len(self.turns)}."
        renamed: dict[str, str] = {}
        staged: list[tuple[Span, str]] = []
        for s in spans:
            claimed = self._claim(prefix + s.span_id)
            renamed.setdefault(s.span_id, claimed)
            staged.append((s, claimed))

        out: list[Span] = []
        for s, claimed in staged:
            parent = s.parent_id
            if parent:
                if parent in renamed:
                    parent = renamed[parent]
                else:
                    self.disclosures.append(
                        f"span {s.span_id!r} named parent {parent!r}, which is "
                        f"not in the batch recorded for turn {len(self.turns)}; "
                        "the link was cleared because a bare id is ambiguous "
                        "across turns")
                    parent = None
            out.append(s.model_copy(update={"span_id": claimed,
                                            "parent_id": parent}))
        self.spans.extend(out)
        return out

    # -- the result --------------------------------------------------------

    def to_trace(self, adapter: AgentAdapter | None = None, *,
                 agent_id: str | None = None,
                 agent_config_hash: str | None = None,
                 visibility: str | None = None,
                 final_output: str | None = None,
                 trace_id: str | None = None,
                 source: str = "native") -> Trace:
        """Build the one Trace covering the whole conversation.

        Identity comes from ``adapter`` when one is passed — the agent under test
        is the authority on its own id, visibility and config hash, and a session
        that restated them would be a second place for them to drift. The keyword
        overrides exist for a caller that has no adapter object (a replay, a
        test).

        Does not mutate the session: it can be called again after more turns.
        ``total_steps`` counts ``llm_call`` and ``tool_call`` spans, the same
        definition ``scenario/runner.py`` uses — turns are counted by the
        ``user_turn`` spans and are deliberately not steps.
        """
        if adapter is not None:
            agent_id = agent_id or adapter.agent_id
            agent_config_hash = agent_config_hash or adapter.config_hash()
            visibility = visibility or adapter.visibility
        missing = [n for n, v in (("agent_id", agent_id),
                                  ("agent_config_hash", agent_config_hash),
                                  ("visibility", visibility)) if not v]
        if missing:
            raise SessionContractError(
                f"session {self.session_id}: cannot build a trace without "
                f"{', '.join(missing)} — pass the adapter that ran it, or "
                "supply the fields explicitly.")

        spans = list(self.spans)
        if not spans:
            raise SessionContractError(
                f"session {self.session_id}: no spans — nothing was delivered "
                "and nothing was recorded, so there is no run to describe. "
                "Drive it with run_session(session, adapter) first.")
        if self.disclosures:
            # The disclosures ride ON the trace, not only on this object: a
            # session is discarded after the run and the trace is what gets
            # stored, so a caller reading the record back would otherwise never
            # learn that an id was rewritten or a parent link cleared.
            t = self._epoch + timedelta(seconds=self._tick + 1)
            spans.append(Span(
                span_id="env-disclosure", kind="env_step",
                name="session_disclosure", start_time=t, end_time=t,
                output={"disclosures": list(self.disclosures)}))

        text = final_output if final_output is not None else self._final
        if not text:
            for s in reversed(spans):
                if s.kind == "final_output":
                    text = str((s.output or {}).get("text") or "")
                    if text:
                        break

        return Trace(
            trace_id=trace_id or uuid.uuid4().hex,
            agent_id=agent_id, agent_config_hash=agent_config_hash,
            test_case_id=self.test_case_id,
            session_id=self.session_id,
            spans=spans, visibility=visibility, final_output=text,
            total_cost_usd=self._cost_usd,
            total_latency_ms=self._latency_ms,
            total_steps=sum(1 for s in spans
                            if s.kind in ("llm_call", "tool_call")),
            source=source, schema_version=SCHEMA_VERSION)

    # -- internals ---------------------------------------------------------

    @staticmethod
    def _normalize(message: str | dict) -> dict:
        """A message is a dict, the same shape ``run(test_input)`` takes."""
        if isinstance(message, dict):
            return dict(message)
        if isinstance(message, str):
            return {"message": message}
        raise TypeError(
            f"a session message must be a str or a dict, not "
            f"{type(message).__name__}: the agent is handed the dict as-is, so "
            "there is no correct way to guess a field name for this.")

    def _clock(self) -> datetime:
        """One second per session event, from the world's zero. An ORDER, not a
        duration — nothing here measures latency and it will not invent one."""
        self._tick += 1
        return self._epoch + timedelta(seconds=self._tick)

    def _claim(self, span_id: str) -> str:
        """Reserve a span id unique within this session, disclosing a rewrite."""
        if span_id not in self._ids:
            self._ids.add(span_id)
            return span_id
        n = 2
        while f"{span_id}#{n}" in self._ids:
            n += 1
        out = f"{span_id}#{n}"
        self._ids.add(out)
        self.disclosures.append(
            f"span_id {span_id!r} was already used in this session; the second "
            f"one was recorded as {out!r}")
        return out


def run_session(session: Session, adapter: AgentAdapter) -> Trace:
    """Drive one session against one adapter.

    Raises :class:`~agenttic.adapters.base.SessionsUnsupported` (from
    ``converse``'s default, which names the adapter) when the agent takes one
    turn only. Check :meth:`~agenttic.adapters.base.AgentAdapter.supports_sessions`
    first if that is a question rather than an error.

    The returned trace must belong to this session. That check is not
    ceremonial: an adapter that builds its own ``Trace`` rather than calling
    :meth:`Session.to_trace` returns one with ``session_id=None``, which would be
    stored as a single-shot run carrying multi-turn spans.
    """
    trace = adapter.converse(session)
    if trace.session_id != session.session_id:
        raise SessionContractError(
            f"{type(adapter).__name__}.converse returned a trace with "
            f"session_id={trace.session_id!r}, but it was driving session "
            f"{session.session_id!r}. Return session.to_trace(self) — a trace "
            "that does not name its session is indistinguishable from a "
            "single-shot run once it is stored.")
    return trace
