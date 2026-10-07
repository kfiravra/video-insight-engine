"""Chapter-aware transcript splitting with fallback chain.

Splits long video transcripts into chapter-based chunks for batched extraction.
Fallback chain: YouTube chapters -> AI detection -> time-based split -> single chunk.

Chunk texts are extraction-prompt text: sliced from the segments and rendered
with absolute ``[m:ss]`` markers (``render_transcript``), so every batch reads
the same video clock as the single-call path.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ...config import prompts_from_disk, settings
from ...utils.json_parsing import parse_json_array_response, parse_json_response
from ...utils.llm_retry import call_llm_with_retry
from ...utils.transcript_slicer import segments_in_range, slice_transcript_for_chapter
from ...services.pipeline.pipeline_helpers import normalize_segments
from ...services.transcript.render import MARKER_PATTERN, marker_seconds, render_transcript

if TYPE_CHECKING:
    from ...services.llm import LLMService

logger = logging.getLogger(__name__)

# Process-lifetime cache of the chapter-detect prompt (registry mode only).
_CHAPTER_DETECT_PROMPT: str | None = None
_CHAPTER_DETECT_PATH = Path(__file__).parent.parent.parent / "prompts" / "chapter_detect.txt"


@dataclass
class ChapterChunk:
    """A chapter-sized transcript chunk for batched extraction."""

    index: int
    title: str
    start_seconds: float
    end_seconds: float
    text: str
    source: str  # "youtube" | "ai_detected" | "time_split" | "full"
    token_estimate: int  # approximate token count


def _estimate_tokens(text: str) -> int:
    """Estimate token count from text (~1.33 words per token for English)."""
    return max(1, int(len(text.split()) * 1.33))


def _chunk_text(segments: list[dict[str, Any]], start: float, end: float) -> str:
    """Prompt text of one chunk: the segments starting in [start, end), rendered
    with absolute ``[m:ss]`` markers (the chunk opens on its own marker)."""
    return render_transcript(segments_in_range(segments, int(start), int(end)))


def _snap_to_markers(chunks: list[ChapterChunk], duration_seconds: float) -> list[ChapterChunk]:
    """Read force-split chunk bounds from the ``[m:ss]`` markers in their text.

    Sentence splitting places bounds by word share, which disagrees with the
    absolute markers inside each chunk. A chunk that opens on a marker starts
    at it; one that opens mid-block starts at the last marker before it, so the
    ``=== CHAPTER …`` header range contains every marker of its chunk. Each
    chunk ends where the next starts, the last at the video's end. Unmarked
    text (metadata-only transcripts) keeps the proportional bounds.
    """
    if not any(MARKER_PATTERN.search(chunk.text) for chunk in chunks):
        return chunks
    carry = 0.0
    for i, chunk in enumerate(chunks):
        times = marker_seconds(chunk.text)
        opens_on_marker = MARKER_PATTERN.match(chunk.text.lstrip()) is not None
        chunk.start_seconds = float(times[0]) if i and opens_on_marker else carry
        if times:
            carry = float(times[-1])
    for chunk, following in zip(chunks, chunks[1:]):
        chunk.end_seconds = following.start_seconds
    chunks[-1].end_seconds = max(float(duration_seconds), carry)
    return chunks


# Pre-compiled regex for sentence splitting in force_split_by_sentences
_ABBREV_PLACEHOLDER = "\uffff"  # Unicode noncharacter — safe for JSON/DB unlike NUL
_ABBREV_RE = re.compile(r"\b(Mr|Mrs|Ms|Dr|Prof|Sr|Jr|vs|etc|approx|inc|avg)\.")
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")

FORCE_SPLIT_TARGET_WORDS = 5000  # target words per chunk


def force_split_by_sentences(
    transcript: str,
    duration_seconds: float,
    target_words: int = FORCE_SPLIT_TARGET_WORDS,
    target_chunks: int | None = None,
) -> list[ChapterChunk]:
    """Force-split transcript at sentence boundaries when chapter splitting fails.

    Creates synthetic ChapterChunk objects by splitting the transcript
    into roughly equal word-count segments, snapping to sentence endings
    to avoid breaking mid-sentence.

    When ``target_chunks`` is provided, ``target_words`` is overridden so the
    transcript is divided into approximately that many chunks. Used by
    extraction to align chunk count with parallel batch concurrency.
    """
    # Protect common abbreviations from being treated as sentence endings
    protected = _ABBREV_RE.sub(lambda m: m.group(1) + _ABBREV_PLACEHOLDER, transcript)
    sentences = _SENTENCE_SPLIT_RE.split(protected)
    sentences = [s.replace(_ABBREV_PLACEHOLDER, ".") for s in sentences]
    if not sentences:
        return []

    total_words = sum(len(s.split()) for s in sentences)
    if target_chunks is not None and target_chunks > 1:
        # Round up so we don't undershoot the requested chunk count.
        target_words = max(1, (total_words + target_chunks - 1) // target_chunks)
    if total_words <= target_words:
        return []  # no split needed

    num_chunks = max(2, (total_words + target_words - 1) // target_words)
    chunk_duration = duration_seconds / num_chunks if duration_seconds > 0 else 0

    chunks: list[ChapterChunk] = []
    current_sentences: list[str] = []
    current_word_count = 0
    chunk_idx = 0

    for sentence in sentences:
        sentence_words = len(sentence.split())
        current_sentences.append(sentence)
        current_word_count += sentence_words

        if current_word_count >= target_words:
            text = " ".join(current_sentences)
            chunks.append(
                ChapterChunk(
                    index=chunk_idx,
                    title=f"Part {chunk_idx + 1}",
                    start_seconds=chunk_idx * chunk_duration,
                    end_seconds=min((chunk_idx + 1) * chunk_duration, duration_seconds),
                    text=text,
                    source="force_split",
                    token_estimate=int(current_word_count * 1.33),
                )
            )
            chunk_idx += 1
            current_sentences = []
            current_word_count = 0

    # Append remaining sentences as the last chunk
    if current_sentences:
        text = " ".join(current_sentences)
        if chunks:
            if current_word_count < target_words // 3:
                # Merge small remainder into previous chunk
                prev = chunks[-1]
                chunks[-1] = ChapterChunk(
                    index=prev.index,
                    title=prev.title,
                    start_seconds=prev.start_seconds,
                    end_seconds=min((chunk_idx + 1) * chunk_duration, duration_seconds),
                    text=prev.text + " " + text,
                    source="force_split",
                    token_estimate=prev.token_estimate + int(current_word_count * 1.33),
                )
            else:
                chunks.append(
                    ChapterChunk(
                        index=chunk_idx,
                        title=f"Part {chunk_idx + 1}",
                        start_seconds=chunk_idx * chunk_duration,
                        end_seconds=duration_seconds,
                        text=text,
                        source="force_split",
                        token_estimate=int(current_word_count * 1.33),
                    )
                )
        else:
            return []

    return _snap_to_markers(chunks, duration_seconds) if len(chunks) >= 2 else []


async def split_transcript_into_chapters(
    video_data: dict[str, Any],
    segments: list[dict[str, Any]],
    transcript: str,
    llm_service: LLMService | None = None,
    description_chapters: list[dict[str, Any]] | None = None,
) -> list[ChapterChunk]:
    """Split transcript into chapter-based chunks using fallback chain.

    Fallback chain:
    1. YouTube creator chapters (video_data["chapters"])
    2. Author-description timestamp markers (description_chapters)
    3. AI chapter detection (fast model)
    4. Time-based splitting (~5 min segments)
    5. Single chunk (entire transcript)

    Args:
        video_data: Video metadata dict with optional "chapters", "duration", "title".
        segments: Transcript segments with startMs/endMs/text.
        transcript: Full transcript text (fallback when segments unavailable).
        llm_service: LLM service for AI chapter detection (optional).
        description_chapters: Timestamp markers parsed from the video description,
            each {"seconds": int, "label": str}. Used when the creator listed
            chapters in the description but YouTube has no native chapters.

    Returns:
        List of ChapterChunk, always at least 1.
    """
    duration = video_data.get("duration", 0)
    chapters = video_data.get("chapters")

    # Defensive: normalize segments to startMs/endMs format
    if segments:
        sample_keys = set(segments[0].keys()) if segments else set()
        if "start" in sample_keys and "startMs" not in sample_keys:
            logger.info(
                "Normalizing %d segments from start/duration to startMs/endMs", len(segments)
            )
            segments = normalize_segments(segments)

    max_minutes = settings.MAX_MINUTES_PER_BATCH

    # Path 1: YouTube chapters
    if chapters and len(chapters) >= 2:
        result = _from_youtube_chapters(chapters, segments, duration)
        if result:
            result = _subdivide_oversized_chapters(result, segments, max_minutes)
            logger.info("Chapter splitting: %d chunks from youtube", len(result))
            return result

    # Path 2: Author-description timestamp markers
    if description_chapters and len(description_chapters) >= 2 and (segments or transcript):
        result = _from_description_timestamps(description_chapters, segments, duration)
        if result:
            result = _subdivide_oversized_chapters(result, segments, max_minutes)
            logger.info("Chapter splitting: %d chunks from description", len(result))
            return result

    # Path 3: AI chapter detection
    if llm_service and duration > 0 and (segments or transcript):
        result = await _detect_chapters_with_ai(
            title=video_data.get("title", ""),
            description=video_data.get("description", ""),
            transcript=transcript,
            duration=duration,
            segments=segments,
            llm_service=llm_service,
        )
        if result:
            result = _subdivide_oversized_chapters(result, segments, max_minutes)
            logger.info("Chapter splitting: %d chunks from ai_detected", len(result))
            return result

    # Path 4: Time-based splitting
    if duration > 0 and segments:
        result = _time_split_chapters(duration, segments)
        if result and len(result) >= 2:
            logger.info("Chapter splitting: %d chapters from time_split", len(result))
            return result

    # Path 5: Single chunk fallback
    logger.info("Chapter splitting: 1 chapter from full (fallback)")
    return [
        ChapterChunk(
            index=0,
            title="Full Video",
            start_seconds=0,
            end_seconds=float(duration) if duration else 0,
            text=transcript,
            source="full",
            token_estimate=_estimate_tokens(transcript),
        )
    ]


def _from_youtube_chapters(
    chapters: list[dict[str, Any]],
    segments: list[dict[str, Any]],
    duration: float,
) -> list[ChapterChunk] | None:
    """Convert YouTube chapters (from yt-dlp) to ChapterChunks.

    yt-dlp format: {"title": str, "start_time": float, "end_time": float}
    """
    if not chapters or len(chapters) < 2:
        return None

    result: list[ChapterChunk] = []
    for i, ch in enumerate(chapters):
        # Support both dict (raw yt-dlp) and dataclass (Chapter) objects
        if isinstance(ch, dict):
            start = ch.get("start_time", 0)
            end = ch.get("end_time", duration)
            title = ch.get("title", f"Chapter {i + 1}")
        else:
            start = getattr(ch, "start_time", 0)
            end = getattr(ch, "end_time", duration)
            title = getattr(ch, "title", f"Chapter {i + 1}")

        text = _chunk_text(segments, start, end) if segments else ""

        if not text.strip():
            continue

        result.append(
            ChapterChunk(
                index=i,
                title=title,
                start_seconds=float(start),
                end_seconds=float(end),
                text=text,
                source="youtube",
                token_estimate=_estimate_tokens(text),
            )
        )

    return result if len(result) >= 2 else None


def _from_description_timestamps(
    description_chapters: list[dict[str, Any]],
    segments: list[dict[str, Any]],
    duration: float,
) -> list[ChapterChunk] | None:
    """Convert author-description timestamp markers to ChapterChunks.

    Each marker is {"seconds": int, "label": str}. Markers are sorted by
    start; each chapter's end is the next marker's start (the last ends at
    the video duration). The transcript is sliced per range. Returns None
    unless at least 2 non-empty chunks result.
    """
    markers = sorted(
        (m for m in description_chapters if m.get("seconds") is not None),
        key=lambda m: m["seconds"],
    )
    # Drop duplicate start offsets (keep first label seen).
    deduped: list[dict[str, Any]] = []
    for m in markers:
        if not deduped or m["seconds"] != deduped[-1]["seconds"]:
            deduped.append(m)
    if len(deduped) < 2:
        return None

    result: list[ChapterChunk] = []
    for i, marker in enumerate(deduped):
        start = float(marker["seconds"])
        end = float(deduped[i + 1]["seconds"]) if i + 1 < len(deduped) else float(duration)
        if end <= start:
            continue

        text = _chunk_text(segments, start, end) if segments else ""
        if not text.strip():
            continue

        result.append(
            ChapterChunk(
                index=i,
                title=str(marker.get("label", f"Chapter {i + 1}")),
                start_seconds=start,
                end_seconds=end,
                text=text,
                source="description",
                token_estimate=_estimate_tokens(text),
            )
        )

    return result if len(result) >= 2 else None


def _subdivide_oversized_chapters(
    chapters: list[ChapterChunk],
    segments: list[dict[str, Any]],
    max_minutes: float,
) -> list[ChapterChunk]:
    """Split any chapter whose span exceeds ``max_minutes`` into even sub-chunks.

    ``batch_chapters`` never splits a single chapter, so a chapter longer than the
    batch span cap would otherwise become one oversized batch that the fast model
    under-extracts (it front-loads and drops the tail — a 3h chapter produced
    timestamps only for its first ~40 min). Each oversized chapter is re-sliced
    from ``segments`` into ``ceil(span / max_minutes)`` windows. No-op without
    segments (can't re-slice accurately) or when every chapter already fits.
    """
    max_seconds = max_minutes * 60.0
    if not segments or max_seconds <= 0:
        return chapters

    result: list[ChapterChunk] = []
    for ch in chapters:
        span = ch.end_seconds - ch.start_seconds
        if span <= max_seconds:
            result.append(ch)
            continue

        parts = int((span + max_seconds - 1) // max_seconds)  # ceil division
        sub_dur = span / parts
        for k in range(parts):
            sub_start = ch.start_seconds + k * sub_dur
            sub_end = ch.end_seconds if k + 1 == parts else ch.start_seconds + (k + 1) * sub_dur
            text = _chunk_text(segments, sub_start, sub_end)
            if not text.strip():
                continue
            result.append(
                ChapterChunk(
                    index=0,  # re-indexed below
                    title=f"{ch.title} (part {k + 1})",
                    start_seconds=sub_start,
                    end_seconds=sub_end,
                    text=text,
                    source=ch.source,
                    token_estimate=_estimate_tokens(text),
                )
            )

    for i, ch in enumerate(result):
        ch.index = i
    return result


def _fmt_ts(seconds: float) -> str:
    """Format seconds as M:SS or H:MM:SS."""
    total = int(seconds)
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def _build_sampled_excerpts(
    segments: list[dict[str, Any]],
    duration: float,
    transcript: str,
    num_samples: int = 12,
    words_per_sample: int = 120,
) -> str:
    """Build evenly-spaced, timestamp-labelled excerpts spanning the video.

    Splits [0, duration] into ``num_samples`` windows and takes the first
    ``words_per_sample`` words of each window, prefixed with its real start
    timestamp ("[at H:MM:SS] ..."). This lets the LLM place chapter
    boundaries across the WHOLE video instead of guessing from only the
    first/last words. Falls back to word-position slicing when segments lack
    timestamps.
    """
    if duration <= 0 or num_samples < 1:
        return transcript[:4000]

    window = duration / num_samples
    words = transcript.split()
    excerpts: list[str] = []
    for i in range(num_samples):
        start = i * window
        end = (i + 1) * window if i + 1 < num_samples else duration

        if segments:
            text = slice_transcript_for_chapter(
                segments,
                start_seconds=int(start),
                end_seconds=int(end),
            )
        else:
            start_word = int((start / duration) * len(words))
            end_word = int((end / duration) * len(words))
            text = " ".join(words[start_word:end_word])

        excerpt = " ".join(text.split()[:words_per_sample]).strip()
        if excerpt:
            excerpts.append(f"[at {_fmt_ts(start)}] {excerpt}")

    return "\n\n".join(excerpts) if excerpts else transcript[:4000]


def _chapters_cover_duration(
    chunks: list[ChapterChunk],
    duration: float,
    tol: float = 0.05,
) -> bool:
    """True when chapters span ~the whole video (first ~0, last ~duration).

    Guards against the LLM clustering all chapters in the first portion of a
    long video — the failure mode that silently truncated coverage.
    """
    if not chunks or duration <= 0:
        return False
    first_start = min(c.start_seconds for c in chunks)
    last_end = max(c.end_seconds for c in chunks)
    return first_start <= duration * tol and last_end >= duration * (1 - tol)


def _chapter_detect_prompt() -> str | None:
    """The chapter-detect prompt; ``None`` when the file is missing.

    Registry mode caches it for the process lifetime. Disk mode
    (``PROMPT_SOURCE=disk``) re-loads on every call so a dev's .txt edit
    applies without a restart — and never touches the cache.
    """
    global _CHAPTER_DETECT_PROMPT
    from_disk = prompts_from_disk()
    if _CHAPTER_DETECT_PROMPT is not None and not from_disk:
        return _CHAPTER_DETECT_PROMPT
    if not _CHAPTER_DETECT_PATH.exists():
        logger.warning("chapter_detect.txt prompt not found, skipping AI detection")
        return None
    from src.services.pipeline.prompt_builder import load_prompt_text

    text = load_prompt_text(_CHAPTER_DETECT_PATH)
    if not from_disk:
        _CHAPTER_DETECT_PROMPT = text
    return text


async def _detect_chapters_with_ai(
    title: str,
    description: str,
    transcript: str,
    duration: float,
    segments: list[dict[str, Any]],
    llm_service: LLMService,
) -> list[ChapterChunk] | None:
    """Use fast LLM to detect chapter boundaries from transcript content."""
    chapter_prompt = _chapter_detect_prompt()
    if chapter_prompt is None:
        return None

    words = transcript.split()
    duration_min = round(duration / 60)
    samples = _build_sampled_excerpts(segments, duration, transcript)

    prompt = (
        chapter_prompt.replace("{title}", title)
        .replace("{description}", (description or "")[:500])
        .replace("{transcript_samples}", samples)
        .replace("{duration_minutes}", str(duration_min))
    )

    # Re-tag the feature so admin attribution shows chapter_detect cost
    # separately from extraction (outer phase sets summarize:extraction).
    from llm_common.context import llm_feature_var

    feature_token = llm_feature_var.set("summarize:chapter_detect")
    try:
        raw = await call_llm_with_retry(
            llm_service,
            prompt,
            max_tokens=3000,
            timeout=25.0,
            max_retries=1,
            stage_name="chapter_detect",
            use_fast_model=True,
            model_override=settings.get_stage_model("chapter_detect"),
        )
        if not raw:
            return None

        # The prompt asks for a bare JSON array; parse_json_array_response
        # handles that correctly. Fall back to {"chapters": [...]} for models
        # that wrap the array in an object.
        chapter_list = parse_json_array_response(raw)
        if not chapter_list:
            obj = parse_json_response(raw)
            if isinstance(obj, dict):
                chapter_list = obj.get("chapters", [])
        if not isinstance(chapter_list, list) or len(chapter_list) < 2:
            return None

        # Validate and build chunks
        result: list[ChapterChunk] = []
        for i, ch in enumerate(chapter_list):
            if not isinstance(ch, dict):
                continue
            start = float(ch.get("startSeconds", 0))
            end = float(ch.get("endSeconds", 0))
            ch_title = ch.get("title", f"Section {i + 1}")

            if end <= start:
                continue

            if segments:
                text = _chunk_text(segments, start, end)
            else:
                # Fallback: slice by word position
                total_dur = duration if duration > 0 else 1
                start_ratio = start / total_dur
                end_ratio = end / total_dur
                start_word = int(start_ratio * len(words))
                end_word = int(end_ratio * len(words))
                text = " ".join(words[start_word:end_word])

            if not text.strip():
                continue

            result.append(
                ChapterChunk(
                    index=i,
                    title=ch_title,
                    start_seconds=start,
                    end_seconds=end,
                    text=text,
                    source="ai_detected",
                    token_estimate=_estimate_tokens(text),
                )
            )

        if len(result) < 2:
            return None
        if not _chapters_cover_duration(result, duration):
            logger.warning(
                "AI chapters cover only %.0f-%.0fs of %.0fs video; "
                "discarding and falling through to time-split",
                min(c.start_seconds for c in result),
                max(c.end_seconds for c in result),
                duration,
            )
            return None
        return result

    except Exception as e:
        logger.warning("AI chapter detection failed (non-critical): %s", e)
        return None
    finally:
        llm_feature_var.reset(feature_token)


def _time_split_chapters(
    duration: float,
    segments: list[dict[str, Any]],
    target_minutes: float = 5.0,
) -> list[ChapterChunk]:
    """Split transcript into fixed-duration chunks (~5 minutes each).

    Args:
        duration: Video duration in seconds.
        segments: Transcript segments with startMs/endMs/text.
        target_minutes: Target chunk length in minutes.

    Returns:
        List of ChapterChunk with source="time_split".
    """
    target_seconds = target_minutes * 60
    num_chunks = max(2, round(duration / target_seconds))
    chunk_duration = duration / num_chunks

    if segments:
        sample = segments[0]
        logger.debug(
            "time_split: %d segments, sample keys=%s, first startMs=%s",
            len(segments),
            list(sample.keys()),
            sample.get("startMs", "MISSING"),
        )

    result: list[ChapterChunk] = []
    for i in range(num_chunks):
        start = i * chunk_duration
        end = min((i + 1) * chunk_duration, duration)

        text = _chunk_text(segments, start, end)

        if not text.strip():
            continue

        result.append(
            ChapterChunk(
                index=i,
                title=f"Part {i + 1}",
                start_seconds=start,
                end_seconds=end,
                text=text,
                source="time_split",
                token_estimate=_estimate_tokens(text),
            )
        )

    empty_count = num_chunks - len(result)
    if empty_count > 0:
        logger.warning(
            "time_split: %d/%d chunks were empty (segments may lack startMs)",
            empty_count,
            num_chunks,
        )

    return result
