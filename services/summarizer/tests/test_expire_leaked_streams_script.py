"""scripts/expire_leaked_pipeline_streams.py — the one-off sweep for no-TTL streams (2.1, A12).

A leaked stream = no TTL and no producer lock (a held lock is a live run on
pre-2.1 code). Dry run by default; ``--apply`` sets a TTL, never deletes, in
one Lua step per stream that re-checks both conditions (the fake mirrors it;
``TestApplyScriptOnRedis`` runs the real script on scratch db 15).
"""

from __future__ import annotations

import importlib.util
import sys
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import pytest
import redis
from bson import ObjectId

from src.config import settings
from src.services.cache.pipeline_event_stream import SENTINEL_DONE, PipelineEventStream

_SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "expire_leaked_pipeline_streams.py"
_NO_EXPIRY = -1
_SCRATCH_DB = 15


@pytest.fixture(scope="module")
def sweep() -> ModuleType:
    spec = importlib.util.spec_from_file_location("expire_leaked_streams_under_test", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class FakeRedis:
    """The sync surface the sweep reads and writes (decode_responses=True)."""

    def __init__(self) -> None:
        self.streams: dict[str, list[tuple[str, dict[str, str]]]] = {}
        self.locks: set[str] = set()
        self.ttls: dict[str, int] = {}

    def add_stream(self, video_id: str, *data: str, ttl: int | None = None) -> str:
        key = PipelineEventStream.stream_key(video_id)
        self.streams[key] = [(f"1760262000000-{i}", {"data": d}) for i, d in enumerate(data)]
        if ttl is not None:
            self.ttls[key] = ttl
        return key

    def scan_iter(self, match: str, count: int) -> list[str]:
        prefix = match.removesuffix("*")
        return [key for key in self.streams if key.startswith(prefix)]

    def ttl(self, name: str) -> int:
        return self.ttls.get(name, _NO_EXPIRY)

    def exists(self, *names: str) -> int:
        return sum(1 for name in names if name in self.locks or name in self.streams)

    def xlen(self, name: str) -> int:
        return len(self.streams[name])

    def xrevrange(self, name: str, count: int) -> list[Any]:
        return list(reversed(self.streams[name]))[:count]

    def eval(self, script: str, numkeys: int, *keys_and_args: Any) -> int:
        """The sweep's one script: TTL only while the stream has none and no lock exists."""
        (stream, lock), (ttl,) = keys_and_args[:numkeys], keys_and_args[numkeys:]
        if stream not in self.streams or stream in self.ttls or lock in self.locks:
            return 0
        self.ttls[stream] = int(ttl)
        return 1


def test_should_list_a_stream_without_ttl_and_without_a_producer(sweep: ModuleType) -> None:
    client = FakeRedis()
    key = client.add_stream("vid-1", "e1", SENTINEL_DONE)

    leaked = sweep.find_leaked(client)

    assert [(s.key, s.entries, s.ended) for s in leaked] == [(key, 2, True)]


def test_should_skip_a_stream_that_has_a_ttl(sweep: ModuleType) -> None:
    client = FakeRedis()
    client.add_stream("vid-1", "e1", ttl=120)

    assert sweep.find_leaked(client) == []


def test_should_skip_a_live_run_whose_producer_holds_the_lock(sweep: ModuleType) -> None:
    client = FakeRedis()
    client.add_stream("vid-1", "e1")
    client.locks.add(PipelineEventStream.lock_key("vid-1"))

    assert sweep.find_leaked(client) == []


def test_should_set_the_ttl_on_every_leaked_stream_when_applied(sweep: ModuleType) -> None:
    client = FakeRedis()
    keys = [client.add_stream(v, "e1") for v in ("vid-1", "vid-2")]

    changed = sweep.expire_leaked(client, sweep.find_leaked(client), 120)

    assert (changed, [client.ttl(k) for k in keys]) == (2, [120, 120])


def test_should_keep_a_ttl_set_between_the_scan_and_the_expire(sweep: ModuleType) -> None:
    client = FakeRedis()
    key = client.add_stream("vid-1", "e1")
    leaked = sweep.find_leaked(client)
    client.ttls[key] = 600

    sweep.expire_leaked(client, leaked, 120)

    assert client.ttl(key) == 600


def test_should_leave_a_run_that_claimed_the_stream_after_the_scan(sweep: ModuleType) -> None:
    """R4-7: a pre-2.1 producer claiming between the scan and the apply keeps its live stream."""
    client = FakeRedis()
    key = client.add_stream("vid-1", "e1")
    leaked = sweep.find_leaked(client)
    client.locks.add(PipelineEventStream.lock_key("vid-1"))

    changed = sweep.expire_leaked(client, leaked, 120)

    assert (changed, client.ttl(key)) == (0, _NO_EXPIRY)


def test_should_change_nothing_on_a_dry_run(sweep: ModuleType, monkeypatch, capsys) -> None:
    client = FakeRedis()
    key = client.add_stream("vid-1", "e1")
    monkeypatch.setattr(sweep.redis.Redis, "from_url", lambda *_a, **_k: client)

    exit_code = sweep.main([])

    assert (exit_code, client.ttl(key), "dry run" in capsys.readouterr().out) == (
        0,
        _NO_EXPIRY,
        True,
    )


def test_should_apply_the_ttl_with_the_apply_flag(sweep: ModuleType, monkeypatch) -> None:
    client = FakeRedis()
    key = client.add_stream("vid-1", "e1")
    monkeypatch.setattr(sweep.redis.Redis, "from_url", lambda *_a, **_k: client)

    sweep.main(["--apply", "--ttl", "300"])

    assert client.ttl(key) == 300


def test_should_reject_a_non_positive_ttl(sweep: ModuleType) -> None:
    assert sweep.main(["--apply", "--ttl", "0"]) == 2


def test_should_match_the_brokers_keys_and_sentinel(sweep: ModuleType) -> None:
    """The script is self-contained (it runs from stdin in the prod container)."""
    assert (
        PipelineEventStream.stream_key("x"),
        PipelineEventStream.lock_key("x"),
        SENTINEL_DONE,
    ) == (f"{sweep.STREAM_PREFIX}x", f"{sweep.LOCK_PREFIX}x", sweep.SENTINEL_DONE)


# ─── The apply script on a real Redis (scratch db 15) ───


def _scratch_url() -> str:
    parts = urlsplit(settings.REDIS_URL)
    return urlunsplit((parts.scheme, parts.netloc, f"/{_SCRATCH_DB}", "", ""))


@pytest.fixture
def scratch_redis() -> Iterator[tuple[redis.Redis, str]]:
    """A real client on scratch db 15 and a fresh video id; its keys are deleted afterwards."""
    client = redis.Redis.from_url(_scratch_url(), decode_responses=True, socket_timeout=2)
    try:
        client.ping()
    except (OSError, redis.RedisError) as exc:
        client.close()
        pytest.skip(f"no Redis answers on the configured REDIS_URL: {type(exc).__name__}")
    video_id = str(ObjectId())
    try:
        yield client, video_id
    finally:
        client.delete(
            PipelineEventStream.stream_key(video_id), PipelineEventStream.lock_key(video_id)
        )
        client.close()


class TestApplyScriptOnRedis:
    def test_should_set_the_ttl_on_a_stream_still_leaked(
        self, sweep: ModuleType, scratch_redis: tuple[redis.Redis, str]
    ) -> None:
        client, video_id = scratch_redis
        key = PipelineEventStream.stream_key(video_id)
        client.xadd(key, {"data": "e1"})
        leaked = [s for s in sweep.find_leaked(client) if s.key == key]

        changed = sweep.expire_leaked(client, leaked, 120)

        assert (changed, 0 < int(client.ttl(key)) <= 120) == (1, True)

    def test_should_leave_a_stream_whose_lock_was_claimed_after_the_scan(
        self, sweep: ModuleType, scratch_redis: tuple[redis.Redis, str]
    ) -> None:
        client, video_id = scratch_redis
        key = PipelineEventStream.stream_key(video_id)
        client.xadd(key, {"data": "e1"})
        leaked = [s for s in sweep.find_leaked(client) if s.key == key]
        client.set(PipelineEventStream.lock_key(video_id), "pre-2.1-producer", ex=60)

        changed = sweep.expire_leaked(client, leaked, 120)

        assert (changed, int(client.ttl(key))) == (0, _NO_EXPIRY)
