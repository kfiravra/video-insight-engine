"""Whisper audio transcription fallback for videos without captions.

This is the nuclear option - used only when:
1. No captions exist on YouTube
2. All other transcript methods have failed

Cost: ~$0.006 per minute of audio via OpenAI Whisper API
"""

import asyncio
import logging
import tempfile
import time
import uuid
from collections import Counter
from pathlib import Path

from openai import OpenAI
from pydub import AudioSegment

from src.config import settings
from src.exceptions import TranscriptError
from src.models.schemas import (
    ErrorCode,
    NormalizedTranscript,
    TranscriptSegment,
)
from src.services.media.download_utils import download_youtube_audio
from src.services.pipeline.pipeline_timing import record_download
from src.services.transcription.usage import emit_transcription_usage
from src.utils.language_utils import normalize_language_code

logger = logging.getLogger(__name__)

# Temp directory for audio files
TEMP_DIR = Path(tempfile.gettempdir()) / "vie-whisper"

# Target chunk size in MB (under Whisper API 25MB limit)
CHUNK_TARGET_SIZE_MB = 24


def _cached_audio_path(video_id: str) -> Path:
    """Deterministic cache path for audio reuse by translate step."""
    return TEMP_DIR / f"{video_id}_cached.mp3"


def _response_duration(response: object) -> float:
    """Billed audio seconds from a Whisper verbose_json response.

    Type-guarded: a bare ``MagicMock`` (in tests) or a missing field yields
    0.0 rather than a Mock that would blow up ``float()`` — same hazard the
    callback's ``_safe_int`` guards against.
    """
    value = getattr(response, "duration", 0.0)
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else 0.0


def _make_openai_client() -> OpenAI:
    """OpenAI client with a bounded per-request timeout + retry budget.

    The SDK default (600s × 2 retries) lets a stalled chunk upload hang far past
    the pipeline's outer backstop with zero feedback — the freeze observed on a
    long Hebrew video. Bounding it here makes a hung chunk surface as a
    ``TranscriptError`` in minutes, including chunk 0 (the chunked loop's
    ``deadline`` only guards chunks after the first).
    """
    return OpenAI(
        api_key=settings.OPENAI_API_KEY,
        timeout=settings.WHISPER_CLIENT_TIMEOUT_SECONDS,
        max_retries=settings.WHISPER_MAX_RETRIES,
    )


def _download_audio_sync(video_id: str) -> Path:
    """
    Download audio from YouTube using yt-dlp.

    Retries transient failures with exponential backoff. yt-dlp is also
    configured to retry internally on fragment/HTTP errors.

    Args:
        video_id: YouTube video ID

    Returns:
        Path to downloaded MP3 file

    Raises:
        TranscriptError: If download fails after all retries
    """
    TEMP_DIR.mkdir(exist_ok=True)
    # Unique suffix prevents race conditions when concurrent requests
    # download the same video (multiple tabs, API retries)
    download_id = uuid.uuid4().hex[:8]
    file_stem = f"{video_id}_{download_id}"
    output_path = TEMP_DIR / f"{file_stem}.%(ext)s"

    ydl_opts = {
        # bestaudio has no match when YouTube's SABR experiment strips audio-only
        # format URLs from the android client (2026-09) — /best falls back to a
        # progressive muxed stream, which FFmpegExtractAudio demuxes to mp3.
        "format": "bestaudio/best",
        "postprocessors": [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "128",
            }
        ],
        "outtmpl": str(output_path),
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        # Network resilience
        "retries": 3,
        "fragment_retries": 5,
        "socket_timeout": 30,
        "continuedl": False,
    }

    started = time.monotonic()
    download_youtube_audio(video_id, ydl_opts, TEMP_DIR, file_stem)

    mp3_path = TEMP_DIR / f"{file_stem}.mp3"
    record_download(
        kind="audio", purpose="whisper", start_monotonic=started, path=mp3_path,
        ok=mp3_path.exists(),
    )
    if not mp3_path.exists():
        raise TranscriptError(
            "Audio download completed but file not found",
            ErrorCode.UNKNOWN_ERROR,
        )

    file_size_mb = mp3_path.stat().st_size / (1024 * 1024)
    logger.info("Downloaded audio: %s (%.1fMB)", mp3_path, file_size_mb)
    return mp3_path


def _transcribe_sync(
    audio_path: Path,
    is_music: bool = False,
    client: OpenAI | None = None,
) -> dict:
    """
    Transcribe audio using OpenAI Whisper API.

    Args:
        audio_path: Path to audio file
        is_music: If True, provide a lyrics-focused prompt hint
        client: Optional pre-initialized OpenAI client (reused across chunks)

    Returns:
        Transcription response with text and optional segments

    Raises:
        TranscriptError: If transcription fails
    """
    if not settings.OPENAI_API_KEY:
        raise TranscriptError(
            "OpenAI API key not configured for Whisper",
            ErrorCode.UNKNOWN_ERROR,
        )

    if client is None:
        client = _make_openai_client()

    try:
        with open(audio_path, "rb") as f:
            # Use verbose_json to get word-level timestamps
            # Whisper's prompt parameter guides transcription style
            whisper_kwargs: dict = {
                "model": "whisper-1",
                "file": f,
                "response_format": "verbose_json",
                "timestamp_granularities": ["segment"],
            }
            if is_music:
                whisper_kwargs["prompt"] = (
                    "Transcribe all lyrics and singing accurately. Include all vocal content."
                )
            response = client.audio.transcriptions.create(**whisper_kwargs)

        # Capture detected language from Whisper response
        detected_language = getattr(response, "language", None)
        logger.info(
            "Whisper transcription complete: %d chars, language=%s",
            len(response.text),
            detected_language,
        )
        # Convert Pydantic TranscriptionSegment objects to plain dicts
        # so downstream code can use .get() safely
        raw_segments = response.segments if hasattr(response, "segments") else []
        segments = [
            {
                "text": getattr(seg, "text", ""),
                "start": getattr(seg, "start", 0),
                "end": getattr(seg, "end", 0),
            }
            for seg in (raw_segments or [])
        ]
        return {
            "text": response.text,
            "segments": segments,
            "language": detected_language,
            # verbose_json carries the billed audio duration in seconds; used
            # to compute Whisper cost ($0.006/min) for the usage ledger.
            "duration": _response_duration(response),
        }
    except Exception as e:
        logger.error("Whisper transcription failed: %s", e)
        raise TranscriptError(
            f"Whisper transcription failed: {str(e)}",
            ErrorCode.UNKNOWN_ERROR,
        )


def _split_audio_chunks(audio_path: Path) -> list[tuple[Path, int]]:
    """
    Split a large audio file into chunks under the Whisper API size limit.

    Uses pydub to load the MP3, calculates chunk duration from the file's
    bitrate and target size, then exports each chunk as a separate MP3.

    Args:
        audio_path: Path to the original MP3 file

    Returns:
        List of (chunk_path, offset_ms) tuples
    """
    audio = AudioSegment.from_mp3(audio_path)
    file_size_bytes = audio_path.stat().st_size
    duration_ms = len(audio)

    if duration_ms == 0:
        raise TranscriptError(
            "Audio file has zero duration",
            ErrorCode.UNKNOWN_ERROR,
        )

    bytes_per_ms = file_size_bytes / duration_ms
    chunk_duration_ms = int((CHUNK_TARGET_SIZE_MB * 1024 * 1024) / bytes_per_ms)

    chunks: list[tuple[Path, int]] = []
    stem = audio_path.stem
    parent = audio_path.parent

    for i, offset_ms in enumerate(range(0, duration_ms, chunk_duration_ms)):
        end_ms = min(offset_ms + chunk_duration_ms, duration_ms)
        chunk = audio[offset_ms:end_ms]
        chunk_path = parent / f"{stem}_chunk_{i}.mp3"
        chunk.export(str(chunk_path), format="mp3")
        chunks.append((chunk_path, offset_ms))

    logger.info(
        "Split %s into %d chunks (%.0fs each)",
        audio_path.name,
        len(chunks),
        chunk_duration_ms / 1000,
    )
    return chunks


def _merge_chunk_results(results: list[tuple[int, dict]]) -> dict:
    """Merge per-chunk transcription dicts into one combined result.

    ``results`` is ``(offset_ms, chunk_result)`` for the chunks that
    transcribed successfully, in chunk order. Segment timestamps are shifted by
    each chunk's offset, text is space-joined, billed duration summed.

    Without combining the per-chunk languages, chunked Whisper would return
    ``language=None`` even when every chunk detected the same language —
    observed on an Arabic football video where 2 chunks said "arabic" but the
    combined result dropped the field, downstream defaulted to English, and
    translation never ran. Normalize at the boundary because Whisper returns a
    name ("chinese", "hebrew"); callers that truncate to 2 chars would produce
    invalid codes like "ch" (Chamorro). ``Counter.most_common`` is deterministic
    on ties — it preserves insertion order (dict insertion-ordered since 3.7).
    """
    all_text: list[str] = []
    all_segments: list[dict] = []
    detected_languages: list[str] = []
    total_duration = 0.0

    for offset_ms, result in results:
        all_text.append(result["text"])
        total_duration += float(result.get("duration") or 0.0)
        if result.get("language"):
            detected_languages.append(result["language"])

        offset_sec = offset_ms / 1000.0
        for seg in result.get("segments", []):
            all_segments.append(
                {
                    "text": seg.get("text", ""),
                    "start": seg.get("start", 0) + offset_sec,
                    "end": seg.get("end", 0) + offset_sec,
                }
            )

    combined_language = (
        normalize_language_code(Counter(detected_languages).most_common(1)[0][0])
        if detected_languages
        else None
    )

    return {
        "text": " ".join(all_text),
        "segments": all_segments,
        "language": combined_language,
        "duration": total_duration,
    }


async def _transcribe_chunks_parallel(
    chunks: list[tuple[Path, int]],
    is_music: bool = False,
    deadline: float | None = None,
) -> dict:
    """Transcribe audio chunks concurrently (bounded) and merge in chunk order.

    Each chunk is an independent OpenAI call dispatched via ``asyncio.to_thread``
    under a ``Semaphore(WHISPER_CHUNK_CONCURRENCY)`` — so a 2-chunk video runs in
    ~one chunk's wall-clock instead of two. ``asyncio.gather`` preserves input
    order, so merging by chunk offset stays correct.

    The first chunk always runs; a later chunk is skipped once ``deadline`` (a
    ``time.monotonic()`` value) has passed, preserving a partial-but-real
    transcript for very long videos — the same intent as the old sequential
    loop. A single hung chunk is bounded by the client's per-request timeout;
    failed chunks are dropped (``return_exceptions=True``) so one bad chunk never
    voids the whole transcript.

    Args:
        chunks: List of (chunk_path, offset_ms) from _split_audio_chunks
        is_music: If True, provide a lyrics-focused prompt hint per chunk
        deadline: Optional ``time.monotonic()`` cutoff for partial return

    Returns:
        Combined {"text": ..., "segments": [...]} matching single-file shape

    Raises:
        TranscriptError: If every chunk failed (none usable).
    """
    sem = asyncio.Semaphore(settings.WHISPER_CHUNK_CONCURRENCY)
    client = _make_openai_client()
    total = len(chunks)

    async def _run_chunk(index: int, chunk_path: Path, offset_ms: int) -> dict | None:
        async with sem:
            if deadline is not None and index > 0 and time.monotonic() >= deadline:
                logger.warning(
                    "Whisper deadline reached before chunk %d/%d; skipping (partial transcript)",
                    index + 1,
                    total,
                )
                return None
            logger.info(
                "Whisper chunk %d/%d starting (offset=%.0fs)",
                index + 1,
                total,
                offset_ms / 1000.0,
            )
            chunk_start = time.monotonic()
            result = await asyncio.to_thread(_transcribe_sync, chunk_path, is_music, client)
            logger.info(
                "Whisper chunk %d/%d done in %.1fs (%d chars)",
                index + 1,
                total,
                time.monotonic() - chunk_start,
                len(result["text"]),
            )
            return result

    outcomes = await asyncio.gather(
        *[_run_chunk(i, path, offset) for i, (path, offset) in enumerate(chunks)],
        return_exceptions=True,
    )

    merge_inputs: list[tuple[int, dict]] = []
    for (_, offset_ms), outcome in zip(chunks, outcomes):
        if isinstance(outcome, BaseException):
            logger.warning(
                "Whisper chunk at offset %.0fs failed, dropping: %s",
                offset_ms / 1000.0,
                outcome,
            )
            continue
        if outcome is None:
            continue  # skipped past deadline
        merge_inputs.append((offset_ms, outcome))

    if not merge_inputs:
        raise TranscriptError("All Whisper chunks failed", ErrorCode.UNKNOWN_ERROR)

    return _merge_chunk_results(merge_inputs)


def _create_estimated_segments(text: str) -> list[TranscriptSegment]:
    """
    Create estimated segments when Whisper doesn't return timing info.

    Estimates ~2.5 words per second for natural speech.

    Args:
        text: Full transcript text

    Returns:
        List of TranscriptSegment with estimated timestamps
    """
    words = text.split()
    segments = []
    words_per_segment = 30  # ~12 seconds of speech
    words_per_second = 2.5

    for i in range(0, len(words), words_per_segment):
        chunk = " ".join(words[i : i + words_per_segment])
        start_ms = int((i / words_per_second) * 1000)
        end_ms = int(((i + words_per_segment) / words_per_second) * 1000)
        segments.append(
            TranscriptSegment(
                text=chunk,
                startMs=start_ms,
                endMs=end_ms,
            )
        )

    return segments


def _translate_sync(audio_path: Path, client: OpenAI | None = None) -> dict:
    """Translate audio to English using Whisper translate API (synchronous).

    Uses client.audio.translations.create() which transcribes AND translates
    non-English audio to English in a single step.

    Returns:
        Dict with 'text' and 'segments' keys.
    """
    if not settings.OPENAI_API_KEY:
        raise TranscriptError("OpenAI API key not configured", ErrorCode.UNKNOWN_ERROR)

    if client is None:
        client = _make_openai_client()

    try:
        with open(audio_path, "rb") as f:
            response = client.audio.translations.create(
                model="whisper-1",
                file=f,
                response_format="verbose_json",
            )
        raw_segments = response.segments if hasattr(response, "segments") else []
        segments = [
            {
                "text": getattr(seg, "text", ""),
                "start": getattr(seg, "start", 0),
                "end": getattr(seg, "end", 0),
            }
            for seg in (raw_segments or [])
        ]
        return {
            "text": response.text,
            "segments": segments,
            "duration": _response_duration(response),
        }
    except Exception as e:
        logger.error("Whisper translation failed: %s", e)
        raise TranscriptError(f"Whisper translation failed: {e}", ErrorCode.UNKNOWN_ERROR)


async def translate_audio_to_english(
    video_id: str,
    cached_audio_path: Path | None = None,
) -> str | None:
    """Download audio and translate to English using Whisper translate API.

    Args:
        video_id: YouTube video ID.
        cached_audio_path: Optional pre-downloaded audio file to reuse.

    Returns English transcript text, or None on failure.
    Designed for non-English videos that need English text for Qdrant embedding.
    """
    audio_path: Path | None = None
    owns_audio = False
    try:
        # Check for cached audio from transcription phase first
        whisper_cached = _cached_audio_path(video_id)
        if cached_audio_path and cached_audio_path.exists():
            audio_path = cached_audio_path
        elif whisper_cached.exists():
            audio_path = whisper_cached
            owns_audio = True  # Clean up cache after use
            logger.info("Reusing cached Whisper audio for translation: %s", whisper_cached)
        else:
            audio_path = await asyncio.to_thread(_download_audio_sync, video_id)
            owns_audio = True

        result = await asyncio.to_thread(_translate_sync, audio_path)

        # The translate API billed by duration regardless of text content.
        emit_transcription_usage(
            provider="openai",
            model="whisper-1",
            feature="summarize:transcript:whisper_translate",
            audio_seconds=float(result.get("duration") or 0.0),
            success=True,
        )

        if not result.get("text"):
            logger.warning("Whisper translate returned empty text for %s", video_id)
            return None

        logger.info(
            "Whisper translate complete for %s: %d chars",
            video_id,
            len(result["text"]),
        )
        return result["text"]
    except Exception as e:
        logger.warning("Whisper translate failed for %s: %s", video_id, e)
        emit_transcription_usage(
            provider="openai",
            model="whisper-1",
            feature="summarize:transcript:whisper_translate",
            success=False,
        )
        return None
    finally:
        if owns_audio and audio_path and audio_path.exists():
            try:
                audio_path.unlink()
            except Exception:
                pass


async def transcribe_with_whisper(
    video_id: str,
    is_music: bool = False,
    deadline: float | None = None,
) -> NormalizedTranscript:
    """
    Full async workflow: download audio → transcribe → normalize.

    This is the main entry point for Whisper fallback. Files larger than
    CHUNK_TARGET_SIZE_MB are split into chunks before transcription.

    Args:
        video_id: YouTube video ID
        is_music: If True, provide a lyrics-focused prompt hint
        deadline: Optional ``time.monotonic()`` cutoff. When the chunked path
            crosses it, transcription returns the chunks finished so far rather
            than continuing — preserving a partial transcript for long videos.

    Returns:
        NormalizedTranscript with source="whisper"

    Raises:
        TranscriptError: If any step fails
    """
    audio_path: Path | None = None
    chunk_paths: list[Path] = []

    try:
        logger.info("Starting Whisper fallback for %s", video_id)

        # Download audio (blocking, run in thread)
        audio_path = await asyncio.to_thread(_download_audio_sync, video_id)

        # Check if chunking is needed
        file_size_mb = audio_path.stat().st_size / (1024 * 1024)

        if file_size_mb > CHUNK_TARGET_SIZE_MB:
            logger.info(
                "Audio %.1fMB exceeds %dMB, chunking",
                file_size_mb,
                CHUNK_TARGET_SIZE_MB,
            )
            try:
                chunks = await asyncio.to_thread(_split_audio_chunks, audio_path)
            except Exception as e:
                logger.error("Audio chunking failed for %s: %s", video_id, e)
                raise TranscriptError(
                    f"Audio chunking failed: {e}",
                    ErrorCode.UNKNOWN_ERROR,
                )
            chunk_paths = [path for path, _ in chunks]
            # Already async — it dispatches one thread per chunk internally and
            # bounds concurrency itself, so no outer to_thread wrapper here.
            result = await _transcribe_chunks_parallel(chunks, is_music, deadline)
        else:
            result = await asyncio.to_thread(_transcribe_sync, audio_path, is_music)

        # Build normalized segments
        if result.get("segments"):
            # Whisper returned segment data
            segments = [
                TranscriptSegment(
                    text=seg.get("text", "").strip(),
                    startMs=int(seg.get("start", 0) * 1000),
                    endMs=int(seg.get("end", 0) * 1000),
                )
                for seg in result["segments"]
                if seg.get("text", "").strip()
            ]
        else:
            # Fallback to estimated segments
            logger.warning("Whisper didn't return segments, using estimates")
            segments = _create_estimated_segments(result["text"])

        detected_language = result.get("language")
        logger.info(
            "Whisper fallback complete: %d chars, %d segments, language=%s",
            len(result["text"]),
            len(segments),
            detected_language,
        )

        # Cache audio for non-English so translate_audio_to_english can reuse it
        if detected_language and detected_language != "en" and audio_path and audio_path.exists():
            cached = _cached_audio_path(video_id)
            try:
                import shutil

                shutil.move(str(audio_path), str(cached))
                audio_path = None  # Prevent finally cleanup
                logger.debug("Cached audio for translation reuse: %s", cached)
            except Exception:
                pass  # Not critical — translate will re-download

        emit_transcription_usage(
            provider="openai",
            model="whisper-1",
            feature="summarize:transcript:whisper",
            audio_seconds=float(result.get("duration") or 0.0),
            success=True,
        )

        return NormalizedTranscript(
            text=result["text"],
            segments=segments,
            source="whisper",
            language=detected_language,
        )

    except Exception:
        emit_transcription_usage(
            provider="openai",
            model="whisper-1",
            feature="summarize:transcript:whisper",
            success=False,
        )
        raise
    finally:
        # Cleanup original audio file (skipped if moved to cache above)
        if audio_path and audio_path.exists():
            try:
                audio_path.unlink()
                logger.debug("Cleaned up audio file: %s", audio_path)
            except Exception as e:
                logger.warning("Failed to cleanup audio file: %s", e)
        # Cleanup chunk files
        for chunk_path in chunk_paths:
            if chunk_path.exists():
                try:
                    chunk_path.unlink()
                    logger.debug("Cleaned up chunk file: %s", chunk_path)
                except Exception as e:
                    logger.warning("Failed to cleanup chunk file: %s", e)
