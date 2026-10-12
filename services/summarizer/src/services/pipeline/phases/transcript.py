"""Phase 2a: Transcript — fetch, clean, SponsorBlock filter.

``run_audio_transcript_pass`` re-runs the phase on the audio layers alone, with
the same post-processing, when the tier probe judged a caption transcript
unusable (pipeline-1min 2.5, ``phases/transcript_fallback.py``).
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING, AsyncGenerator

from llm_common.context import llm_feature_var

from src.config import settings
from src.exceptions import TranscriptError
from src.models.schemas import ErrorCode
from src.services.pipeline.pipeline_helpers import (
    TranscriptTrail,
    normalize_segments,
    sse_event,
)
from src.services.transcript.cleaner import clean_transcript_advanced
from src.services.transcription.transcript import clean_transcript
from src.services.transcription.transcript_fetcher import fetch_audio_transcript, fetch_transcript
from src.services.video.sponsorblock import (
    SponsorSegment,
    filter_transcript_segments,
    get_sponsor_segments,
)
from src.utils.language_utils import (
    detect_language_by_script,
    detect_language_from_text,
    is_sound_only_video,
)
from src.utils.worker_pool import run_in_pool

if TYPE_CHECKING:
    from src.services.pipeline.context import PipelineContext
    from src.services.pipeline.pipeline_helpers import TranscriptData
    from src.services.video.youtube import VideoData

logger = logging.getLogger(__name__)

TRANSCRIPT_FEATURE = "summarize:transcript"


def _is_music(video_data: VideoData) -> bool:
    return (video_data.context.category == "music") if video_data.context else False


def _elapsed_ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)


async def _fetch_with_trail(
    ctx: PipelineContext, video_data: VideoData, is_music: bool, trail: TranscriptTrail
) -> AsyncGenerator[str | TranscriptData, None]:
    """Run the fetch chain, stamping wall time + outcome on ``trail`` whatever happens.

    The trail is hung on ``ctx`` in a ``finally`` so the runner can persist
    provenance for failed runs too — which layers were tried before the chain
    gave up is the whole point of the record. Raises NO_TRANSCRIPT when the
    chain finishes without ever yielding a ``TranscriptData``.
    """
    started = time.monotonic()
    got_transcript = False
    try:
        async for item in fetch_transcript(
            ctx.youtube_id,
            video_data,
            video_data.duration,
            is_music=is_music,
            trail=trail,
            skip_cache=getattr(ctx, "cold_media", False),
        ):
            if not isinstance(item, str):
                got_transcript = True
            yield item
        if not got_transcript:
            raise TranscriptError("Failed to fetch transcript", ErrorCode.NO_TRANSCRIPT)
    except asyncio.CancelledError:
        # Producer torn down mid-fetch (worker restart, sibling-phase failure):
        # keep the persisted block self-describing instead of a null code.
        trail.error_code = "CANCELLED"
        raise
    except TranscriptError as e:
        trail.error_code = e.code.value
        raise
    except Exception:
        trail.error_code = ErrorCode.UNKNOWN_ERROR.value
        raise
    finally:
        trail.fetch_wall_ms = _elapsed_ms(started)
        ctx.transcript_trail = trail


# ─── Post-processing (shared by the first pass and the audio pass) ───────────


def _source_language(
    transcript_data: TranscriptData, video_data: VideoData, is_music: bool
) -> str | None:
    """The detected non-English source language, or None (English / sound-only).

    English-canonical pipeline: ALL generation runs in English, so ctx.language
    stays "en". The DETECTED original language drives the final English→source
    translation pass, the RAG transcript translation, and cache ownership.
    Detected from the source's own metadata or, when absent (e.g. the Gemini
    fallback carries no language), from the transcript content — never leave a
    non-English video unrecorded.
    """
    raw_text = transcript_data.raw_text or ""
    detected = transcript_data.language
    if not detected and raw_text:
        detected = detect_language_from_text(raw_text) or detect_language_by_script(raw_text)
    source_code = detected if detected and detected != "en" else None

    # Sound-only detection: instrumental/no-speech music whose transcription is
    # hallucinated foreign-language fragments. Drop the source language so no
    # translation runs and the FE renders no language toggle — the right UX.
    if source_code and is_sound_only_video(
        is_music=is_music,
        language=source_code,
        raw_text=raw_text,
        duration=video_data.duration or 0,
        wps_threshold=settings.MUSIC_LANGUAGE_FORCE_EN_WPS,
    ):
        logger.warning(
            "Sound-only music video detected; dropping source language %r "
            "(word_count=%d, duration=%ds)",
            source_code,
            len(raw_text.split()),
            video_data.duration or 0,
        )
        return None
    return source_code


def _adopt_transcript(
    ctx: PipelineContext, transcript_data: TranscriptData, video_data: VideoData, is_music: bool
) -> None:
    """Make ``transcript_data`` the run's transcript: data, prompt segments, source language."""
    ctx.transcript_data = transcript_data
    ctx.prompt_segments = list(transcript_data.segments or [])
    ctx.source_language_code = _source_language(transcript_data, video_data, is_music)
    logger.info(
        "Source language: %s (generation runs in English)", ctx.source_language_code or "en"
    )


async def _advanced_clean(ctx: PipelineContext) -> None:
    """spaCy + TF-IDF cleaning of ``ctx.clean_text``; basic cleaning stays on failure."""
    try:
        ctx.clean_text = await asyncio.wait_for(
            run_in_pool(clean_transcript_advanced, ctx.clean_text),
            timeout=settings.TRANSCRIPT_CLEANING_TIMEOUT,
        )
    except asyncio.TimeoutError:
        logger.warning(
            "Advanced transcript cleaning timed out (%.0fs), using basic cleaning",
            settings.TRANSCRIPT_CLEANING_TIMEOUT,
        )
    except Exception as e:
        logger.warning("Advanced transcript cleaning failed (non-critical): %s", e)


async def _sponsor_segments(ctx: PipelineContext) -> list[SponsorSegment]:
    """The run's SponsorBlock segments, fetched once: the audio pass reuses the first pass's."""
    # getattr: phase tests drive this with context stand-ins that predate the field.
    cached: list[SponsorSegment] | None = getattr(ctx, "sponsor_segments", None)
    if cached is None:
        cached = await get_sponsor_segments(ctx.youtube_id)
        ctx.sponsor_segments = cached
    return cached


async def _filter_sponsor_reads(ctx: PipelineContext, transcript_data: TranscriptData) -> None:
    """Cut SponsorBlock reads from ``clean_text`` AND ``prompt_segments``."""
    try:
        sponsor_segments = await _sponsor_segments(ctx)
        if sponsor_segments and transcript_data.segments:
            normalized = normalize_segments(transcript_data.segments)
            sb_segments = [
                {
                    "text": s["text"],
                    "start": s["startMs"] / 1000.0,
                    "duration": (s["endMs"] - s["startMs"]) / 1000.0,
                }
                for s in normalized
            ]
            filtered = filter_transcript_segments(sb_segments, sponsor_segments)
            if filtered:
                ctx.clean_text = clean_transcript(
                    " ".join(s["text"] for s in filtered), source_language=ctx.source_language_code
                )
                # Prompt renders must drop the sponsor read too, not just clean_text.
                ctx.prompt_segments = filtered
                logger.info("SponsorBlock: filtered %d sponsor segments", len(sponsor_segments))
    except (TypeError, ValueError, KeyError) as e:
        logger.warning("SponsorBlock filtering failed (non-critical): %s - %s", type(e).__name__, e)
    except (OSError, TimeoutError) as e:
        logger.error("SponsorBlock network error: %s - %s", type(e).__name__, e)


async def _build_clean_text(ctx: PipelineContext, transcript_data: TranscriptData) -> None:
    """``ctx.clean_text`` from the adopted transcript: basic → advanced cleaning → SponsorBlock."""
    ctx.clean_text = clean_transcript(
        transcript_data.raw_text, source_language=ctx.source_language_code
    )
    if settings.TRANSCRIPT_CLEANING_ENABLED:
        await _advanced_clean(ctx)
    await _filter_sponsor_reads(ctx, transcript_data)


# ─── Phases ──────────────────────────────────────────────────────────────────


async def run_phase_transcript(ctx: PipelineContext) -> AsyncGenerator[str, None]:
    """Fetch transcript, clean it, and apply SponsorBlock filtering."""
    llm_feature_var.set(TRANSCRIPT_FEATURE)
    video_data = ctx.video_data
    assert video_data is not None
    is_music = _is_music(video_data)

    trail = TranscriptTrail()
    transcript_data: TranscriptData | None = None
    async for item in _fetch_with_trail(ctx, video_data, is_music, trail):
        if isinstance(item, str):
            yield item
        else:
            transcript_data = item

    # _fetch_with_trail raises NO_TRANSCRIPT before we get here without data.
    assert transcript_data is not None
    _adopt_transcript(ctx, transcript_data, video_data, is_music)
    yield sse_event("transcript_ready", {"duration": video_data.duration})
    await _build_clean_text(ctx, transcript_data)


async def run_audio_transcript_pass(ctx: PipelineContext) -> AsyncGenerator[str, None]:
    """Re-run the transcript phase on the audio layers only (Whisper → Gemini).

    No S3, no captions, no youtube-transcript-api: the caller already holds a
    transcript and wants the audio's instead. Streams the audio layers' phase
    events; on success the audio transcript replaces ``ctx.transcript_data``
    with the first pass's post-processing (source language, prompt segments,
    cleaning, SponsorBlock). On failure the current transcript stays — a result
    beats a failure — and the failed layers are on the trail. The pass's wall
    time is added to the trail's ``fetch_wall_ms``.
    """
    llm_feature_var.set(TRANSCRIPT_FEATURE)
    video_data = ctx.video_data
    trail = ctx.transcript_trail
    assert video_data is not None and trail is not None
    is_music = _is_music(video_data)

    audio_data: TranscriptData | None = None
    started = time.monotonic()
    try:
        async for item in fetch_audio_transcript(
            ctx.youtube_id, video_data.duration or 0, is_music, trail=trail
        ):
            if isinstance(item, str):
                yield item
            else:
                audio_data = item
    except TranscriptError as exc:
        logger.warning(
            "Audio transcription failed (%s); keeping the %s transcript",
            exc.code.value,
            ctx.transcript_data.source if ctx.transcript_data else "current",
        )
    finally:
        trail.fetch_wall_ms = (trail.fetch_wall_ms or 0) + _elapsed_ms(started)
    if audio_data is None:
        return
    # ``origin`` describes an S3-served transcript; this one came from audio.
    trail.origin = None
    _adopt_transcript(ctx, audio_data, video_data, is_music)
    await _build_clean_text(ctx, audio_data)
