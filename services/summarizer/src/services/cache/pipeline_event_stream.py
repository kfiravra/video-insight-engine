"""Redis Streams broker for pipeline SSE event fan-out.

One pipeline run per ``video_summary_id`` produces events that any number of
SSE consumers can attach to. The first request to acquire the per-video lock
becomes the producer; everyone else (StrictMode reconnects, additional tabs,
the processing-manager hook) just subscribes to the shared stream.

Key design properties:

- **Producer lifetime is independent of any single connection.** When a
  client aborts the SSE request (StrictMode unmount, page navigation, hard
  refresh), the producer task keeps running in the background.
- **Late joiners replay from the beginning.** ``XREAD`` with ``last_id="0"``
  returns the full event history retained in the stream, so a reconnect
  receives the same events the first connection saw.
- **Crash safety.** The lock has a TTL; if the worker process dies, the
  lock auto-expires and a future request can pick the work back up. The
  release path uses a Lua compare-and-delete so we never release a lock we
  no longer own.
"""

from __future__ import annotations

import logging
from typing import AsyncGenerator

import redis.asyncio as aioredis

from src.config import settings

logger = logging.getLogger(__name__)


# Sentinel value written to the stream when the producer finishes (success
# or failure). Consumers exit cleanly when they see it.
SENTINEL_DONE = "__pipeline_done__"


# Lua script for atomic compare-and-delete on the lock key.
# Prevents a slow worker from deleting a lock that has since been re-acquired
# by a different worker after TTL expiry.
_RELEASE_LOCK_LUA = """
if redis.call('get', KEYS[1]) == ARGV[1] then
    return redis.call('del', KEYS[1])
else
    return 0
end
"""

# Lua script for atomic compare-and-extend (PEXPIRE) on the lock key.
# Used by the producer's heartbeat to keep the lock alive for as long as
# its pipeline is running, without ever extending a lock owned by someone
# else (which would happen with a naive EXPIRE).
_REFRESH_LOCK_LUA = """
if redis.call('get', KEYS[1]) == ARGV[1] then
    return redis.call('expire', KEYS[1], ARGV[2])
else
    return 0
end
"""


class PipelineEventStream:
    """Redis Streams broker for per-video pipeline event fan-out."""

    def __init__(self, redis_url: str | None = None) -> None:
        self._url = redis_url or settings.REDIS_URL
        self._client: aioredis.Redis | None = None
        # Scripts are registered once on first use and cached. redis-py's
        # Script object handles EVALSHA + NOSCRIPT-fallback to EVAL internally,
        # so the wire payload after the first call is just the SHA.
        self._release_script = None
        self._refresh_script = None

    def _get_client(self) -> aioredis.Redis:
        """Lazy-initialize async Redis client. Mirrors ResponseCache pattern.

        socket_timeout must exceed the longest blocking command we issue,
        otherwise BLOCK reads (``xread block=30_000``) trip a spurious
        TimeoutError every time the producer is idle. 35s gives the 30s
        block a 5s safety margin without making dead-connection detection
        meaningfully slower for one-shot commands.
        """
        if self._client is None:
            self._client = aioredis.from_url(
                self._url,
                socket_connect_timeout=2.0,
                socket_timeout=35.0,
                socket_keepalive=True,
                decode_responses=True,
            )
        return self._client

    def _ensure_scripts(self) -> None:
        """Register the Lua scripts against the current client.

        Re-binds when the underlying client changes (e.g. after a ``close()``
        in tests) so the Script objects always target the live connection.
        """
        client = self._get_client()
        if (
            self._release_script is None
            or getattr(self._release_script, "registered_client", None) is not client
        ):
            self._release_script = client.register_script(_RELEASE_LOCK_LUA)
            self._refresh_script = client.register_script(_REFRESH_LOCK_LUA)

    @staticmethod
    def stream_key(video_summary_id: str) -> str:
        return f"vie:pipeline:events:{video_summary_id}"

    @staticmethod
    def lock_key(video_summary_id: str) -> str:
        return f"vie:pipeline:lock:{video_summary_id}"

    async def acquire_lock(self, video_summary_id: str, owner: str) -> bool:
        """Try to claim producer ownership. Returns True if we won the race."""
        client = self._get_client()
        result = await client.set(
            self.lock_key(video_summary_id),
            owner,
            nx=True,
            ex=settings.PIPELINE_LOCK_TTL_SECONDS,
        )
        return bool(result)

    async def purge(self, video_summary_id: str) -> int:
        """Delete the event stream and the producer lock for one run.

        Used by the global video purge: after the row is gone nothing may
        replay or resume its run. Returns the number of keys removed (0-2).
        """
        client = self._get_client()
        removed = await client.delete(
            self.stream_key(video_summary_id),
            self.lock_key(video_summary_id),
        )
        return int(removed)

    async def release_lock(self, video_summary_id: str, owner: str) -> None:
        """Release the lock only if we still own it (compare-and-delete)."""
        self._ensure_scripts()
        assert self._release_script is not None  # set by _ensure_scripts
        try:
            await self._release_script(
                keys=[self.lock_key(video_summary_id)],
                args=[owner],
            )
        except (aioredis.ConnectionError, aioredis.TimeoutError) as e:
            logger.debug("Lock release failed for %s: %s", video_summary_id, e)
        except aioredis.RedisError as e:
            logger.warning("Lock release unexpected error for %s: %s", video_summary_id, e)

    async def refresh_lock(self, video_summary_id: str, owner: str) -> bool:
        """Extend the lock TTL atomically, only if we still own it.

        Returns True when the lock was extended, False when it had already
        expired / been re-claimed (in which case the producer has effectively
        lost ownership and should consider stopping). Returns False on Redis
        errors so the caller can decide whether to keep heartbeating.
        """
        self._ensure_scripts()
        assert self._refresh_script is not None  # set by _ensure_scripts
        try:
            result = await self._refresh_script(
                keys=[self.lock_key(video_summary_id)],
                args=[owner, str(settings.PIPELINE_LOCK_TTL_SECONDS)],
            )
        except (aioredis.ConnectionError, aioredis.TimeoutError) as e:
            logger.debug("Lock refresh transient failure for %s: %s", video_summary_id, e)
            return False
        except aioredis.RedisError as e:
            logger.warning("Lock refresh unexpected error for %s: %s", video_summary_id, e)
            return False
        return result == 1

    async def lock_held(self, video_summary_id: str) -> bool:
        """Check whether anyone currently holds the producer lock."""
        client = self._get_client()
        try:
            return bool(await client.exists(self.lock_key(video_summary_id)))
        except (aioredis.ConnectionError, aioredis.TimeoutError) as e:
            logger.debug("Lock existence check failed for %s: %s", video_summary_id, e)
            # Fail open — assume held so consumers don't bail prematurely.
            return True

    async def publish(self, video_summary_id: str, event: str) -> None:
        """Append an SSE event chunk to the stream."""
        client = self._get_client()
        await client.xadd(
            self.stream_key(video_summary_id),
            {"data": event},
            maxlen=settings.PIPELINE_STREAM_MAXLEN,
            approximate=True,
        )

    async def mark_done(self, video_summary_id: str) -> None:
        """Append the DONE sentinel and set retention so late joiners can drain."""
        client = self._get_client()
        try:
            await client.xadd(
                self.stream_key(video_summary_id),
                {"data": SENTINEL_DONE},
            )
        except (aioredis.ConnectionError, aioredis.TimeoutError, aioredis.RedisError) as e:
            logger.warning("Failed to mark stream done for %s: %s", video_summary_id, e)
        try:
            await client.expire(
                self.stream_key(video_summary_id),
                settings.PIPELINE_STREAM_TTL_SECONDS,
            )
        except (aioredis.ConnectionError, aioredis.TimeoutError, aioredis.RedisError) as e:
            logger.debug("Failed to set stream TTL for %s: %s", video_summary_id, e)

    async def subscribe(
        self,
        video_summary_id: str,
        block_ms: int = 30_000,
    ) -> AsyncGenerator[str, None]:
        """Yield events from the stream until the DONE sentinel.

        Always starts from the beginning (``XREAD`` cursor ``"0"``) — late
        joiners replay every event the first connection received. The
        frontend stream-registry holds its own replay buffer for cross-
        component fan-out, so we never need a "resume from cursor" path.
        ``XREAD BLOCK`` waits up to ``block_ms`` for new messages; if the
        producer lock has also disappeared, we treat that as "producer died
        without DONE" and exit so the consumer doesn't hang forever.
        """
        client = self._get_client()
        key = self.stream_key(video_summary_id)
        cursor = "0"
        while True:
            try:
                result = await client.xread({key: cursor}, count=100, block=block_ms)
            except (aioredis.ConnectionError, aioredis.TimeoutError) as e:
                logger.warning("Stream read failed for %s: %s", video_summary_id, e)
                return

            if not result:
                # No new events within the block window — check whether the
                # producer is still alive. If the lock is gone and the stream
                # is empty, the producer crashed without writing DONE.
                if not await self.lock_held(video_summary_id):
                    logger.warning(
                        "Producer lock disappeared without DONE for %s — exiting consumer",
                        video_summary_id,
                    )
                    return
                continue

            for _, messages in result:
                for msg_id, fields in messages:
                    cursor = msg_id
                    data = fields.get("data") if isinstance(fields, dict) else None
                    if data == SENTINEL_DONE:
                        return
                    if data is not None:
                        yield data

    async def close(self) -> None:
        """Close the Redis connection pool."""
        if self._client is not None:
            await self._client.close()
            self._client = None


# Module-level singleton (lazy-initialized).
pipeline_event_stream = PipelineEventStream()
