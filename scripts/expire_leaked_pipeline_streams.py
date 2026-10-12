#!/usr/bin/env python3
"""Give leaked pipeline event streams a TTL — one-off (pipeline-1min 2.1, A12).

Before 2.1 a run's Redis stream (``vie:pipeline:events:<videoSummaryId>``) got
its TTL only from ``mark_done``, so a producer that died mid-run left a stream
without one, forever (five in dev Redis on 2026-10-06). Since 2.1 every event
renews a TTL; this sweeps what leaked before. A stream counts as leaked when
it has no TTL and no producer holds its lock (``vie:pipeline:lock:<id>``) — a
held lock is a live run on pre-2.1 code and is left alone. Nothing is deleted:
the script sets a TTL (default 120 s, the drain window ``mark_done`` uses) and
Redis expires the stream.

Dry run by default — prints each leaked stream (entries, time of its last
event, whether it ended with the done sentinel) and changes nothing.
``--apply`` sets the TTL (``EXPIRE … NX``: a TTL set meanwhile is kept).

Dev (dev Redis on localhost:6379)::

    services/summarizer/.venv/bin/python scripts/expire_leaked_pipeline_streams.py
    services/summarizer/.venv/bin/python scripts/expire_leaked_pipeline_streams.py --apply

Prod (the summarizer container has redis-py and REDIS_URL; run from the repo root —
``docker exec`` by container name, so no compose ``-f`` files are needed)::

    docker exec -i vie-summarizer python - < scripts/expire_leaked_pipeline_streams.py
    docker exec -i vie-summarizer python - --apply < scripts/expire_leaked_pipeline_streams.py
"""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

import redis

# Mirrors PipelineEventStream.stream_key / lock_key and SENTINEL_DONE
# (services/summarizer/src/services/cache/pipeline_event_stream.py).
STREAM_PREFIX = "vie:pipeline:events:"
LOCK_PREFIX = "vie:pipeline:lock:"
SENTINEL_DONE = "__pipeline_done__"
DEFAULT_TTL_SECONDS = 120  # PIPELINE_STREAM_TTL_SECONDS
NO_EXPIRY = -1  # TTL reply for a key that has none


class StreamClient(Protocol):
    """The sync redis-py surface the sweep uses (``decode_responses=True``)."""

    def scan_iter(self, match: str, count: int) -> Any: ...
    def ttl(self, name: str) -> Any: ...
    def exists(self, *names: str) -> Any: ...
    def xlen(self, name: str) -> Any: ...
    def xrevrange(self, name: str, count: int) -> Any: ...
    def expire(self, name: str, time: int, nx: bool) -> Any: ...


@dataclass(frozen=True)
class LeakedStream:
    key: str
    entries: int
    last_event_at: str  # ISO time of the last entry, "" for an empty stream
    ended: bool  # the last entry is the done sentinel


def _last_entry(client: StreamClient, key: str) -> tuple[str, bool]:
    """(time of the last entry, whether it is the done sentinel)."""
    last = client.xrevrange(key, count=1)
    if not last:
        return "", False
    entry_id, fields = last[0]
    millis = int(str(entry_id).split("-", 1)[0])
    at = datetime.fromtimestamp(millis / 1000, tz=UTC).isoformat(timespec="seconds")
    return at, fields.get("data") == SENTINEL_DONE


def find_leaked(client: StreamClient) -> list[LeakedStream]:
    """Streams with no TTL whose run no producer holds, sorted by key."""
    leaked: list[LeakedStream] = []
    for key in client.scan_iter(match=f"{STREAM_PREFIX}*", count=500):
        if client.ttl(key) != NO_EXPIRY:
            continue
        if client.exists(LOCK_PREFIX + key.removeprefix(STREAM_PREFIX)):
            continue
        last_event_at, ended = _last_entry(client, key)
        leaked.append(LeakedStream(key, int(client.xlen(key)), last_event_at, ended))
    return sorted(leaked, key=lambda stream: stream.key)


def expire_leaked(client: StreamClient, leaked: list[LeakedStream], ttl: int) -> int:
    """Set ``ttl`` on each leaked stream that still has none; how many were set."""
    return sum(1 for stream in leaked if client.expire(stream.key, ttl, nx=True))


def describe(stream: LeakedStream) -> str:
    return (
        f"{stream.key}  entries={stream.entries}  last={stream.last_event_at or '-'}  "
        f"ended={'yes' if stream.ended else 'no'}"
    )


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--apply", action="store_true", help="set the TTL (default: dry run)")
    parser.add_argument("--ttl", type=int, default=DEFAULT_TTL_SECONDS, help="seconds")
    parser.add_argument(
        "--redis-url",
        default=os.environ.get("REDIS_URL", "redis://localhost:6379"),
        help="default: $REDIS_URL, else redis://localhost:6379",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    if args.ttl <= 0:
        print("--ttl must be positive", file=sys.stderr)
        return 2
    client = redis.Redis.from_url(args.redis_url, decode_responses=True, socket_timeout=10)
    leaked = find_leaked(client)
    for stream in leaked:
        print(describe(stream))
    if not args.apply:
        print(f"{len(leaked)} leaked stream(s) — dry run, nothing changed (--apply sets the TTL)")
        return 0
    changed = expire_leaked(client, leaked, args.ttl)
    print(f"TTL {args.ttl}s set on {changed} of {len(leaked)} leaked stream(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
