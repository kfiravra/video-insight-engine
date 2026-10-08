"""Phase 1: Metadata — one yt-dlp ``extract_info``, ``validate_duration``, then the t=0 group.

The phase ends as soon as the video is accepted. What used to run inside it
now runs in the background from that moment, alongside transcript and frames:

* the caption fetch (and its 429 retries) — the transcript phase awaits it
  (``after_captions``);
* description analysis (an LLM call) — every reader awaits it
  (``await_description_analysis``); assembly emits its SSE;
* the low-res and 720p downloads (``ctx.lowres_video`` / ``ctx.hires_video``),
  unless the frame manifest is cached — checked during ``extract_info``.

Nothing starts before ``validate_duration`` passes, so a rejected video costs
no download bytes and no LLM call.
"""

from __future__ import annotations

import asyncio
import functools
import logging
from collections.abc import AsyncGenerator, Callable
from typing import TYPE_CHECKING

from llm_common.context import llm_feature_var

from src.config import settings
from src.services.media.scene_extractor import frames_cached
from src.services.pipeline.pipeline_helpers import sse_event, validate_duration
from src.services.video.description_analyzer import DescriptionAnalysis, analyze_description

if TYPE_CHECKING:
    from src.services.pipeline.context import PipelineContext

logger = logging.getLogger(__name__)

PhaseFn = Callable[["PipelineContext"], AsyncGenerator[str, None]]

# The manifest lookup normally answers long before extract_info returns; a slow
# S3 must not stretch the metadata wall. Past the cap it counts as a miss (the
# downloads start; scene extraction's own lookup still serves a late hit).
_CACHE_CHECK_CAP_SECONDS = 2.0


async def run_phase_metadata(ctx: PipelineContext) -> AsyncGenerator[str, None]:
    """Extract video metadata, validate duration, start the t=0 background group."""
    from src.services.video import youtube

    llm_feature_var.set("summarize:metadata")
    # The manifest lookup (one S3 GET) overlaps extract_info; its answer
    # decides whether the downloads start at all.
    cache_check = (
        asyncio.create_task(
            frames_cached(ctx.youtube_id, skip_cache=getattr(ctx, "cold_media", False))
        )
        if settings.SCENE_EXTRACTION_ENABLED
        else None
    )
    try:
        ctx.video_data = await youtube.extract_video_data(ctx.youtube_id)
        yield sse_event(
            "metadata",
            {
                "title": ctx.video_data.title,
                "channel": ctx.video_data.channel,
                "thumbnailUrl": ctx.video_data.thumbnail_url,
                "duration": ctx.video_data.duration,
            },
        )
        validate_duration(ctx.video_data.duration)
    except BaseException:
        if cache_check is not None:
            cache_check.cancel()
        raise

    ctx.caption_task = asyncio.create_task(youtube.fetch_video_captions(ctx.video_data))
    ctx.description_task = asyncio.create_task(_analyze_description(ctx))
    await _start_downloads(ctx, cache_check)


async def _start_downloads(ctx: PipelineContext, cache_check: asyncio.Task[bool] | None) -> None:
    """Start the run's low-res + 720p downloads unless the frames are cached."""
    if cache_check is None:
        return
    try:
        cached = await asyncio.wait_for(cache_check, timeout=_CACHE_CHECK_CAP_SECONDS)
    except asyncio.TimeoutError:
        logger.warning("Frame manifest lookup slow for %s — downloading anyway", ctx.youtube_id)
        cached = False
    if cached:
        # Moment fill still starts the 720p file itself if it needs one.
        logger.info("Frames cached for %s — no early video download", ctx.youtube_id)
        return
    if ctx.lowres_video is not None:
        ctx.lowres_video.start()
    if ctx.hires_video is not None and settings.SCENE_HIRES_ENABLED:
        ctx.hires_video.start()


async def _analyze_description(ctx: PipelineContext) -> None:
    """Description analysis for meta + chapter tier 2. Never raises (non-critical)."""
    assert ctx.video_data is not None
    try:
        ctx.description_analysis = await analyze_description(
            ctx.video_data.description or "",
            fast_model=(
                settings.get_stage_model("description_analysis") or ctx.llm_service.fast_model
            ),
        )
    except Exception as e:
        logger.warning("Description analysis failed (non-critical): %s", e)
        ctx.description_analysis = None


async def await_description_analysis(ctx: PipelineContext) -> DescriptionAnalysis | None:
    """The description analysis started in metadata, once it has landed.

    Shielded: a reader's own cancellation never cancels the analysis another
    reader is waiting for.
    """
    task = getattr(ctx, "description_task", None)
    if task is not None:
        await asyncio.shield(task)
    return getattr(ctx, "description_analysis", None)


async def await_captions(ctx: PipelineContext) -> None:
    """Join the caption fetch started in metadata (fills ``video_data.subtitles``)."""
    task = getattr(ctx, "caption_task", None)
    if task is not None:
        await task


def after_captions(phase: PhaseFn) -> PhaseFn:
    """``phase`` run once the captions landed; keeps its name for ``pipeline.timing``."""

    @functools.wraps(phase)
    async def wrapped(ctx: PipelineContext) -> AsyncGenerator[str, None]:
        await await_captions(ctx)
        async for event in phase(ctx):
            yield event

    return wrapped


def release_background_work(ctx: PipelineContext) -> None:
    """Cancel the t=0 tasks a failed or cancelled run never joined.

    A finished task's exception is retrieved so asyncio never logs it as lost.
    """
    for name in ("caption_task", "description_task"):
        task: asyncio.Task[None] | None = getattr(ctx, name, None)
        if task is None:
            continue
        if not task.done():
            task.cancel()
        elif not task.cancelled():
            task.exception()
