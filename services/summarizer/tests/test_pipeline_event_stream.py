"""Tests for the Redis Streams pipeline event broker.

The broker fronts a fake aioredis client so the tests don't need a real
Redis server (``tests/replay/test_replay_broker.py`` runs the same code on a
real one). We verify the contract that matters in production:

1. ``acquire_lock`` is single-winner under concurrent calls.
2. ``release_lock`` only deletes the lock if we still own it.
3. ``subscribe`` replays history from id="0" (so a late joiner sees the
   same events the first connection saw).
4. The DONE sentinel terminates ``subscribe`` cleanly.
5. If the producer disappears without DONE (lock TTL expired or crash),
   ``subscribe`` exits instead of hanging forever.
6. A won lock claim starts the run from an empty stream (2.1).
7. The stream carries a TTL from its first event on; ``mark_done`` shortens
   it to the drain window (2.1).
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from src.config import settings
from src.services.cache.pipeline_event_stream import (
    _ACQUIRE_LOCK_LUA,
    _REFRESH_LOCK_LUA,
    _RELEASE_LOCK_LUA,
    SENTINEL_DONE,
    PipelineEventStream,
)

_NO_KEY = -2
_NO_EXPIRY = -1


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


class FakePipeline:
    """``pipeline(transaction=True)``: queue commands, run them in order on execute."""

    def __init__(self, fake_redis: "FakeRedis") -> None:
        self._fake = fake_redis
        self._queued: list[tuple[str, tuple, dict]] = []

    async def __aenter__(self) -> "FakePipeline":
        return self

    async def __aexit__(self, *_exc: object) -> None:
        self._queued.clear()

    def xadd(self, *args: Any, **kwargs: Any) -> "FakePipeline":
        self._queued.append(("xadd", args, kwargs))
        return self

    def expire(self, *args: Any, **kwargs: Any) -> "FakePipeline":
        self._queued.append(("expire", args, kwargs))
        return self

    async def execute(self) -> list:
        return [await getattr(self._fake, name)(*a, **kw) for name, a, kw in self._queued]


class FakeRedis:
    """Minimal in-memory Redis replacement covering the surface the broker uses."""

    def __init__(self) -> None:
        self.kv: dict[str, str] = {}
        self.streams: dict[str, list[tuple[str, dict[str, str]]]] = {}
        self.ttls: dict[str, int] = {}
        self._stream_seq = 0

    def register_script(self, source: str) -> FakeScript:
        # SYNC method (matches redis-py); the returned Script's __call__ is async.
        return FakeScript(source, self)

    def pipeline(self, transaction: bool = True) -> FakePipeline:
        return FakePipeline(self)

    def _exists(self, key: str) -> bool:
        return key in self.kv or key in self.streams

    def _acquire(self, lock: str, stream: str, owner: str, ttl: str) -> int:
        if lock in self.kv:
            return 0
        self.kv[lock] = owner
        self.ttls[lock] = int(ttl)
        self.streams.pop(stream, None)
        self.ttls.pop(stream, None)
        return 1

    def _release(self, lock: str, owner: str) -> int:
        if self.kv.get(lock) != owner:
            return 0
        self.kv.pop(lock, None)
        self.ttls.pop(lock, None)
        return 1

    def _refresh(self, lock: str, stream: str, owner: str, ttl: str) -> int:
        if self.kv.get(lock) != owner:
            return 0
        if stream in self.streams:
            self.ttls[stream] = int(ttl)
        self.ttls[lock] = int(ttl)
        return 1

    async def execute_command(self, *args):
        # Args layout: EVAL script numkeys key1 ... keyN arg1 ... argN
        if args[0] != "EVAL":
            raise NotImplementedError(args[0])
        script, numkeys = args[1], int(args[2])
        keys, extra = list(args[3 : 3 + numkeys]), list(args[3 + numkeys :])
        if script == _ACQUIRE_LOCK_LUA:
            return self._acquire(keys[0], keys[1], extra[0], extra[1])
        if script == _RELEASE_LOCK_LUA:
            return self._release(keys[0], extra[0])
        if script == _REFRESH_LOCK_LUA:
            return self._refresh(keys[0], keys[1], extra[0], extra[1])
        raise NotImplementedError("unknown script")

    async def exists(self, key: str) -> int:
        return 1 if self._exists(key) else 0

    async def ttl(self, key: str) -> int:
        if not self._exists(key):
            return _NO_KEY
        return self.ttls.get(key, _NO_EXPIRY)

    async def delete(self, *keys: str) -> int:
        removed = 0
        for key in keys:
            removed += int(self.kv.pop(key, None) is not None)
            removed += int(self.streams.pop(key, None) is not None)
            self.ttls.pop(key, None)
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

    async def expire(self, key: str, seconds: int) -> bool:
        if not self._exists(key):
            return False
        self.ttls[key] = int(seconds)
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
            new_messages = [m for m in messages if _id_key(m[0]) > _id_key(last_id)]
            if new_messages:
                result.append((key, new_messages[:count]))
        if result:
            return result
        # No messages yet — yield control briefly so producers can write.
        await asyncio.sleep(min(block / 1000, 0.05) if block else 0)
        return []

    async def close(self) -> None:
        pass


def _id_key(msg_id: str) -> tuple[int, int]:
    """Stream ids compare numerically, part by part ("10-0" > "9-0")."""
    ms, _, seq = msg_id.partition("-")
    return int(ms), int(seq or 0)


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


async def _replay(broker: PipelineEventStream, video_id: str) -> list[str]:
    """What a late joiner receives (the stream must be marked done)."""
    return [event async for event in broker.subscribe(video_id, block_ms=50)]


class TestRunReset:
    """A won lock claim starts the run from an empty stream (pipeline-1min 2.1)."""

    async def test_should_drop_the_previous_runs_events_when_a_new_run_claims(self, broker):
        await broker.acquire_lock("vid-1", "run-1")
        await broker.publish("vid-1", "run-1 event")
        await broker.mark_done("vid-1")
        await broker.release_lock("vid-1", "run-1")

        await broker.acquire_lock("vid-1", "run-2")

        assert await broker._client.exists(broker.stream_key("vid-1")) == 0

    async def test_should_replay_only_the_new_run_to_a_late_joiner(self, broker):
        await broker.acquire_lock("vid-1", "run-1")
        await broker.publish("vid-1", "run-1 event")
        await broker.mark_done("vid-1")
        await broker.release_lock("vid-1", "run-1")
        await broker.acquire_lock("vid-1", "run-2")
        await broker.publish("vid-1", "run-2 event")
        await broker.mark_done("vid-1")

        assert await _replay(broker, "vid-1") == ["run-2 event"]

    async def test_should_keep_the_running_stream_when_a_claim_loses(self, broker):
        await broker.acquire_lock("vid-1", "owner-A")
        await broker.publish("vid-1", "live event")

        await broker.acquire_lock("vid-1", "owner-B")
        await broker.mark_done("vid-1")

        assert await _replay(broker, "vid-1") == ["live event"]


class TestStreamTTL:
    """The stream never exists without a TTL (pipeline-1min 2.1, A12)."""

    async def test_should_set_the_running_ttl_with_the_first_event(self, broker):
        await broker.acquire_lock("vid-1", "owner-A")

        await broker.publish("vid-1", "first")

        ttl = await broker._client.ttl(broker.stream_key("vid-1"))
        assert ttl == settings.PIPELINE_LOCK_TTL_SECONDS

    async def test_should_leave_a_crashed_runs_stream_expiring(self, broker):
        await broker.acquire_lock("vid-1", "owner-A")
        await broker.publish("vid-1", "last event before the crash")
        await broker.release_lock("vid-1", "owner-A")  # the lock's own TTL in production

        assert await broker._client.ttl(broker.stream_key("vid-1")) > 0

    async def test_should_shorten_the_ttl_to_the_drain_window_when_done(self, broker):
        await broker.acquire_lock("vid-1", "owner-A")
        await broker.publish("vid-1", "event")

        await broker.mark_done("vid-1")

        ttl = await broker._client.ttl(broker.stream_key("vid-1"))
        assert ttl == settings.PIPELINE_STREAM_TTL_SECONDS

    async def test_should_renew_the_stream_ttl_on_the_owners_heartbeat(self, broker):
        await broker.acquire_lock("vid-1", "owner-A")
        await broker.publish("vid-1", "event")
        broker._client.ttls[broker.stream_key("vid-1")] = 5

        await broker.refresh_lock("vid-1", "owner-A")

        ttl = await broker._client.ttl(broker.stream_key("vid-1"))
        assert ttl == settings.PIPELINE_LOCK_TTL_SECONDS

    async def test_should_not_renew_the_stream_ttl_for_another_owner(self, broker):
        await broker.acquire_lock("vid-1", "owner-A")
        await broker.publish("vid-1", "event")
        broker._client.ttls[broker.stream_key("vid-1")] = 5

        await broker.refresh_lock("vid-1", "owner-B")

        assert await broker._client.ttl(broker.stream_key("vid-1")) == 5

    async def test_should_replay_a_crashed_runs_events_when_the_lock_is_gone(self, broker):
        """No sentinel, no lock: a reconnect still gets what the run streamed, then exits."""
        await broker.acquire_lock("vid-1", "owner-A")
        await broker.publish("vid-1", "e1")
        await broker.release_lock("vid-1", "owner-A")

        assert await _replay(broker, "vid-1") == ["e1"]
