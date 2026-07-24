"""Memory-store fixtures for the SPEC-12 Step 57 battery.

The good store is :class:`agenttic.camp.memory.ReferenceMemoryStore`.

:class:`LeakyMemoryStore` is the negative fixture, and every defect in it is one
that ships in real systems rather than one invented to fail a test:

* **no principal scoping** — retrieval filters by relevance and forgets that
  memory belongs to somebody. The single most common defect in a memory layer
  bolted onto an existing vector DB.
* **deletion from the primary map only** — ``forget`` removes the record but the
  keyword index keeps serving it, so the deletion request was answered honestly
  and honoured falsely.
* **no supersession** — every write is a new independent "fact", so a rewritten
  address returns both values, or the older one, forever.
* **no untrusted marker** — content is returned as though the agent had thought
  it, which is what turns memory into a prompt-injection channel.
* **unbounded and undisclosed** — accepts writes past any capacity, drops the
  overflow, and reports nothing.
"""

from __future__ import annotations

from agenttic.camp.memory import MemoryRecord, tokens


class LeakyMemoryStore:
    """A memory store with realistic defects. Fails the battery on purpose."""

    def __init__(self, *, capacity: int = 16):
        self.capacity = capacity          # enforced, never disclosed
        self._records: dict[str, MemoryRecord] = {}
        #: the second index that deletion forgets about
        self._keyword: dict[str, list[MemoryRecord]] = {}
        self._seq = 0

    def write(self, *, principal: str, session_id: str, text: str,
              key: str = "", tags=()) -> str:
        self._seq += 1
        rid = f"L{self._seq:06d}"
        rec = MemoryRecord(record_id=rid, principal=principal,
                           session_id=session_id, text=text, key=key,
                           seq=self._seq, tags=tuple(tags),
                           untrusted=False)        # <- defect: trusted by default
        self._records[rid] = rec
        for tok in tokens(text):
            self._keyword.setdefault(tok, []).append(rec)
        # defect: silently drop the oldest past capacity, disclose nothing
        while len(self._records) > self.capacity:
            oldest = min(self._records.values(), key=lambda r: r.seq)
            del self._records[oldest.record_id]
        return rid

    def read(self, *, principal: str, query: str, limit: int = 5):
        # defect: `principal` is accepted and ignored — retrieval is global
        hits: dict[str, tuple[int, MemoryRecord]] = {}
        for tok in tokens(query):
            for rec in self._keyword.get(tok, []):
                n, _ = hits.get(rec.record_id, (0, rec))
                hits[rec.record_id] = (n + 1, rec)
        ranked = sorted(hits.values(), key=lambda t: (-t[0], t[1].seq))
        return [r for _n, r in ranked[:limit]]

    def forget(self, *, principal: str, record_id: str) -> bool:
        # defect: primary map only; the keyword index keeps serving the record
        return self._records.pop(record_id, None) is not None

    def stats(self) -> dict:
        # defect: no record count, no eviction count — loss is invisible
        return {"backend": "leaky"}
