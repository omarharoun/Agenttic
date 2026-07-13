"""Pluggable event transport: in-memory delivery + the make_transport factory.
The Redis path is selected by config (exercised against a live Redis in CI via
ASCORE_TEST_REDIS); here we cover selection + the in-memory transport."""

import asyncio
import os

import pytest

from agenttic.server.events import (
    InMemoryTransport, RedisTransport, make_transport)


def test_in_memory_publish_subscribe():
    async def main():
        loop = asyncio.get_running_loop()
        t = InMemoryTransport(loop)
        sub = await t.open_subscription("e1")
        got = []

        async def reader():
            async for evt in sub.listen():
                got.append(evt)

        task = asyncio.create_task(reader())
        await asyncio.sleep(0)  # let reader attach
        t.publish("e1", {"seq": 1, "type": "node_started"})
        t.publish("e1", {"seq": 2, "type": "node_completed"})
        await asyncio.sleep(0.05)
        t.close("e1")  # sentinel ends the reader
        await asyncio.wait_for(task, timeout=1)
        assert [e["seq"] for e in got] == [1, 2]

    asyncio.run(main())


def test_other_execution_isolated():
    async def main():
        loop = asyncio.get_running_loop()
        t = InMemoryTransport(loop)
        sub = await t.open_subscription("e1")
        t.publish("e2", {"seq": 1})       # different execution
        t.close("e1")
        got = [e async for e in sub.listen()]
        assert got == []
    asyncio.run(main())


class TestFactory:
    def test_defaults_to_memory(self):
        async def main():
            return make_transport({}, asyncio.get_running_loop())
        assert isinstance(asyncio.run(main()), InMemoryTransport)

    def test_redis_when_configured(self):
        async def main():
            return make_transport(
                {"events": {"backend": "redis", "redis_url": "redis://x:6379/0"}},
                asyncio.get_running_loop())
        assert isinstance(asyncio.run(main()), RedisTransport)

    def test_redis_without_url_falls_back_to_memory(self, monkeypatch):
        monkeypatch.delenv("ASCORE_REDIS_URL", raising=False)
        async def main():
            return make_transport(
                {"events": {"backend": "redis"}}, asyncio.get_running_loop())
        assert isinstance(asyncio.run(main()), InMemoryTransport)


@pytest.mark.skipif(not os.environ.get("ASCORE_TEST_REDIS"),
                    reason="set ASCORE_TEST_REDIS to run against live Redis")
def test_redis_roundtrip_live():
    async def main():
        t = RedisTransport(os.environ["ASCORE_TEST_REDIS"])
        sub = await t.open_subscription("live1")
        got = []

        async def reader():
            async for evt in sub.listen():
                got.append(evt)
        task = asyncio.create_task(reader())
        await asyncio.sleep(0.1)
        t.publish("live1", {"seq": 1, "type": "x"})
        await asyncio.sleep(0.2)
        t.close("live1")
        await asyncio.wait_for(task, timeout=2)
        assert got and got[0]["seq"] == 1
    asyncio.run(main())
