"""The real broker on a scratch id (pipeline-1min 2.1, gate 2: no replay duplicates).

A replayed run's SSE stream goes through ``produce_to_broker`` into a real
Redis — lock claim, per-event publish, TTL, ``mark_done``, lock release — and
comes back through ``subscribe``, the way every SSE client reads it. Checked:
no duplicates, order, final flags and the provisional→final replacement as a
subscriber receives them; a re-run (or a crashed run) on the same id never
replays the previous run's events; the stream has a TTL from its first event.

Runs against ``settings.REDIS_URL`` on scratch db 15 and purges its keys;
skipped when no Redis answers (CI's summarizer job runs a Redis service).
"""

from __future__ import annotations

import asyncio
import json
from collections import Counter
from collections.abc import AsyncGenerator, AsyncIterator, Callable
from typing import Any
from unittest.mock import MagicMock
from urllib.parse import urlsplit, urlunsplit

import pytest
import redis.asyncio as aioredis
from bson import ObjectId

from src.config import settings
from src.routes import pipeline_broker
from src.services.cache.pipeline_event_stream import PipelineEventStream
from tests.replay.driver import ReplayResult

_SCRATCH_DB = 15
_RUN = "T1dQhQAm8Tc"
_PREVIOUS_RUN = "uC45_4nnEAI"
_OWNER = "replay-broker-test"
_DONE_CHUNK = "data: [DONE]\n\n"
_BLOCK_MS = 200


def _scratch_url() -> str:
    parts = urlsplit(settings.REDIS_URL)
    return urlunsplit((parts.scheme, parts.netloc, f"/{_SCRATCH_DB}", "", ""))


@pytest.fixture
async def broker(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[tuple[PipelineEventStream, str]]:
    """A real broker on scratch db 15 with a fresh scratch id; purged afterwards."""
    stream = PipelineEventStream(redis_url=_scratch_url())
    try:
        await asyncio.wait_for(stream._get_client().ping(), timeout=2)
    except (OSError, aioredis.RedisError, TimeoutError) as exc:
        await stream.close()
        pytest.skip(f"no Redis answers on the configured REDIS_URL: {type(exc).__name__}")
    monkeypatch.setattr(pipeline_broker, "pipeline_event_stream", stream)
    scratch_id = str(ObjectId())
    try:
        yield stream, scratch_id
    finally:
        await stream.purge(scratch_id)
        await stream.close()


def _replaying(
    chunks: list[str], after_first: Callable[[], Any] | None = None
) -> Callable[..., AsyncGenerator[str, None]]:
    """A ``stream_summarization`` stand-in that yields a recorded run's chunks."""

    async def stream_summarization(*_args: Any, **_kwargs: Any) -> AsyncGenerator[str, None]:
        for index, chunk in enumerate(chunks):
            if index == 1 and after_first is not None:
                await after_first()
            yield chunk

    return stream_summarization


async def _run_claimed(
    monkeypatch: pytest.MonkeyPatch,
    scratch_id: str,
    stream_summarization: Callable[..., AsyncGenerator[str, None]],
) -> None:
    """The producer of a run whose lock ``_OWNER`` already claimed."""
    monkeypatch.setattr(pipeline_broker, "stream_summarization", stream_summarization)
    repository = MagicMock()
    repository.get_video_summary.return_value = {"youtubeId": "scratch"}
    await pipeline_broker.produce_to_broker(scratch_id, {}, repository, MagicMock(), _OWNER)


async def _produce(
    monkeypatch: pytest.MonkeyPatch,
    stream: PipelineEventStream,
    scratch_id: str,
    stream_summarization: Callable[..., AsyncGenerator[str, None]],
) -> None:
    """One run the way stream.py / the worker drive it: claim, then produce."""
    assert await stream.acquire_lock(scratch_id, _OWNER)
    await _run_claimed(monkeypatch, scratch_id, stream_summarization)


async def _subscribe(stream: PipelineEventStream, scratch_id: str) -> list[str]:
    return [chunk async for chunk in stream.subscribe(scratch_id, block_ms=_BLOCK_MS)]


def _tab_events(chunks: list[str]) -> list[dict[str, Any]]:
    payloads = [json.loads(c.removeprefix("data: ")) for c in chunks if c != _DONE_CHUNK]
    return [p for p in payloads if p.get("event") == "tab_ready"]


def _saved_ids(result: ReplayResult) -> list[str]:
    return [t["id"] for t in (result.saved_result or {}).get("tabs", [])]


async def test_should_deliver_the_run_once_in_order_to_live_and_late_subscribers(
    broker: tuple[PipelineEventStream, str], replays: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    stream, scratch_id = broker
    chunks = replays[_RUN].chunks()
    assert await stream.acquire_lock(scratch_id, _OWNER)
    live = asyncio.create_task(_subscribe(stream, scratch_id))  # attaches like stream.py

    await _run_claimed(monkeypatch, scratch_id, _replaying(chunks))

    assert (await live, await _subscribe(stream, scratch_id)) == (chunks, chunks)


async def test_should_replay_the_run_from_its_start_to_a_mid_run_reconnect(
    broker: tuple[PipelineEventStream, str], replays: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    stream, scratch_id = broker
    chunks = replays[_RUN].chunks()
    reconnects: list[asyncio.Task[list[str]]] = []

    async def _reconnect() -> None:
        reconnects.append(asyncio.create_task(_subscribe(stream, scratch_id)))

    await _produce(monkeypatch, stream, scratch_id, _replaying(chunks, _reconnect))

    assert await reconnects[0] == chunks


async def test_should_hand_a_subscriber_one_final_emission_per_saved_tab(
    broker: tuple[PipelineEventStream, str], replays: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    stream, scratch_id = broker
    await _produce(monkeypatch, stream, scratch_id, _replaying(replays[_RUN].chunks()))

    finals = Counter(
        t["id"] for t in _tab_events(await _subscribe(stream, scratch_id)) if t["final"]
    )

    assert finals == Counter(_saved_ids(replays[_RUN]))


async def test_should_hand_a_subscriber_the_provisional_tab_then_its_final(
    broker: tuple[PipelineEventStream, str], replays: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    stream, scratch_id = broker
    await _produce(monkeypatch, stream, scratch_id, _replaying(replays[_RUN].chunks()))

    tabs = _tab_events(await _subscribe(stream, scratch_id))

    moments = [(t.get("provisional", False), t["final"]) for t in tabs if t["id"] == tabs[0]["id"]]
    assert (moments[0], moments[-1]) == ((True, False), (False, True))


async def test_should_replay_only_the_new_run_after_a_re_run_on_the_same_id(
    broker: tuple[PipelineEventStream, str], replays: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    stream, scratch_id = broker
    await _produce(monkeypatch, stream, scratch_id, _replaying(replays[_PREVIOUS_RUN].chunks()))

    await _produce(monkeypatch, stream, scratch_id, _replaying(replays[_RUN].chunks()))

    assert await _subscribe(stream, scratch_id) == replays[_RUN].chunks()


async def test_should_replay_only_the_new_run_after_a_crashed_run_on_the_same_id(
    broker: tuple[PipelineEventStream, str], replays: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    stream, scratch_id = broker
    assert await stream.acquire_lock(scratch_id, "crashed-producer")
    for chunk in replays[_PREVIOUS_RUN].chunks()[:5]:
        await stream.publish(scratch_id, chunk)
    await stream.release_lock(scratch_id, "crashed-producer")  # its TTL expiry in production

    await _produce(monkeypatch, stream, scratch_id, _replaying(replays[_RUN].chunks()))

    assert await _subscribe(stream, scratch_id) == replays[_RUN].chunks()


async def test_should_leave_a_crashed_runs_stream_expiring(
    broker: tuple[PipelineEventStream, str], replays: dict
) -> None:
    stream, scratch_id = broker
    assert await stream.acquire_lock(scratch_id, "crashed-producer")
    await stream.publish(scratch_id, replays[_RUN].chunks()[0])
    await stream.release_lock(scratch_id, "crashed-producer")

    assert await stream._get_client().ttl(stream.stream_key(scratch_id)) > 0


async def test_should_set_the_streams_ttl_with_the_first_event(
    broker: tuple[PipelineEventStream, str], replays: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    stream, scratch_id = broker
    seen: list[int] = []

    async def _read_ttl() -> None:
        seen.append(await stream._get_client().ttl(stream.stream_key(scratch_id)))

    await _produce(monkeypatch, stream, scratch_id, _replaying(replays[_RUN].chunks(), _read_ttl))

    assert 0 < seen[0] <= settings.PIPELINE_LOCK_TTL_SECONDS


async def test_should_shorten_the_ttl_to_the_drain_window_when_the_run_ends(
    broker: tuple[PipelineEventStream, str], replays: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    stream, scratch_id = broker

    await _produce(monkeypatch, stream, scratch_id, _replaying(replays[_RUN].chunks()))

    ttl = await stream._get_client().ttl(stream.stream_key(scratch_id))
    assert 0 < ttl <= settings.PIPELINE_STREAM_TTL_SECONDS
