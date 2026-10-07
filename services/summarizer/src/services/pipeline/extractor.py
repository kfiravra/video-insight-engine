"""Adaptive structured extraction — 1-N LLM calls based on transcript length and duration."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any, AsyncGenerator

from litellm.exceptions import RateLimitError, ServiceUnavailableError

from ...config import settings
from ...models.domain_types import validate_domain_output
from ...utils.json_parsing import parse_json_response, strip_markdown_fences
from ...utils.llm_retry import call_llm_with_retry
from .extraction_merger import merge_batch_extractions
from .prompt_builder import (
    build_extraction_template,
    build_tab_goals,
    get_content_emphasis,
    get_detail_level,
    load_prompt_text,
)
from .triage import TriageResult

if TYPE_CHECKING:
    from ...services.llm import LLMService
    from ...services.transcription.transcript_chunker import ChapterChunk

logger = logging.getLogger(__name__)
PROMPTS_DIR = Path(__file__).parent.parent.parent / "prompts"

# Word-count thresholds for adaptive strategy (~1.33 tokens per word for English)
SINGLE_THRESHOLD = 5333  # ~7K tokens → single call
OVERFLOW_THRESHOLD = 20000  # ~27K tokens → overflow extraction


def _dynamic_timeout(word_count: int) -> float:
    """Calculate dynamic timeout based on word count.

    Base 300s + word_count/100, capped at 600s. Longer transcripts need
    proportionally more time for the LLM to generate structured output.
    A 6K-word video gets ~360s, a 20K-word video gets ~500s.
    """
    return min(300.0 + word_count / 100.0, 600.0)


def _force_split_by_sentences(
    transcript: str,
    duration_seconds: float,
    target_chunks: int | None = None,
) -> list[ChapterChunk]:
    """Force-split transcript at sentence boundaries when chapter splitting fails.

    Delegates to transcript_chunker.force_split_by_sentences. When
    ``target_chunks`` is set, the transcript is divided into approximately
    that many chunks instead of fixed word-count buckets.
    """
    from ...services.transcription.transcript_chunker import force_split_by_sentences

    return force_split_by_sentences(transcript, duration_seconds, target_chunks=target_chunks)


def _estimate_tokens(text: str) -> int:
    """Estimate token count from text. ~1.33 tokens per word for English."""
    return int(len(text.split()) * 1.33)


def _load_prompt(path_str: str) -> str:
    """Registry-first prompt loader for the extractor.

    Delegates to :func:`load_prompt_text` so the Langfuse-registered version
    wins when available; falls back to disk via the cached file loader.
    Callers must pass resolved (absolute) paths.
    """
    return load_prompt_text(Path(path_str))


def _resolve_strategy(
    use_chunked: bool,
    chapters: list[ChapterChunk] | None,
    word_count: int,
    transcript: str,
    duration_seconds: float,
) -> tuple[str, list[ChapterChunk] | None, bool]:
    """Resolve extraction strategy.

    Returns ``(strategy_name, chunks_if_applicable, one_chunk_per_batch)``.
    The third element is True when the chunks were produced by force-split:
    in that case ``_chunked_extraction`` must run **one chunk per batch**
    instead of re-grouping via ``batch_chapters`` (which would re-collapse
    token-light force-split chunks into a single batch and defeat the
    parallelism the force-split was added to provide).

    Strategy values: ``"chunked"``, ``"single"``, ``"overflow"``.
    """
    target_chunks = settings.EXTRACTION_FORCE_SPLIT_CHUNKS

    if use_chunked:
        if chapters is None:
            raise ValueError("chapters required for chunked extraction")
        batches = batch_chapters(chapters)
        if len(batches) > 1:
            return "chunked", chapters, False
        if word_count <= SINGLE_THRESHOLD:
            return "single", None, False
        force_chunks = _force_split_by_sentences(
            transcript, duration_seconds, target_chunks=target_chunks
        )
        if len(force_chunks) >= 2:
            logger.info(
                "Single-batch chunked input force-split into %d chunks (%d words) — bypassing batch_chapters",
                len(force_chunks),
                word_count,
            )
            return "chunked", force_chunks, True
        logger.info("Force-split also yielded a single chunk — falling back to overflow")
        return "overflow", None, False

    if word_count > OVERFLOW_THRESHOLD:
        force_chunks = _force_split_by_sentences(
            transcript, duration_seconds, target_chunks=target_chunks
        )
        if len(force_chunks) >= 2:
            logger.info(
                "Force-splitting %d-word transcript into %d chunks (chapter splitting unavailable)",
                word_count,
                len(force_chunks),
            )
            return "chunked", force_chunks, True
        return "overflow", None, False

    if word_count < SINGLE_THRESHOLD:
        return "single", None, False

    return "overflow", None, False


async def extract(
    llm_service: LLMService,
    triage_result: TriageResult,
    transcript: str,
    video_data: dict,
    chapters: list[ChapterChunk] | None = None,
    video_context: str = "",
    extra_instruction: str = "",
    force_primary_model: bool = False,
    frame_context: str = "",
) -> AsyncGenerator[dict, None]:
    """Adaptive extraction yielding progress events and final result.

    Strategy selection:
    - chapters provided AND duration > 30 min: chunked extraction (batched by chapters)
    - <5.3K words (~4K tokens): single extraction call
    - 5.3-20K words (~4-15K tokens): single call + overflow retry if validation fails
    - 20K+ words: overflow extraction

    ``force_primary_model`` overrides ``EXTRACTION_USE_FAST_FIRST`` and is set
    to True by the synthesis-fed retry path, so the retry always escalates
    to the primary model regardless of the flag.
    """
    word_count = len(transcript.split())
    duration_seconds = video_data.get("duration", 0)
    duration_minutes = duration_seconds / 60
    logger.info(
        "Extraction: tags=%s, words=%d, duration=%.0fmin, chapters=%s",
        triage_result.content_tags,
        word_count,
        duration_minutes,
        len(chapters) if chapters else "none",
    )

    # Load quality rules (merged from accuracy_rules + voice rules)
    rules_path = PROMPTS_DIR / "quality_rules.txt"
    quality_rules = _load_prompt(str(rules_path.resolve())) if rules_path.exists() else ""

    title = video_data.get("title", "")
    duration_min = round(duration_seconds / 60)

    tab_goals_text = build_tab_goals(triage_result.tabs)
    detail_level = get_detail_level(duration_seconds)
    primary_tag = triage_result.primary_tag
    content_emphasis = get_content_emphasis(primary_tag)

    prompt_template = build_extraction_template(
        triage_result.content_tags,
        triage_result.modifiers,
        quality_rules,
        title,
        duration_min,
        user_goal=triage_result.user_goal,
        tab_goals=tab_goals_text,
        detail_level=detail_level,
        content_emphasis=content_emphasis,
        video_context=video_context,
        frame_context=frame_context,
    )

    if extra_instruction:
        # SECURITY: extra_instruction must only contain server-generated content
        # (from build_synthesis_fed_retry_prompt). Never pass user input here.
        prompt_template += f"\n<retry_guidance>\n{extra_instruction}\n</retry_guidance>"

    # Strategy selection: chunked for long videos with chapters
    use_chunked = (
        chapters is not None
        and len(chapters) >= 2
        and duration_seconds > settings.CHUNKED_EXTRACTION_THRESHOLD
    )

    strategy, resolved_chunks, one_chunk_per_batch = _resolve_strategy(
        use_chunked,
        chapters,
        word_count,
        transcript,
        duration_seconds,
    )

    use_fast_model = settings.EXTRACTION_USE_FAST_FIRST and not force_primary_model
    if force_primary_model and settings.EXTRACTION_USE_FAST_FIRST:
        logger.info("Extraction retry: force_primary_model=True, escalating to primary")

    if strategy == "chunked":
        if resolved_chunks is None:
            raise ValueError("resolved_chunks required for chunked strategy")
        async for event in _chunked_extraction(
            llm_service,
            triage_result,
            prompt_template,
            resolved_chunks,
            use_fast_model=use_fast_model,
            one_chunk_per_batch=one_chunk_per_batch,
        ):
            yield event
    elif strategy == "single":
        async for event in _single_extraction(
            llm_service,
            triage_result,
            prompt_template,
            transcript,
            use_fast_model=use_fast_model,
        ):
            yield event
    else:  # "overflow"
        overflow_text = (
            "\n".join(ch.text for ch in chapters) if use_chunked and chapters else transcript
        )
        async for event in _overflow_extraction(
            llm_service,
            triage_result,
            prompt_template,
            overflow_text,
            word_count=word_count,
            use_fast_model=use_fast_model,
        ):
            yield event


def _format_prompt(template: str, transcript: str, batch_context: str = "") -> str:
    """Format an extraction prompt with transcript and optional batch context.

    All other placeholders (title, quality_rules, duration_minutes,
    domain_schemas, user_goal, tab_goals) are already injected by
    build_extraction_template(). Only {transcript} and {batch_context}
    remain — both substituted per call so {batch_context} stays in the
    dynamic (uncached) portion of the prompt.
    """
    return template.replace("{batch_context}", batch_context).replace("{transcript}", transcript)


def _split_prompt_for_caching(
    template: str,
    transcript: str,
    batch_context: str = "",
) -> tuple[str, str]:
    """Split extraction prompt into static (cacheable) and dynamic parts.

    The template contains {transcript} and {batch_context} as remaining
    placeholders. Everything before ``<transcript>`` is static (schemas,
    rules, instructions); the transcript block is dynamic. Both
    placeholders live inside the dynamic portion so per-batch context
    never invalidates the cached prefix.

    Returns:
        (cache_static, dynamic_prompt) tuple. If splitting fails, returns
        ("", full_prompt) so caching is skipped gracefully.
    """
    marker = "<transcript>"
    idx = template.find(marker)
    if idx == -1:
        logger.debug(
            "Prompt caching skipped: <transcript> marker not found in template (len=%d)",
            len(template),
        )
        return "", _format_prompt(template, transcript, batch_context)

    static = template[: idx + len(marker)]
    dynamic_suffix = template[idx + len(marker) :]
    dynamic = dynamic_suffix.replace("{batch_context}", batch_context).replace(
        "{transcript}", transcript
    )
    return static, dynamic


def _build_batch_context(
    batch_idx: int,
    total_batches: int,
    batch: list[ChapterChunk],
    full_duration_seconds: float,
) -> str:
    """Build per-batch partial-extraction guidance.

    Returns an empty string when there is only one batch (single-call paths).
    Otherwise returns an XML block that overrides the base prompt's
    completeness/density rules for partial transcripts. This is the
    Phase 6.1+6.2 fix for the v6 retry burn: when the chunked path runs
    parallel batches over slices of the transcript, the model would
    otherwise see "108-min video missing steps" and either invent steps
    or return empty arrays — both of which trip the low-score retry gate.
    """
    if total_batches <= 1:
        return ""
    batch_seconds = sum(ch.end_seconds - ch.start_seconds for ch in batch)
    full_minutes = max(1, int(round(full_duration_seconds / 60)))
    batch_minutes = max(1, int(round(batch_seconds / 60)))
    return (
        "<batch_partial_context>\n"
        f"You are extracting from BATCH {batch_idx + 1} of {total_batches} parallel batches.\n"
        f"This segment covers ~{batch_minutes} of the full {full_minutes}-minute video.\n"
        "\n"
        "CRITICAL RULES FOR PARTIAL EXTRACTION:\n"
        "- Extract ONLY what THIS segment explicitly contains.\n"
        "- Return EMPTY arrays for fields not present in your segment — other batches cover them.\n"
        '- A downstream merger combines all batches; DO NOT pad fields to meet "no empty array" or density quotas.\n'
        "- The completeness/density rules in the base prompt apply to the FULL video's MERGED output, not your batch in isolation.\n"
        "- DO NOT infer absence: a field absent here may be covered by another batch.\n"
        "</batch_partial_context>\n"
    )


def _parse_llm_json(raw: str) -> dict:
    """Parse JSON from LLM response, stripping markdown fences first."""
    cleaned = strip_markdown_fences(raw)
    data = parse_json_response(cleaned)
    if not data:
        logger.error(
            "Failed to parse JSON from LLM response (len=%d): %.500s", len(raw), repr(raw[:500])
        )
        raise ValueError("Failed to parse JSON from LLM response")
    return data


# ---------------------------------------------------------------------------
# Single extraction (<5.3K words)
# ---------------------------------------------------------------------------


async def _single_extraction(
    llm_service: LLMService,
    triage_result: TriageResult,
    template: str,
    transcript: str,
    *,
    use_fast_model: bool = False,
) -> AsyncGenerator[dict, None]:
    """Single extraction call for short transcripts (<4K words)."""
    yield {"event": "extraction_progress", "section": "all", "percent": 10}

    cache_static, dynamic_prompt = _split_prompt_for_caching(template, transcript)

    yield {"event": "extraction_progress", "section": "all", "percent": 30}
    raw = await call_llm_with_retry(
        llm_service,
        dynamic_prompt,
        max_tokens=16384,
        timeout=240.0,
        max_retries=2,
        stage_name="extraction",
        json_mode=True,
        cache_static=cache_static or None,
        use_fast_model=use_fast_model,
        model_override=settings.get_stage_model("extraction"),
    )
    if not raw:
        raise ValueError("Extraction LLM call failed after retries")
    data = _parse_llm_json(raw)

    yield {"event": "extraction_progress", "section": "all", "percent": 80}
    validated = validate_domain_output(triage_result.content_tags, triage_result.modifiers, data)

    yield {"event": "extraction_progress", "section": "all", "percent": 100}
    yield {
        "event": "extraction_complete",
        "data": validated,
    }


# ---------------------------------------------------------------------------
# Overflow extraction (5.3-20K words)
# ---------------------------------------------------------------------------


async def _overflow_extraction(
    llm_service: LLMService,
    triage_result: TriageResult,
    template: str,
    transcript: str,
    word_count: int = 0,
    *,
    use_fast_model: bool = False,
) -> AsyncGenerator[dict, None]:
    """Overflow extraction for medium-long transcripts (5.3K+ words).

    Single call with dynamic timeout based on word count. JSON mode
    guarantees valid JSON — no retry/salvage needed.
    """
    timeout = _dynamic_timeout(word_count) if word_count else 240.0
    yield {"event": "extraction_progress", "section": "all", "percent": 10}

    cache_static, dynamic_prompt = _split_prompt_for_caching(template, transcript)

    yield {"event": "extraction_progress", "section": "all", "percent": 25}
    raw = await call_llm_with_retry(
        llm_service,
        dynamic_prompt,
        max_tokens=32768,
        timeout=timeout,
        max_retries=2,
        stage_name="extraction",
        json_mode=True,
        cache_static=cache_static or None,
        use_fast_model=use_fast_model,
        model_override=settings.get_stage_model("extraction"),
    )
    if not raw:
        raise ValueError("Extraction LLM call failed after retries")
    data = _parse_llm_json(raw)

    yield {"event": "extraction_progress", "section": "all", "percent": 80}
    validated = validate_domain_output(triage_result.content_tags, triage_result.modifiers, data)

    yield {"event": "extraction_progress", "section": "all", "percent": 100}
    yield {
        "event": "extraction_complete",
        "data": validated,
    }


# ---------------------------------------------------------------------------
# Chunked extraction (>30 min with chapters)
# ---------------------------------------------------------------------------


def batch_chapters(
    chapters: list[ChapterChunk],
    max_tokens_per_batch: int | None = None,
    max_minutes_per_batch: float | None = None,
) -> list[list[ChapterChunk]]:
    """Group chapters into batches that fit within token AND span limits.

    Groups chapters sequentially, closing the current batch when adding the
    next chapter would exceed either the token limit OR the wall-clock span
    limit. The span cap stops a token-light but multi-hour batch from being
    handed to the fast model in one call (which front-loads and drops the
    tail). Never splits a single chapter.

    Args:
        chapters: List of ChapterChunks to batch.
        max_tokens_per_batch: Max tokens per batch. Defaults to config value.
        max_minutes_per_batch: Max wall-clock span per batch. Defaults to config.

    Returns:
        List of batches, each a list of ChapterChunks.
    """
    token_limit = max_tokens_per_batch or settings.MAX_TOKENS_PER_BATCH
    minute_limit = max_minutes_per_batch or settings.MAX_MINUTES_PER_BATCH
    span_limit = minute_limit * 60.0
    batches: list[list[ChapterChunk]] = []
    current_batch: list[ChapterChunk] = []
    current_tokens = 0
    current_span = 0.0

    for chapter in chapters:
        ch_span = max(0.0, chapter.end_seconds - chapter.start_seconds)
        over_tokens = current_tokens + chapter.token_estimate > token_limit
        over_span = current_span + ch_span > span_limit
        if (over_tokens or over_span) and current_batch:
            batches.append(current_batch)
            current_batch = []
            current_tokens = 0
            current_span = 0.0
        current_batch.append(chapter)
        current_tokens += chapter.token_estimate
        current_span += ch_span

    if current_batch:
        batches.append(current_batch)

    return batches


def _format_time(seconds: float) -> str:
    """Format seconds as MM:SS or HH:MM:SS."""
    total = int(seconds)
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def _build_batch_transcript(batch: list[ChapterChunk]) -> str:
    """Build chapter-headed transcript text for a batch.

    Chunk texts carry absolute ``[m:ss]`` markers; each header's range is on the
    same clock and contains every marker of its chunk.
    """
    parts: list[str] = []
    for ch in batch:
        header = f"\n\n=== CHAPTER {ch.index + 1}: {ch.title} ({_format_time(ch.start_seconds)} - {_format_time(ch.end_seconds)}) ==="
        parts.append(header)
        parts.append(ch.text)
    return "\n".join(parts)


_RATE_LIMIT_BACKOFF_SECONDS = 2.0


def _percent_for_batch(completed: int, total: int) -> int:
    """Map (completed/total) into the 5-70% chunked-extraction band."""
    if total <= 0:
        return 5
    raw = int(5 + 65 * (completed / total))
    return max(5, min(70, raw))


async def _run_batch_extraction(
    llm_service: LLMService,
    template: str,
    batch: list[ChapterChunk],
    *,
    stage_name: str,
    use_fast_model: bool = False,
    batch_context: str = "",
) -> dict[str, Any] | None:
    """Run a single batch extraction. Returns parsed dict, or None on parse fail.

    ``propagate_rate_limit=True`` opts into the call-wrapper's terminal-error
    re-raise contract so ``RateLimitError`` / ``ServiceUnavailableError`` reach
    the parallel orchestrator and can be routed into the sequential fallback.

    ``batch_context`` is the optional per-batch partial-extraction guidance
    produced by ``_build_batch_context``. Empty for single-batch chunked
    paths; the chunked orchestrator fills it for multi-batch runs.
    """
    chapter_text = _build_batch_transcript(batch)
    cache_static, dynamic_prompt = _split_prompt_for_caching(
        template,
        chapter_text,
        batch_context=batch_context,
    )
    raw = await call_llm_with_retry(
        llm_service,
        dynamic_prompt,
        max_tokens=16384,
        timeout=300.0,
        max_retries=2,
        stage_name=stage_name,
        json_mode=True,
        cache_static=cache_static or None,
        use_fast_model=use_fast_model,
        propagate_rate_limit=True,
        model_override=settings.get_stage_model("extraction"),
    )
    if not raw:
        return None
    try:
        return _parse_llm_json(raw)
    except ValueError:
        logger.warning("[%s] JSON parse failed", stage_name)
        return None


async def _chunked_extraction(
    llm_service: LLMService,
    triage_result: TriageResult,
    template: str,
    chapters: list[ChapterChunk],
    *,
    use_fast_model: bool = False,
    one_chunk_per_batch: bool = False,
) -> AsyncGenerator[dict, None]:
    """Chunked extraction for long videos.

    Runs batches in parallel under EXTRACTION_PARALLEL_BATCHES; rate-limited
    batches fall back to a sequential second pass with backoff. Per-batch
    progress is streamed via an asyncio.Queue so subscribers see N/M ticks
    instead of a single 5% → 70% jump.

    ``one_chunk_per_batch`` is set by ``_resolve_strategy`` for the
    force-split path: the chunks were created precisely BECAUSE the natural
    batching collapsed to one. Re-running ``batch_chapters`` on them would
    re-collapse them under MAX_TOKENS_PER_BATCH, so each chunk gets its own
    batch instead.

    Each batch receives a ``batch_context`` block in its dynamic prompt
    portion telling the model that it sees only a partial transcript and
    must NOT pad fields to meet density quotas — the Phase 6 fix that
    keeps the merged extraction above the retry-trigger threshold.
    """
    if one_chunk_per_batch:
        batches: list[list[ChapterChunk]] = [[c] for c in chapters]
    else:
        batches = batch_chapters(chapters)
    num_batches = len(batches)
    full_duration_seconds = max((ch.end_seconds for ch in chapters), default=0.0)
    logger.info(
        "Chunked extraction: %d chapters in %d batches%s",
        len(chapters),
        num_batches,
        " (one chunk per batch — force-split bypass)" if one_chunk_per_batch else "",
    )

    # Initial kickoff event so the UI can render "Extracting batch 1/N…".
    yield {
        "event": "extraction_progress",
        "section": "chunked",
        "batch": 0,
        "of": num_batches,
        "percent": 5,
    }

    parallel_limit = max(1, min(settings.EXTRACTION_PARALLEL_BATCHES, num_batches))
    semaphore = asyncio.Semaphore(parallel_limit)
    batch_results: list[dict[str, Any] | None] = [None] * num_batches
    rate_limited: list[int] = []
    progress_queue: asyncio.Queue[dict | None] = asyncio.Queue()
    completed = 0

    async def process_batch(batch_idx: int, batch: list[ChapterChunk]) -> None:
        nonlocal completed
        async with semaphore:
            try:
                batch_results[batch_idx] = await _run_batch_extraction(
                    llm_service,
                    template,
                    batch,
                    stage_name=f"extraction_batch{batch_idx + 1}",
                    use_fast_model=use_fast_model,
                    batch_context=_build_batch_context(
                        batch_idx,
                        num_batches,
                        batch,
                        full_duration_seconds,
                    ),
                )
            except (RateLimitError, ServiceUnavailableError) as e:
                logger.warning(
                    "Batch %d rate-limited (%s); queued for sequential fallback",
                    batch_idx + 1,
                    type(e).__name__,
                )
                rate_limited.append(batch_idx)
            except Exception:
                # Unexpected failures (auth, programming bugs, etc.) must NOT
                # leave the progress UI hanging. Log with full traceback and
                # let the merger drop the missing slot.
                logger.exception("Batch %d failed unexpectedly", batch_idx + 1)
        completed += 1
        await progress_queue.put(
            {
                "event": "extraction_progress",
                "section": "chunked",
                "batch": completed,
                "of": num_batches,
                "percent": _percent_for_batch(completed, num_batches),
            }
        )

    tasks = [asyncio.create_task(process_batch(i, batch)) for i, batch in enumerate(batches)]

    async def _signal_done() -> None:
        results = await asyncio.gather(*tasks, return_exceptions=True)
        # process_batch already logs its own failures, but a defensive sweep
        # here catches anything that escaped (e.g., a CancelledError during
        # shutdown) so we never silently lose a diagnostic.
        for i, result in enumerate(results):
            if isinstance(result, BaseException):
                logger.error("Batch task %d crashed unhandled: %r", i + 1, result)
        await progress_queue.put(None)

    sentinel = asyncio.create_task(_signal_done())

    while True:
        event = await progress_queue.get()
        if event is None:
            break
        yield event

    await sentinel

    if rate_limited:
        logger.info(
            "Sequential fallback: re-running %d rate-limited batches with %.1fs backoff",
            len(rate_limited),
            _RATE_LIMIT_BACKOFF_SECONDS,
        )
        total_seq = len(rate_limited)
        for seq_idx, batch_idx in enumerate(rate_limited, start=1):
            await asyncio.sleep(_RATE_LIMIT_BACKOFF_SECONDS)
            try:
                batch_results[batch_idx] = await _run_batch_extraction(
                    llm_service,
                    template,
                    batches[batch_idx],
                    stage_name=f"extraction_batch{batch_idx + 1}_seq",
                    use_fast_model=use_fast_model,
                    batch_context=_build_batch_context(
                        batch_idx,
                        num_batches,
                        batches[batch_idx],
                        full_duration_seconds,
                    ),
                )
            except (RateLimitError, ServiceUnavailableError) as e:
                logger.warning(
                    "Batch %d still rate-limited on sequential retry (%s); skipping",
                    batch_idx + 1,
                    type(e).__name__,
                )
            # Spread sequential progress across the 70-85% band so the UI
            # advances per retry rather than appearing stuck at 70.
            seq_percent = 70 + int(15 * seq_idx / total_seq)
            yield {
                "event": "extraction_progress",
                "section": "chunked-sequential",
                "batch": batch_idx + 1,
                "of": num_batches,
                "percent": min(85, seq_percent),
            }

    successful = sum(1 for r in batch_results if r is not None)
    logger.info("Chunked extraction: %d/%d batches succeeded", successful, num_batches)

    if successful == 0:
        raise ValueError("All extraction batches failed")

    if successful < num_batches:
        # Surface dropped batches: a lost late batch silently truncates
        # coverage even though the run still "succeeds".
        logger.warning(
            "Chunked extraction dropped %d/%d batches — output may be incomplete",
            num_batches - successful,
            num_batches,
        )

    merged = merge_batch_extractions(batch_results, triage_result.content_tags)

    yield {"event": "extraction_progress", "section": "validation", "percent": 85}
    validated = validate_domain_output(triage_result.content_tags, triage_result.modifiers, merged)

    yield {"event": "extraction_progress", "section": "all", "percent": 100}
    yield {
        "event": "extraction_complete",
        "data": validated,
        "batches_total": num_batches,
        "batches_succeeded": successful,
    }
