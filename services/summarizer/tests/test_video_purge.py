"""Tests for the global video purge service (video_purge.py)."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.services import video_purge as vp

YT = "dQw4w9WgXcQ"
IDS = ["6a7b2f6abacdb32871507996", "6a7b2f6abacdb32871507997"]


@pytest.fixture
def stores(monkeypatch):
    vector = MagicMock()
    vector.count_points.return_value = 7
    vector.delete_video.return_value = True
    monkeypatch.setattr(vp, "_get_vector_service", lambda: vector)

    s3 = MagicMock()
    s3.delete_prefix = AsyncMock(return_value=5)
    monkeypatch.setattr(vp, "s3_client", s3)
    monkeypatch.setattr(vp.S3Client, "is_available", staticmethod(lambda: True))

    cache = MagicMock()
    cache.purge_all_versions = AsyncMock(return_value=1)
    monkeypatch.setattr(vp, "response_cache", cache)

    stream = MagicMock()
    stream.purge = AsyncMock(return_value=2)
    monkeypatch.setattr(vp, "pipeline_event_stream", stream)

    monkeypatch.setattr(vp, "settings", MagicMock(QDRANT_ENABLED=True, REDIS_ENABLED=True))
    monkeypatch.setattr(vp, "clear_override", MagicMock())
    return {"vector": vector, "s3": s3, "cache": cache, "stream": stream}


class TestPurgeVideo:
    @pytest.mark.asyncio
    async def test_should_count_every_store_when_all_succeed(self, stores):
        outcome = await vp.purge_video(YT, IDS)

        assert (outcome.qdrant_points, outcome.s3_objects, outcome.redis_keys) == (7, 5, 5)
        assert outcome.warnings == []
        stores["s3"].delete_prefix.assert_awaited_once_with(f"videos/{YT}/")
        assert stores["stream"].purge.await_count == len(IDS)

    @pytest.mark.asyncio
    async def test_should_keep_purging_other_stores_when_s3_fails(self, stores):
        stores["s3"].delete_prefix = AsyncMock(side_effect=RuntimeError("boom"))

        outcome = await vp.purge_video(YT, IDS)

        assert outcome.warnings == ["s3: boom"]
        assert outcome.qdrant_points == 7
        assert outcome.redis_keys == 5

    @pytest.mark.asyncio
    async def test_should_keep_other_counts_when_redis_fails(self, stores):
        stores["stream"].purge = AsyncMock(side_effect=RuntimeError("redis down"))

        outcome = await vp.purge_video(YT, IDS)

        assert outcome.warnings == ["redis: redis down"]
        assert (outcome.qdrant_points, outcome.s3_objects) == (7, 5)

    @pytest.mark.asyncio
    async def test_should_skip_qdrant_when_disabled(self, stores, monkeypatch):
        monkeypatch.setattr(vp, "settings", MagicMock(QDRANT_ENABLED=False, REDIS_ENABLED=True))

        outcome = await vp.purge_video(YT, IDS)

        assert outcome.qdrant_points == 0
        stores["vector"].delete_video.assert_not_called()

    @pytest.mark.asyncio
    async def test_should_warn_when_qdrant_delete_reports_failure(self, stores):
        stores["vector"].delete_video.return_value = False

        outcome = await vp.purge_video(YT, IDS)

        assert outcome.qdrant_points == 0
        assert outcome.warnings[0].startswith("qdrant:")

    @pytest.mark.asyncio
    async def test_should_clear_in_memory_override_for_each_summary(self, stores):
        await vp.purge_video(YT, IDS)

        assert vp.clear_override.call_count == len(IDS)
