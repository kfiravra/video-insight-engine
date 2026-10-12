"""Unusable caption transcript → the audio path, once per run (pipeline-1min 2.5).

A wrongly tagged video can carry only an ASR caption track recognised in the
wrong language: TNhaISOUy6Q (English speech) is tagged ``pl`` and its one ASR
track is Polish word salad, which the caption picker takes. The tier probe
reads the native transcript windows before anything translates them (nothing
translates before the plan) and answers ``transcript_usable``; when it says
false, the text branch re-runs the transcript phase on the audio layers
(Whisper → Gemini), starts a new probe on the new text and plans from that.

Gates, all required:

* a probe answer with ``transcript_usable`` false (no answer = no judgement);
* a caption transcript — yt-dlp, youtube-transcript-api, or an S3 blob one of
  them produced; an audio or metadata transcript is what the fallback would
  produce anyway;
* not a music video: lyrics and ``[Music]`` tags would send every song to Whisper;
* an audio layer allowed (``WHISPER_ENABLED`` / ``GEMINI_API_KEY`` within the
  duration cap — the chain's own gates).

One fallback per run. A failed audio pass keeps the caption transcript (a
result beats a failure). Every unusable answer is recorded on the trail as
``<source>-unusable``, and a run that continues with one plans without a probe:
a classification made from garbage steers neither the plan nor the frames tier.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, AsyncGenerator

from src.services.pipeline.phases.probe import probe_for_plan, start_tier_probe
from src.services.pipeline.phases.transcript import run_audio_transcript_pass
from src.services.pipeline.pipeline_timing import timed_step
from src.services.transcription.transcript_fetcher import audio_fallback_allowed

if TYPE_CHECKING:
    from src.services.pipeline.context import PipelineContext

logger = logging.getLogger(__name__)

CAPTION_SOURCES: frozenset[str] = frozenset({"ytdlp", "api", "proxy"})
AUDIO_STEP = "transcript_audio"
# ``transcriptMeta.attempted`` entry: "<source>-unusable" (ytdlp, api, proxy, s3, whisper, …).
UNUSABLE_SUFFIX = "-unusable"


def _is_unusable(ctx: PipelineContext) -> bool:
    return ctx.probe is not None and not ctx.probe.transcript_usable


def _record_unusable(ctx: PipelineContext) -> None:
    """One ``<source>-unusable`` trail entry per answer that judged the transcript unusable."""
    data, trail = ctx.transcript_data, ctx.transcript_trail
    assert data is not None and trail is not None
    trail.attempted.append(f"{data.source}{UNUSABLE_SUFFIX}")


def _is_caption_transcript(ctx: PipelineContext) -> bool:
    """yt-dlp / youtube-transcript-api captions, or an S3 blob one of them produced."""
    data, trail = ctx.transcript_data, ctx.transcript_trail
    assert data is not None
    if data.source in CAPTION_SOURCES:
        return True
    return data.source == "s3" and trail is not None and trail.origin in CAPTION_SOURCES


def _skip_reason(ctx: PipelineContext) -> str | None:
    """Why an unusable transcript stays (None = the audio path may replace it)."""
    video_data = ctx.video_data
    assert video_data is not None
    if not _is_caption_transcript(ctx):
        source = ctx.transcript_data.source if ctx.transcript_data else None
        return f"not a caption transcript (source={source})"
    if video_data.context is not None and video_data.context.category == "music":
        return "music video"
    if not audio_fallback_allowed(video_data.duration or 0):
        return "no audio layer allowed"
    return None


async def _audio_pass(ctx: PipelineContext) -> AsyncGenerator[str, None]:
    """The audio layers in place of the captions, then a second probe on the new text."""
    logger.warning(
        "Tier probe judged the %s transcript of %s unusable; re-transcribing from audio",
        ctx.transcript_data.source if ctx.transcript_data else None,
        ctx.youtube_id,
    )
    caption_transcript = ctx.transcript_data
    with timed_step(AUDIO_STEP):
        async for event in run_audio_transcript_pass(ctx):
            yield event
    if ctx.transcript_data is caption_transcript:
        return
    start_tier_probe(ctx)
    await probe_for_plan(ctx)
    if _is_unusable(ctx):
        _record_unusable(ctx)


def _drop_unusable_probe(ctx: PipelineContext) -> None:
    """The plan gets no probe rather than one classified from garbage (frames ignore it too)."""
    if _is_unusable(ctx):
        logger.warning("Planning without the tier probe: it judged the transcript unusable")
        ctx.probe = None


async def replace_unusable_captions(ctx: PipelineContext) -> AsyncGenerator[str, None]:
    """Audio pass + a fresh probe when the probe judged a caption transcript unusable.

    Records ``<source>-unusable`` on the trail before the audio layers append
    their own failures, so ``transcriptMeta.attempted`` reads in run order;
    ``transcriptMeta.source`` then shows whether the audio path replaced it.
    Streams the audio layers' phase events; ``pipeline.timing`` gets the pass
    as ``transcript_audio`` and the second probe as another ``tier_probe`` call.
    """
    if not _is_unusable(ctx):
        return
    _record_unusable(ctx)
    reason = _skip_reason(ctx)
    if reason is None:
        async for event in _audio_pass(ctx):
            yield event
    else:
        logger.warning("Tier probe judged the transcript unusable; keeping it: %s", reason)
    _drop_unusable_probe(ctx)
