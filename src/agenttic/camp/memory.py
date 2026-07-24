"""Agent memory as a multi-session environment (SPEC-12 Step 57, M38b).

Memory is the part of an agent's supply chain that nothing else in the platform
could reach: a tool call is a single request/response, but memory is *state that
survives the session boundary*. Every interesting memory defect — a fact leaking
between principals, a deletion that only deleted from one index, a stale answer
winning over a newer one, an instruction smuggled in as a stored "fact" — is
invisible inside one session and obvious across two.

So this module gives the certifier two things:

* :class:`MemoryStore` — the narrow interface a memory backend has to satisfy to
  be testable at all. Deliberately tiny (``write`` / ``read`` / ``forget`` /
  ``stats``) and framework-agnostic, the same way :mod:`tool_suite` is: whatever
  a vector DB, a scratchpad file, or a hosted memory API calls these operations,
  an adapter is a few lines.
* :class:`MemorySessionEnv` — an :class:`~agenttic.camp.environment.Environment`
  (the SPEC-7 Step 29 RL shape, ``reset`` / ``step``) where ``reset()`` ends the
  current session and starts a NEW one against the same store. That is the whole
  point: the session boundary becomes something the harness can cross on purpose.

:class:`ReferenceMemoryStore` is the correct implementation, used as the positive
fixture and as a starting point for real adapters. It is deterministic (an
integer sequence, never wall-clock) so the same probes always produce the same
findings.

Nothing here decides whether a store is good — that is
:mod:`agenttic.certification.memory_suite`. This module only makes the behaviour
reachable.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, Iterable, Protocol, runtime_checkable

from .environment import Environment, StepResult

# Tokens too common to carry retrieval signal (mirrors tool_suite._STOP).
_STOP = frozenset(
    "a an the of to for and or with from this that it its is are be by on in at "
    "was were has have had my your our their what when where who how".split())


def tokens(text: str) -> set[str]:
    """Lowercased content words. The reference retrieval signal — simple on
    purpose, because the certification battery must not be measuring the
    cleverness of one particular retriever."""
    return {w for w in re.findall(r"[a-z0-9]+", (text or "").lower())
            if len(w) > 2 and w not in _STOP}


@dataclass
class MemoryRecord:
    """One remembered item.

    ``principal`` is the isolation boundary — a tenant, an end user, whatever the
    deployment considers "whose memory this is". ``key`` is the optional identity
    of a *fact* (e.g. ``"shipping_address"``), which is what makes contradiction
    resolvable: two writes to the same key are two versions of one fact, not two
    facts. ``seq`` is a monotonic counter rather than a timestamp so ordering is
    deterministic under test.
    """

    record_id: str
    principal: str
    session_id: str
    text: str
    key: str = ""
    seq: int = 0
    tags: tuple[str, ...] = ()
    #: Set by a store that recognises stored content is untrusted input. Content
    #: read back out of memory is data; a store that hands it back as though the
    #: agent had reasoned it is handing an attacker a prompt.
    untrusted: bool = True
    #: Superseded by a later write to the same (principal, key).
    superseded: bool = False

    def as_dict(self) -> dict:
        return {"record_id": self.record_id, "principal": self.principal,
                "session_id": self.session_id, "text": self.text, "key": self.key,
                "seq": self.seq, "tags": list(self.tags),
                "untrusted": self.untrusted, "superseded": self.superseded}


@runtime_checkable
class MemoryStore(Protocol):
    """The interface a memory backend must satisfy to be certifiable.

    Four operations, because a store that cannot do these four cannot be held to
    any useful property:

    ``write``   persist something for a principal, optionally under a fact key
    ``read``    retrieve for a principal — and ONLY that principal
    ``forget``  delete, everywhere, permanently
    ``stats``   disclose capacity and what has been evicted (see
                ``capacity_bound``: a store may drop data, but it may not drop
                data silently)
    """

    def write(self, *, principal: str, session_id: str, text: str,
              key: str = "", tags: Iterable[str] = ()) -> str: ...

    def read(self, *, principal: str, query: str,
             limit: int = 5) -> list[MemoryRecord]: ...

    def forget(self, *, principal: str, record_id: str) -> bool: ...

    def stats(self) -> dict: ...


class ReferenceMemoryStore:
    """A correct in-process memory store.

    Correct meaning: it enforces principal isolation, honours deletion in every
    index it keeps, resolves same-key contradictions in favour of the newer
    write, marks retrieved content as untrusted, and when it hits capacity it
    evicts *and says so*.
    """

    def __init__(self, *, capacity: int = 256):
        self.capacity = capacity
        self._records: dict[str, MemoryRecord] = {}
        self._seq = 0
        self._evicted = 0

    # -- writes -------------------------------------------------------------- #
    def write(self, *, principal: str, session_id: str, text: str,
              key: str = "", tags: Iterable[str] = ()) -> str:
        self._seq += 1
        rid = f"m{self._seq:06d}"
        if key:
            # A second write to the same fact key supersedes the first. The old
            # version is kept (audit) but never wins retrieval.
            for r in self._records.values():
                if r.principal == principal and r.key == key:
                    r.superseded = True
        self._records[rid] = MemoryRecord(
            record_id=rid, principal=principal, session_id=session_id,
            text=text, key=key, seq=self._seq, tags=tuple(tags))
        self._evict_if_needed()
        return rid

    def _evict_if_needed(self) -> None:
        while len(self._records) > self.capacity:
            oldest = min(self._records.values(), key=lambda r: r.seq)
            del self._records[oldest.record_id]
            self._evicted += 1

    # -- reads --------------------------------------------------------------- #
    def read(self, *, principal: str, query: str,
             limit: int = 5) -> list[MemoryRecord]:
        q = tokens(query)
        scored: list[tuple[float, int, MemoryRecord]] = []
        for r in self._records.values():
            if r.principal != principal:          # the isolation boundary
                continue
            if r.superseded:
                continue
            overlap = len(q & tokens(r.text))
            if overlap:
                scored.append((overlap, r.seq, r))
        scored.sort(key=lambda t: (-t[0], -t[1]))
        return [r for _o, _s, r in scored[:limit]]

    # -- deletes ------------------------------------------------------------- #
    def forget(self, *, principal: str, record_id: str) -> bool:
        r = self._records.get(record_id)
        if r is None or r.principal != principal:
            return False
        del self._records[record_id]
        return True

    # -- disclosure ---------------------------------------------------------- #
    def stats(self) -> dict:
        return {"records": len(self._records), "capacity": self.capacity,
                "evicted": self._evicted, "principals":
                    sorted({r.principal for r in self._records.values()})}


# --------------------------------------------------------------------------- #
# the multi-session environment
# --------------------------------------------------------------------------- #

@dataclass
class MemoryTurn:
    """One scripted interaction inside a session."""

    kind: str                       # "write" | "read"
    text: str = ""
    key: str = ""
    #: for a read: substrings that MUST appear in the recalled text, and
    #: substrings that must NOT (a leak or a stale fact).
    expect: tuple[str, ...] = ()
    forbid: tuple[str, ...] = ()
    principal: str = "user-a"


class MemorySessionEnv(Environment):
    """A memory store exercised across session boundaries.

    ``reset()`` ends the current session and opens a new one — the store persists
    across it, everything else does not. That is the only structural difference
    from a single-session environment, and it is the difference that makes memory
    defects reachable.

    ``step()`` performs one :class:`MemoryTurn` and grades it: a write is graded
    on whether the store acknowledged with a usable id, a read on whether the
    expected content came back and the forbidden content stayed out.
    """

    def __init__(self, store: MemoryStore, script: list[MemoryTurn],
                 *, principal: str = "user-a"):
        self.store = store
        self.script = list(script)
        self.principal = principal
        self._session = 0
        self._i = 0
        self.written: list[str] = []

    @property
    def session_id(self) -> str:
        return f"s{self._session}"

    def reset(self) -> Dict[str, Any]:
        """Close the session and open a fresh one against the SAME store."""
        self._session += 1
        self._i = 0
        return {"session_id": self.session_id, "turns": len(self.script)}

    def step(self, action: Dict[str, Any] | None = None) -> StepResult:
        if self._i >= len(self.script):
            raise RuntimeError("session script exhausted; call reset()")
        turn = self.script[self._i]
        self._i += 1
        done = self._i >= len(self.script)
        principal = turn.principal or self.principal

        if turn.kind == "write":
            rid = self.store.write(principal=principal, session_id=self.session_id,
                                   text=turn.text, key=turn.key)
            self.written.append(rid)
            ok = bool(rid)
            return StepResult(observation={"record_id": rid},
                              reward=1.0 if ok else 0.0, done=done,
                              info={"turn": turn, "record_id": rid})

        records = self.store.read(principal=principal, query=turn.text)
        blob = " ".join(r.text for r in records).lower()
        missing = [e for e in turn.expect if e.lower() not in blob]
        leaked = [f for f in turn.forbid if f.lower() in blob]
        ok = not missing and not leaked
        return StepResult(
            observation={"recalled": [r.as_dict() for r in records]},
            reward=1.0 if ok else 0.0, done=done,
            info={"turn": turn, "missing": missing, "leaked": leaked,
                  "records": records})


def run_sessions(env: MemorySessionEnv, n: int = 2) -> list[StepResult]:
    """Drive ``n`` full sessions of the script and return every graded step."""
    out: list[StepResult] = []
    for _ in range(n):
        env.reset()
        while True:
            r = env.step()
            out.append(r)
            if r.done:
                break
    return out


__all__ = [
    "MemoryRecord", "MemoryStore", "ReferenceMemoryStore", "MemorySessionEnv",
    "MemoryTurn", "run_sessions", "tokens",
]
