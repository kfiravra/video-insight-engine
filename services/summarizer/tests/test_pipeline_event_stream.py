"""Tests for the Redis Streams pipeline event broker.

The broker fronts a fake aioredis client so the tests don't need a real
Redis server. We verify the contract that matters in production:

1. ``acquire_lock`` is single-winner under concurrent calls.
2. ``release_lock`` only deletes the lock if we still own it.
3. ``subscribe`` replays history from id="0" (so a late joiner sees the
   same events the first connection saw).
4. The DONE sentinel terminates ``subscribe`` cleanly.
5. If the producer disappears without DONE (lock TTL expired or crash),
   ``subscribe`` exits instead of hanging forever.
"""

from __future__ import annotations

import asyncio

import pytest

from src.services.cache.pipeline_event_stream import (
    SENTINEL_DONE,
    PipelineEventStream,
)


class FakeScript:
    """Stand-in for redis-py's Script object.

    redis-py's ``register_script`` returns a Script whose ``__call__`` is
    awaitable on async clients. We forward calls to FakeRedis's existing
    EVAL handler so the script-source dispatch stays in one place.
    """

    def __init__(self, source: str, fake_redis: "FakeRedis") -> None:
        self.source = source
        self.registered_client = fake_redis  # mirrors redis-py attribute name

    async def __call__(self, *, keys: list, args: list):
        return await self.registered_client.execute_command(
            "EVAL",
            self.source,
            len(keys),
            *keys,
            *args,
        )


class FakeRedis:
    """Minimal in-memory Redis replacement covering the surface the broker uses."""

    def __init__(self) -> None:
        self.kv: dict[str, str] = {}
        self.streams: dict[str, list[tuple[str, dict[str, str]]]] = {}
        self._stream_seq = 0

    def register_script(self, source: str) -> FakeScript:
        # SYNC method (matches redis-py); the returned Script's __call__ is async.
        return FakeScript(source, self)

    async def set(
        self, key: str, value: str, *, nx: bool = False, ex: int | None = None
    ) -> bool | None:
        if nx and key in self.kv:
            return None
        self.kv[key] = value
        return True

    async def execute_command(self, *args):
        cmd = args[0]
        if cmd == "EVAL":
            # Args layout: EVAL script numkeys key1 ... keyN arg1 ... argN
            script = args[1]
            numkeys = int(args[2])
            keys = list(args[3 : 3 + numkeys])
            extra = list(args[3 + numkeys :])
            key, owner = keys[0], extra[0]
            # Distinguish scripts by their body — the broker only uses two.
            if "expire" in script:  # refresh_lock: compare-and-extend
                # Real Redis EXPIRE returns 1 on success; we don't track TTLs,
                # so "key owned by caller" stands in for "would have extended".
                return 1 if self.kv.get(key) == owner else 0
            # release_lock: compare-and-delete
            if self.kv.get(key) == owner:
                self.kv.pop(key, None)
                return 1
            return 0
        raise NotImplementedError(cmd)

    async def exists(self, key: str) -> int:
        return 1 if key in self.kv else 0

    async def delete(self, *keys: str) -> int:
        removed = 0
        for key in keys:
            removed += int(self.kv.pop(key, None) is not None)
            removed += int(self.streams.pop(key, None) is not None)
        return removed

    async def xadd(
        self,
        key: str,
        fields: dict[str, str],
        *,
        maxlen: int | None = None,
        approximate: bool = True,
    ) -> str:
        self._stream_seq += 1
        msg_id = f"{self._stream_seq}-0"
        self.streams.setdefault(key, []).append((msg_id, dict(fields)))
        if maxlen is not None and len(self.streams[key]) > maxlen:
            self.streams[key] = self.streams[key][-maxlen:]
        return msg_id

    async def expire(self, key: str, _seconds: int) -> bool:
        # No-op for tests — our retention checks are cursor-based, not TTL.
        return True

    async def xread(
        self,
        streams_to_ids: dict[str, str],
        *,
        count: int = 100,
        block: int = 0,
    ) -> list:
        result: list = []
        for key, last_id in streams_to_ids.items():
            messages = self.streams.get(key, [])
            new_messages = [m for m in messages if m[0] > last_id]
            if new_messages:
                result.append((key, new_messages[:count]))
        if result:
            return result
        # No messages yet — yield control briefly so producers can write.
        await asyncio.sleep(min(block / 1000, 0.05) if block else 0)
        return []

    async def close(self) -> None:
        pass


@pytest.fixture
def broker(monkeypatch):
    fake = FakeRedis()
    b = PipelineEventStream(redis_url="redis://fake")
    b._client = fake  # type: ignore[assignment]
    return b


class TestLockSemantics:
    """The lock guarantees one producer per video at any moment."""

    async def test_first_caller_wins_lock(self, broker):
        assert await broker.acquire_lock("vid-1", "owner-A") is True

    async def test_second_caller_loses_lock_until_release(self, broker):
        await broker.acquire_lock("vid-1", "owner-A")
        assert await broker.acquire_lock("vid-1", "owner-B") is False

    async def test_release_then_reacquire_succeeds(self, broker):
        await broker.acquire_lock("vid-1", "owner-A")
        await broker.release_lock("vid-1", "owner-A")
        assert await broker.acquire_lock("vid-1", "owner-B") is True

    async def test_release_does_not_steal_other_owners_lock(self, broker):
        """Compare-and-delete: owner-B can't release a lock held by owner-A,
        even after owner-A's lock would have TTL-expired in a real cluster."""
        await broker.acquire_lock("vid-1", "owner-A")
        await broker.release_lock("vid-1", "owner-B")  # wrong owner — no-op
        assert await broker.acquire_lock("vid-1", "owner-C") is False
        # owner-A is still the owner
        assert await broker.lock_held("vid-1") is True

    async def test_lock_held_reflects_current_state(self, broker):
        assert await broker.lock_held("vid-1") is False
        await broker.acquire_lock("vid-1", "owner-A")
        assert await broker.lock_held("vid-1") is True
        await broker.release_lock("vid-1", "owner-A")
        assert await broker.lock_held("vid-1") is False

    async def test_refresh_extends_lock_for_current_owner(self, broker):
        """Heartbeat: the owner can keep renewing the lock for as long as its
        pipeline is running — prevents TTL expiry under a long chunked run."""
        await broker.acquire_lock("vid-1", "owner-A")
        assert await broker.refresh_lock("vid-1", "owner-A") is True
        # Still ours afterwards.
        assert await broker.acquire_lock("vid-1", "owner-B") is False

    async def test_refresh_rejects_non_owner(self, broker):
        """A worker that no longer owns the lock must not be able to extend it
        — otherwise it could keep a stolen lock alive indefinitely."""
        await broker.acquire_lock("vid-1", "owner-A")
        assert await broker.refresh_lock("vid-1", "owner-B") is False

    async def test_refresh_returns_false_when_lock_missing(self, broker):
        """If the lock has already expired and been deleted, refresh is a
        clean no-op — signals to the producer that it has lost ownership."""
        assert await broker.refresh_lock("vid-1", "owner-A") is False


class TestPublishSubscribe:
    """Events flow from publish → subscribe in order, with full history replay."""

    async def test_subscriber_receives_published_events_in_order(self, broker):
        await broker.acquire_lock("vid-1", "owner-A")

        async def produce():
            for event in ("first", "second", "third"):
                await broker.publish("vid-1", event)
            await broker.mark_done("vid-1")

        async def consume():
            received = []
            async for event in broker.subscribe("vid-1", block_ms=200):
                received.append(event)
            return received

        producer = asyncio.create_task(produce())
        consumer_task = asyncio.create_task(consume())
        await asyncio.gather(producer, consumer_task)

        assert consumer_task.result() == ["first", "second", "third"]

    async def test_late_joiner_replays_full_history(self, broker):
        """Reconnect-after-StrictMode-unmount scenario: the second consumer
        starts AFTER all events are written and gets the entire history."""
        await broker.acquire_lock("vid-1", "owner-A")
        await broker.publish("vid-1", "alpha")
        await broker.publish("vid-1", "beta")
        await broker.publish("vid-1", "gamma")
        await broker.mark_done("vid-1")

        received = []
        async for event in broker.subscribe("vid-1", block_ms=200):
            received.append(event)

        assert received == ["alpha", "beta", "gamma"]

    async def test_done_sentinel_terminates_subscribe(self, broker):
        """The DONE sentinel itself is consumed but not yielded — subscribers
        see only real events and then a clean iterator close."""
        await broker.acquire_lock("vid-1", "owner-A")
        await broker.publish("vid-1", "only-event")
        await broker.mark_done("vid-1")

        received = []
        async for event in broker.subscribe("vid-1", block_ms=200):
            received.append(event)

        assert SENTINEL_DONE not in received
        assert received == ["only-event"]

    async def test_subscribe_exits_when_lock_disappears_without_done(self, broker):
        """Producer crash without a DONE write: lock evaporates (TTL expiry
        in production, manual delete in this test) and subscribers should
        exit instead of hanging on XREAD forever."""
        await broker.acquire_lock("vid-1", "owner-A")
        # Don't publish anything; release the lock to simulate crash + TTL expiry.
        await broker.release_lock("vid-1", "owner-A")

        received = []
        async for event in broker.subscribe("vid-1", block_ms=10):
            received.append(event)

        assert received == []

    async def test_two_subscribers_each_replay_independently(self, broker):
        """Two consumers attached at the same time both see every event;
        starting from id=0 means each gets its own full replay."""
        await broker.acquire_lock("vid-1", "owner-A")
        await broker.publish("vid-1", "e1")
        await broker.publish("vid-1", "e2")
        await broker.mark_done("vid-1")

        async def collect():
            out = []
            async for event in broker.subscribe("vid-1", block_ms=200):
                out.append(event)
            return out

        a, b = await asyncio.gather(collect(), collect())
        assert a == ["e1", "e2"]
        assert b == ["e1", "e2"]


class TestPurge:
    @pytest.mark.asyncio
    async def test_should_remove_lock_and_stream_when_purging_a_run(self, broker):
        assert await broker.acquire_lock("vs-1", "owner-a")
        broker._client.streams[broker.stream_key("vs-1")] = [("1-0", {"data": "{}"})]

        removed = await broker.purge("vs-1")

        assert removed == 2
        assert await broker._client.exists(broker.lock_key("vs-1")) == 0
        assert broker.stream_key("vs-1") not in broker._client.streams

    @pytest.mark.asyncio
    async def test_should_return_zero_when_nothing_exists_for_the_run(self, broker):
        assert await broker.purge("vs-missing") == 0
