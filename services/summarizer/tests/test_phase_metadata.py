"""Phase 1 metadata: extract_info + validate_duration, then the t=0 background group."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.exceptions import TranscriptError
from src.services.pipeline.phases import metadata
from src.services.video.description_analyzer import DescriptionAnalysis, DescriptionTimestamp
from src.services.video.youtube import SubtitleSegment, VideoData

YOUTUBE = "src.services.video.youtube"
ANALYSIS = DescriptionAnalysis(timestamps=[DescriptionTimestamp("1:00", 60, "Setup")])


def _video_data(duration: int = 600) -> VideoData:
    return VideoData(
        video_id="dQw4w9WgXcQ",
        title="t",
        channel="c",
        duration=duration,
        thumbnail_url=None,
        description="Links: https://example.com",
    )


def _ctx() -> SimpleNamespace:
    return SimpleNamespace(
        youtube_id="dQw4w9WgXcQ",
        llm_service=SimpleNamespace(fast_model="fast"),
        video_data=None,
        lowres_video=MagicMock(),
        hires_video=MagicMock(),
        caption_task=None,
        description_task=None,
        description_analysis=None,
    )


class _World:
    """Fakes for every external the phase starts; ``release`` unblocks the description LLM."""

    def __init__(self, duration: int, cached: bool) -> None:
        self.release = asyncio.Event()
        self.extract = AsyncMock(return_value=_video_data(duration))
        self.captions = AsyncMock()
        self.cached = AsyncMock(return_value=cached)

    async def analyze(self, description: str, fast_model: str) -> DescriptionAnalysis:
        await self.release.wait()
        return ANALYSIS


@pytest.fixture
def world() -> Iterator[_World]:
    yield _World(duration=600, cached=False)


def _patches(world: _World):
    return (
        patch(f"{YOUTUBE}.extract_video_data", world.extract),
        patch(f"{YOUTUBE}.fetch_video_captions", world.captions),
        patch.object(metadata, "frames_cached", world.cached),
        patch.object(metadata, "analyze_description", world.analyze),
        patch.object(metadata.settings, "SCENE_EXTRACTION_ENABLED", True),
        patch.object(metadata.settings, "SCENE_HIRES_ENABLED", True),
    )


async def _run(ctx: SimpleNamespace, world: _World) -> list[str]:
    patches = _patches(world)
    for p in patches:
        p.start()
    try:
        return [chunk async for chunk in metadata.run_phase_metadata(ctx)]  # type: ignore[arg-type]
    finally:
        for p in patches:
            p.stop()


class TestRunPhaseMetadata:
    async def test_should_end_before_the_description_analysis_lands(self, world):
        ctx = _ctx()

        await _run(ctx, world)

        assert not ctx.description_task.done()
        world.release.set()
        await ctx.description_task

    async def test_should_defer_the_caption_fetch(self, world):
        ctx = _ctx()

        await _run(ctx, world)
        await ctx.caption_task

        world.captions.assert_awaited_once_with(ctx.video_data)

    async def test_should_start_both_downloads_when_frames_are_not_cached(self, world):
        ctx = _ctx()

        await _run(ctx, world)

        assert (ctx.lowres_video.start.call_count, ctx.hires_video.start.call_count) == (1, 1)

    async def test_should_start_no_download_when_frames_are_cached(self):
        world = _World(duration=600, cached=True)
        ctx = _ctx()

        await _run(ctx, world)

        assert (ctx.lowres_video.start.call_count, ctx.hires_video.start.call_count) == (0, 0)

    async def test_should_tell_the_manifest_lookup_to_skip_s3_when_the_run_is_cold(self, world):
        ctx = _ctx()
        ctx.cold_media = True

        await _run(ctx, world)

        assert world.cached.await_args.kwargs == {"skip_cache": True}

    async def test_should_start_downloads_when_the_manifest_lookup_is_slow(self, monkeypatch):
        monkeypatch.setattr(metadata, "_CACHE_CHECK_CAP_SECONDS", 0.01)
        world = _World(duration=600, cached=True)

        async def slow_lookup(video_id: str, *, skip_cache: bool = False) -> bool:
            await asyncio.sleep(10)
            return True

        world.cached = slow_lookup  # type: ignore[assignment]
        ctx = _ctx()

        await _run(ctx, world)

        assert ctx.lowres_video.start.call_count == 1

    async def test_should_start_nothing_for_a_rejected_video(self):
        world = _World(duration=30, cached=False)  # under MIN_VIDEO_DURATION_SECONDS
        ctx = _ctx()

        with pytest.raises(TranscriptError):
            await _run(ctx, world)

        assert (ctx.lowres_video.start.called, ctx.caption_task, ctx.description_task) == (
            False,
            None,
            None,
        )


class TestDescriptionAnalysis:
    async def test_readers_should_get_the_analysis_once_it_lands(self, world):
        ctx = _ctx()
        await _run(ctx, world)
        world.release.set()

        assert await metadata.await_description_analysis(ctx) is ANALYSIS  # type: ignore[arg-type]

    async def test_should_yield_none_when_the_analysis_fails(self, world):
        async def broken(description: str, fast_model: str) -> DescriptionAnalysis:
            raise RuntimeError("llm down")

        world.analyze = broken  # type: ignore[method-assign]
        ctx = _ctx()
        await _run(ctx, world)

        assert await metadata.await_description_analysis(ctx) is None  # type: ignore[arg-type]

    async def test_should_survive_a_cancelled_reader(self, world):
        ctx = _ctx()
        await _run(ctx, world)
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(metadata.await_description_analysis(ctx), timeout=0.01)  # type: ignore[arg-type]

        world.release.set()

        assert await metadata.await_description_analysis(ctx) is ANALYSIS  # type: ignore[arg-type]


class TestAfterCaptions:
    async def test_should_run_the_phase_once_the_captions_landed(self):
        video_data = _video_data()
        seen: list[int] = []

        async def fetch() -> None:
            await asyncio.sleep(0)
            video_data.subtitles = [SubtitleSegment("hi", 0.0, 1.0)]

        async def run_phase_transcript(ctx):
            seen.append(len(ctx.video_data.subtitles))
            yield "data: transcript\n\n"

        ctx = SimpleNamespace(video_data=video_data, caption_task=asyncio.create_task(fetch()))
        phase = metadata.after_captions(run_phase_transcript)

        _ = [e async for e in phase(ctx)]  # type: ignore[arg-type]

        assert (seen, phase.__name__) == ([1], "run_phase_transcript")


class TestReleaseBackgroundWork:
    async def test_should_cancel_tasks_a_failed_run_never_joined(self):
        pending = asyncio.create_task(asyncio.sleep(60))
        ctx = SimpleNamespace(caption_task=pending, description_task=None)

        metadata.release_background_work(ctx)  # type: ignore[arg-type]
        await asyncio.gather(pending, return_exceptions=True)

        assert pending.cancelled()
