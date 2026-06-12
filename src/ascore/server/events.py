"""Execution event bus: persist every event, fan out to live subscribers.

Events are durably written to ExecutionEventRow (replay-on-reconnect is a
SQL query and the executions page gets history for free), then pushed to
per-subscriber asyncio queues. ``publish`` is thread-safe — node code runs
judge/adapter calls in worker threads via asyncio.to_thread, so fan-out
goes through ``loop.call_soon_threadsafe``.
"""

from __future__ import annotations

import asyncio
import threading
from typing import AsyncIterator

from ascore.server.store import UIStore

_SENTINEL = {"type": "__closed__"}


class EventBus:
    def __init__(self, store: UIStore, loop: asyncio.AbstractEventLoop | None = None):
        self.store = store
        self.loop = loop or asyncio.get_running_loop()  # construct inside the loop
        self._seq: dict[str, int] = {}
        self._subs: dict[str, list[asyncio.Queue]] = {}
        self._active: set[str] = set()
        self._lock = threading.Lock()

    def open(self, execution_id: str) -> None:
        """Mark an execution live BEFORE its task starts publishing, so a
        subscriber attaching in the gap streams instead of replay-only."""
        with self._lock:
            self._active.add(execution_id)

    def publish(self, etype: str, execution_id: str,
                node_id: str | None = None, data: dict | None = None) -> None:
        """THREAD-SAFE. Assigns seq, persists, fans out to live subscribers."""
        data = data or {}
        with self._lock:
            if execution_id not in self._seq:
                # resumed execution: continue the persisted sequence
                self._seq[execution_id] = self.store.last_seq(execution_id)
            seq = self._seq[execution_id] + 1
            self._seq[execution_id] = seq
            queues = list(self._subs.get(execution_id, []))
        self.store.append_event(execution_id, seq, etype, node_id, data)
        evt = {"seq": seq, "type": etype, "node_id": node_id, "data": data}
        for q in queues:
            self.loop.call_soon_threadsafe(q.put_nowait, evt)

    def close(self, execution_id: str) -> None:
        """Signal end-of-stream to live subscribers (terminal event sent)."""
        with self._lock:
            queues = list(self._subs.pop(execution_id, []))
            self._seq.pop(execution_id, None)
            self._active.discard(execution_id)
        for q in queues:
            self.loop.call_soon_threadsafe(q.put_nowait, _SENTINEL)

    async def subscribe(self, execution_id: str,
                        after: int = 0) -> AsyncIterator[dict]:
        """Replay persisted events with seq > after, then yield live events.
        Terminates when the execution closes (or immediately if it is
        already finished and no live publisher exists)."""
        q: asyncio.Queue = asyncio.Queue()
        with self._lock:
            live = execution_id in self._active
            self._subs.setdefault(execution_id, []).append(q)
        try:
            replayed = self.store.events_after(execution_id, after)
            last = after
            for evt in replayed:
                last = evt["seq"]
                yield {"seq": evt["seq"], "type": evt["type"],
                       "node_id": evt["node_id"], "data": evt["data"]}
            if not live:
                return  # finished execution: replay only
            while True:
                evt = await q.get()
                if evt is _SENTINEL:
                    return
                if evt["seq"] > last:
                    last = evt["seq"]
                    yield evt
        finally:
            with self._lock:
                subs = self._subs.get(execution_id, [])
                if q in subs:
                    subs.remove(q)
                if not subs:
                    self._subs.pop(execution_id, None)
