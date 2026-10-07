"""Phase 2a: Transcript — fetch, clean, SponsorBlock filter."""

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
from src.services.transcription.transcript_fetcher import fetch_transcript
from src.services.video.sponsorblock import filter_transcript_segments, get_sponsor_segments
from src.utils.worker_pool import run_in_pool

if TYPE_CHECKING:
    from src.services.pipeline.context import PipelineContext
    from src.services.pipeline.pipeline_helpers import TranscriptData
    from src.services.video.youtube import VideoData

logger = logging.getLogger(__name__)


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
            ctx.youtube_id, video_data, video_data.duration, is_music=is_music, trail=trail
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
        trail.fetch_wall_ms = int((time.monotonic() - started) * 1000)
        ctx.transcript_trail = trail


async def run_phase_transcript(ctx: PipelineContext) -> AsyncGenerator[str, None]:
    """Fetch transcript, clean it, and apply SponsorBlock filtering."""
    llm_feature_var.set("summarize:transcript")
    video_data = ctx.video_data
    assert video_data is not None

    is_music = (video_data.context.category == "music") if video_data.context else False

    # Fetch transcript
    trail = TranscriptTrail()
    transcript_data: TranscriptData | None = None
    async for item in _fetch_with_trail(ctx, video_data, is_music, trail):
        if isinstance(item, str):
            yield item
        else:
            transcript_data = item

    # _fetch_with_trail raises NO_TRANSCRIPT before we get here without data.
    assert transcript_data is not None

    ctx.transcript_data = transcript_data
    ctx.prompt_segments = list(transcript_data.segments or [])

    # English-canonical pipeline: ALL generation runs in English, so ctx.language
    # stays "en" (its default). We only record the DETECTED original language —
    # it drives the final English→source translation pass, the RAG transcript
    # translation, and cache ownership. Detect from the source's own metadata or,
    # when absent (e.g. the Gemini fallback carries no language), from the
    # transcript content — never leave a non-English video unrecorded.
    from src.utils.language_utils import (
        detect_language_by_script,
        detect_language_from_text,
    )

    raw_text = transcript_data.raw_text or ""
    detected_language = transcript_data.language
    if not detected_language and raw_text:
        detected_language = detect_language_from_text(raw_text) or detect_language_by_script(
            raw_text
        )
    source_code = detected_language if detected_language and detected_language != "en" else None

    # Sound-only detection: instrumental/no-speech music whose transcription is
    # hallucinated foreign-language fragments. Drop the source language so no
    # translation runs and the FE renders no language toggle — the right UX.
    from src.utils.language_utils import is_sound_only_video

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
        source_code = None

    ctx.source_language_code = source_code
    logger.info("Source language: %s (generation runs in English)", source_code or "en")

    yield sse_event("transcript_ready", {"duration": video_data.duration})
    ctx.clean_text = clean_transcript(
        transcript_data.raw_text, source_language=ctx.source_language_code
    )

    # Advanced cleaning (spaCy + TF-IDF)
    if settings.TRANSCRIPT_CLEANING_ENABLED:
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

    # SponsorBlock filtering
    try:
        sponsor_segments = await get_sponsor_segments(ctx.youtube_id)
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
