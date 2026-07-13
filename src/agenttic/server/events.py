"""Execution event bus: persist every event, fan out to live subscribers.

Events are durably written to ExecutionEventRow (replay-on-reconnect is a SQL
query), then delivered to live SSE subscribers through a pluggable **transport**:

* ``InMemoryTransport`` (default) — per-process asyncio queues; single worker.
* ``RedisTransport`` — Redis pub/sub, so an SSE client connected to ANY worker
  receives events published by any other worker (true multi-worker).

Liveness is decided from the persisted execution status (terminal => replay
only), which is backend-uniform. ``publish`` is thread-safe (node code emits
from worker threads via asyncio.to_thread).
"""

from __future__ import annotations

import asyncio
import json
import threading
from collections import defaultdict
from typing import AsyncIterator

from agenttic.server.store import UIStore

_TERMINAL = {"succeeded", "failed", "cancelled", "interrupted",
             "completed_with_errors"}
_SENTINEL = object()


def _channel(execution_id: str) -> str:
    return f"ascore:events:{execution_id}"


# -- in-memory transport (default) ------------------------------------------

class _InMemorySubscription:
    def __init__(self, transport: "InMemoryTransport", execution_id: str):
        self._t = transport
        self._eid = execution_id
        self.queue: asyncio.Queue = asyncio.Queue()

    async def listen(self) -> AsyncIterator[dict]:
        while True:
            evt = await self.queue.get()
            if evt is _SENTINEL:
                return
            yield evt

    async def close(self) -> None:
        with self._t._lock:
            subs = self._t._subs.get(self._eid, [])
            if self in subs:
                subs.remove(self)
            if not subs:
                self._t._subs.pop(self._eid, None)


class InMemoryTransport:
    def __init__(self, loop: asyncio.AbstractEventLoop):
        self.loop = loop
        self._subs: dict[str, list[_InMemorySubscription]] = defaultdict(list)
        self._lock = threading.Lock()

    async def open_subscription(self, execution_id: str) -> _InMemorySubscription:
        sub = _InMemorySubscription(self, execution_id)
        with self._lock:
            self._subs[execution_id].append(sub)
        return sub

    def publish(self, execution_id: str, evt: dict) -> None:
        with self._lock:
            subs = list(self._subs.get(execution_id, []))
        for sub in subs:
            self.loop.call_soon_threadsafe(sub.queue.put_nowait, evt)

    def close(self, execution_id: str) -> None:
        with self._lock:
            subs = list(self._subs.get(execution_id, []))
        for sub in subs:
            self.loop.call_soon_threadsafe(sub.queue.put_nowait, _SENTINEL)


# -- redis transport (multi-worker) -----------------------------------------

class _RedisSubscription:
    def __init__(self, client, pubsub, channel):
        self._client = client
        self._pubsub = pubsub
        self._channel = channel

    async def listen(self) -> AsyncIterator[dict]:
        async for msg in self._pubsub.listen():
            if msg.get("type") != "message":
                continue
            evt = json.loads(msg["data"])
            if evt.get("__sentinel__"):
                return
            yield evt

    async def close(self) -> None:
        try:
            await self._pubsub.unsubscribe(self._channel)
            await self._pubsub.aclose()
            await self._client.aclose()
        except Exception:  # noqa: BLE001 — best-effort teardown
            pass


class RedisTransport:
    """Publishes from a sync client (called from worker threads); subscribes via
    redis.asyncio. ``redis`` is imported lazily so it's only required when used."""

    def __init__(self, url: str):
        self.url = url
        self._sync = None

    @property
    def sync(self):
        if self._sync is None:
            import redis  # lazy
            self._sync = redis.Redis.from_url(self.url)
        return self._sync

    async def open_subscription(self, execution_id: str) -> _RedisSubscription:
        import redis.asyncio as aredis  # lazy
        client = aredis.from_url(self.url)
        pubsub = client.pubsub()
        await pubsub.subscribe(_channel(execution_id))
        return _RedisSubscription(client, pubsub, _channel(execution_id))

    def publish(self, execution_id: str, evt: dict) -> None:
        self.sync.publish(_channel(execution_id), json.dumps(evt))

    def close(self, execution_id: str) -> None:
        self.sync.publish(_channel(execution_id), json.dumps({"__sentinel__": True}))


def make_transport(cfg: dict, loop: asyncio.AbstractEventLoop):
    import os
    ev = cfg.get("events", {}) or {}
    backend = str(ev.get("backend", "memory")).lower()
    url = os.environ.get("ASCORE_REDIS_URL") or ev.get("redis_url", "")
    if backend == "redis" and url:
        return RedisTransport(url)
    return InMemoryTransport(loop)


# -- the bus ----------------------------------------------------------------

class EventBus:
    def __init__(self, store: UIStore, loop: asyncio.AbstractEventLoop | None = None,
                 transport=None):
        self.store = store
        self.loop = loop or asyncio.get_running_loop()
        self.transport = transport or InMemoryTransport(self.loop)
        self._seq: dict[str, int] = {}
        self._lock = threading.Lock()

    def open(self, execution_id: str) -> None:
        """No-op kept for API compatibility — liveness comes from the persisted
        execution status, so no pre-registration is needed."""

    def publish(self, etype: str, execution_id: str,
                node_id: str | None = None, data: dict | None = None) -> None:
        """THREAD-SAFE. Assigns seq, persists, fans out via the transport."""
        data = data or {}
        with self._lock:
            if execution_id not in self._seq:
                self._seq[execution_id] = self.store.last_seq(execution_id)
            seq = self._seq[execution_id] + 1
            self._seq[execution_id] = seq
        self.store.append_event(execution_id, seq, etype, node_id, data)
        self.transport.publish(execution_id, {
            "seq": seq, "type": etype, "node_id": node_id, "data": data})

    def close(self, execution_id: str) -> None:
        with self._lock:
            self._seq.pop(execution_id, None)
        self.transport.close(execution_id)

    async def subscribe(self, execution_id: str,
                        after: int = 0) -> AsyncIterator[dict]:
        """Replay persisted events with seq > after, then tail live events via
        the transport. Ends when the execution is already terminal (replay only)
        or when the transport signals end-of-stream."""
        sub = await self.transport.open_subscription(execution_id)
        try:
            last = after
            for evt in self.store.events_after(execution_id, after):
                last = evt["seq"]
                yield {"seq": evt["seq"], "type": evt["type"],
                       "node_id": evt["node_id"], "data": evt["data"]}
            try:
                status = self.store.get_execution(execution_id)["status"]
            except Exception:  # noqa: BLE001
                status = None
            if status in _TERMINAL:
                return  # finished: replay only
            async for evt in sub.listen():
                if evt["seq"] > last:
                    last = evt["seq"]
                    yield evt
        finally:
            await sub.close()
