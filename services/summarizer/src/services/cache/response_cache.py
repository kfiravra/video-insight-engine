"""Redis-backed response cache for VIEResponse.

Same video URL = instant serve from cache. 30-day TTL.
Gracefully degrades if Redis is unavailable — pipeline continues without cache.
"""

import json
import logging

import redis.asyncio as aioredis

from src.config import settings

logger = logging.getLogger(__name__)


class ResponseCache:
    """Cache VIEResponse by video_id in Redis (async)."""

    def __init__(self, redis_url: str | None = None) -> None:
        self._url = redis_url or settings.REDIS_URL
        self._client: aioredis.Redis | None = None

    @staticmethod
    def _key(video_id: str) -> str:
        """Build the Redis key, namespaced by PIPELINE_VERSION.

        Bumping ``settings.PIPELINE_VERSION`` shifts every key, so stale docs
        from an incompatible schema/props version become unreachable in Redis
        and TTL out. This covers the Redis response cache ONLY — persisted
        MongoDB docs are not version-keyed and still need a reprocess/flush.
        """
        return f"vie:response:{settings.PIPELINE_VERSION}:{video_id}"

    def _get_client(self) -> aioredis.Redis:
        """Lazy-initialize async Redis client."""
        if self._client is None:
            self._client = aioredis.from_url(
                self._url,
                socket_connect_timeout=2.0,
                socket_timeout=2.0,
                decode_responses=True,
            )
        return self._client

    async def get_response(self, video_id: str) -> dict | None:
        """Get cached response for a video. Returns None on miss or error."""
        try:
            data = await self._get_client().get(self._key(video_id))
            if data:
                return json.loads(data)
            return None
        except (aioredis.ConnectionError, aioredis.TimeoutError) as e:
            logger.debug("Redis get failed for %s: %s", video_id, e)
            return None
        except (aioredis.RedisError, json.JSONDecodeError) as e:
            logger.warning("Redis get unexpected error for %s: %s", video_id, e)
            return None

    async def set_response(
        self, video_id: str, response: dict, *, safe_keys: frozenset[str] | None = None
    ) -> bool:
        """Cache a response. Only stores safe_keys to prevent pipeline internals from leaking.

        Args:
            video_id: YouTube video ID for cache key.
            response: Full pipeline result dict.
            safe_keys: Allowlist of top-level keys to cache. Defaults to frontend-safe shape.

        Returns True on success.
        """
        _SAFE_KEYS = safe_keys or frozenset(
            {
                "youtubeId",
                "title",
                "creator",
                "channel",
                "duration",
                "thumbnailUrl",
                "status",
                "meta",
                "tabs",
                # Language metadata — non-English videos rely on these for the
                # FE toggle. Dropping them here silently breaks the source-language
                # view on every cache hit.
                "language",
                "isRTL",
                "sourceLanguage",
            }
        )
        try:
            filtered = {k: v for k, v in response.items() if k in _SAFE_KEYS}
            serialized = json.dumps(filtered, default=str)
            await self._get_client().set(
                self._key(video_id),
                serialized,
                ex=settings.REDIS_CACHE_TTL,
            )
            logger.debug("Cached response for video %s", video_id)
            return True
        except (aioredis.ConnectionError, aioredis.TimeoutError) as e:
            logger.debug("Redis set failed for %s: %s", video_id, e)
            return False
        except (aioredis.RedisError, json.JSONDecodeError, TypeError) as e:
            logger.warning("Redis set unexpected error for %s: %s", video_id, e)
            return False

    async def purge_all_versions(self, video_id: str) -> int:
        """Delete the response key of every pipeline version for one video.

        A global purge must not leave an older version's payload behind for
        the rest of its TTL. Returns the number of keys removed; raises on a
        Redis failure so the caller can record the store as failed.
        """
        client = self._get_client()
        removed = 0
        pattern = f"vie:response:*:{video_id}"
        async for key in client.scan_iter(match=pattern, count=100):
            removed += int(await client.delete(key))
        return removed

    async def invalidate(self, video_id: str) -> bool:
        """Remove cached response. Returns True on success."""
        try:
            await self._get_client().delete(self._key(video_id))
            logger.debug("Invalidated cache for video %s", video_id)
            return True
        except (aioredis.ConnectionError, aioredis.TimeoutError) as e:
            logger.debug("Redis invalidate failed for %s: %s", video_id, e)
            return False

    async def close(self) -> None:
        """Close the Redis connection pool."""
        if self._client:
            await self._client.close()
            self._client = None

    async def exists(self, video_id: str) -> bool:
        """Check if response is cached."""
        try:
            return bool(await self._get_client().exists(self._key(video_id)))
        except (aioredis.ConnectionError, aioredis.TimeoutError) as e:
            logger.debug("Redis exists check failed for %s: %s", video_id, e)
            return False
        except aioredis.RedisError as e:
            logger.warning("Redis exists unexpected error for %s: %s", video_id, e)
            return False


# Module-level singleton (lazy-initialized)
response_cache = ResponseCache()
