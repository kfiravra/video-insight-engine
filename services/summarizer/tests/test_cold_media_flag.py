"""1a.7: the API's ``coldMedia`` row stamp (admin/eval ``cold: true``).

The runner turns it into ``ctx.cold_media`` (read by the transcript, metadata
and frames phases) and marks the trace; the final save consumes it together
with ``forceRefresh`` so a later re-run of the row is warm again.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from bson import ObjectId

from src.repositories.mongodb_repository import MongoDBVideoRepository
from src.routes import pipeline_runner


async def _run(entry: dict[str, Any]) -> tuple[Any, dict[str, Any]]:
    seen: dict[str, Any] = {}

    async def _phases(ctx: Any, *_args: object):
        seen["ctx"] = ctx
        yield "data: phases\n\n"

    trace = MagicMock()
    trace.return_value.__aenter__ = AsyncMock()
    trace.return_value.__aexit__ = AsyncMock(return_value=False)
    with (
        patch.object(pipeline_runner.settings, "REDIS_ENABLED", False),
        patch.object(pipeline_runner, "run_pipeline_phases", _phases),
        patch.object(pipeline_runner, "send_video_status", AsyncMock()),
        patch.object(pipeline_runner, "clear_override", MagicMock()),
        patch.object(pipeline_runner, "pipeline_trace", trace),
    ):
        _ = [
            ev
            async for ev in pipeline_runner.stream_summarization(
                "vsid", entry, MagicMock(), MagicMock()
            )
        ]
    return seen["ctx"], trace.call_args.kwargs["metadata"]


class TestRunnerColdFlag:
    async def test_should_flag_the_context_when_the_row_is_stamped_cold(self) -> None:
        ctx, _metadata = await _run({"youtubeId": "yt1", "coldMedia": True})

        assert ctx.cold_media is True

    async def test_should_mark_the_trace_when_the_row_is_stamped_cold(self) -> None:
        _ctx, metadata = await _run({"youtubeId": "yt1", "coldMedia": True})

        assert metadata["coldMedia"] is True

    async def test_should_leave_a_normal_run_warm(self) -> None:
        ctx, metadata = await _run({"youtubeId": "yt1"})

        assert (ctx.cold_media, "coldMedia" in metadata) == (False, False)


class TestFinalSaveConsumesTheStamp:
    def test_should_unset_cold_media_when_the_run_completes(self) -> None:
        collection = MagicMock()
        database = MagicMock()
        database.videoSummaryCache = collection
        repository = MongoDBVideoRepository(database)

        repository.save_structured_result(str(ObjectId()), {"status": "completed"})

        assert "coldMedia" in collection.update_one.call_args.args[1]["$unset"]
