"""Tests for chunked extraction — batch processing, routing, and integration."""

import pytest
from unittest.mock import AsyncMock, patch, MagicMock
import json

from litellm.exceptions import RateLimitError

from src.services.pipeline.extractor import (
    extract,
    batch_chapters,
    _build_batch_context,
    _build_batch_transcript,
    _chunked_extraction,
    _format_time,
    _percent_for_batch,
    _resolve_strategy,
    SINGLE_THRESHOLD,
)
from src.services.pipeline.triage import TriageResult
from src.services.transcription.transcript_chunker import ChapterChunk
from src.services.pipeline.extraction_prompt import ExtractionPrompt
from src.services.llm_messages import TextBlock

_PROMPT = ExtractionPrompt(
    system="rules",
    head="<transcript>\n{transcript}\n</transcript>",
    tail="<your_job>\n{batch_context}JOB\n</your_job>\n{visual_annotations}",
)


def _text(blocks: list[TextBlock]) -> str:
    """The user content a call sent, as one string."""
    return "".join(block["text"] for block in blocks)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_chapter(
    index: int, text: str = "Some transcript text here", token_estimate: int = 5000
) -> ChapterChunk:
    return ChapterChunk(
        index=index,
        title=f"Chapter {index + 1}",
        start_seconds=index * 300.0,
        end_seconds=(index + 1) * 300.0,
        text=text,
        source="youtube",
        token_estimate=token_estimate,
    )


def _make_triage(tags: list[str] | None = None) -> TriageResult:
    return TriageResult(
        content_tags=tags or ["learning"],
        modifiers=[],
        primary_tag=(tags or ["learning"])[0],
        user_goal="Test goal",
        tabs=[
            {"id": "key_points", "label": "Key Points", "component": "list_items", "goal": "test"}
        ],
        confidence=0.9,
    )


# ---------------------------------------------------------------------------
# batch_chapters
# ---------------------------------------------------------------------------


class TestBatchChapters:
    def test_single_batch_when_all_fit(self):
        chapters = [_make_chapter(i, token_estimate=5000) for i in range(6)]
        batches = batch_chapters(chapters, max_tokens_per_batch=50000)

        assert len(batches) == 1
        assert len(batches[0]) == 6

    def test_splits_into_multiple_batches(self):
        chapters = [_make_chapter(i, token_estimate=10000) for i in range(12)]
        batches = batch_chapters(chapters, max_tokens_per_batch=50000)

        assert len(batches) >= 2
        # All chapters accounted for
        total = sum(len(b) for b in batches)
        assert total == 12

    def test_never_splits_single_chapter(self):
        chapters = [_make_chapter(0, token_estimate=60000)]
        batches = batch_chapters(chapters, max_tokens_per_batch=50000)

        assert len(batches) == 1
        assert len(batches[0]) == 1

    def test_empty_chapters(self):
        batches = batch_chapters([], max_tokens_per_batch=50000)
        assert batches == []

    def test_each_batch_respects_token_limit(self):
        chapters = [_make_chapter(i, token_estimate=15000) for i in range(10)]
        batches = batch_chapters(chapters, max_tokens_per_batch=50000)

        for batch in batches:
            total_tokens = sum(ch.token_estimate for ch in batch)
            # Each batch should be at or near the limit (may exceed for single large chapters)
            assert total_tokens <= 60000  # 50K + one chapter overflow

    def test_preserves_chapter_order(self):
        chapters = [_make_chapter(i) for i in range(8)]
        batches = batch_chapters(chapters, max_tokens_per_batch=20000)

        flat = [ch for batch in batches for ch in batch]
        assert [ch.index for ch in flat] == list(range(8))


# ---------------------------------------------------------------------------
# _build_batch_transcript
# ---------------------------------------------------------------------------


class TestBuildBatchTranscript:
    def test_includes_chapter_headers(self):
        chapters = [
            ChapterChunk(0, "Intro", 0, 300, "First chapter text", "youtube", 100),
            ChapterChunk(1, "Main", 300, 600, "Second chapter text", "youtube", 100),
        ]
        result = _build_batch_transcript(chapters)

        assert "=== CHAPTER 1: Intro" in result
        assert "=== CHAPTER 2: Main" in result
        assert "First chapter text" in result
        assert "Second chapter text" in result

    def test_includes_timestamps(self):
        chapters = [ChapterChunk(0, "Test", 0, 3661, "text", "youtube", 100)]
        result = _build_batch_transcript(chapters)

        assert "0:00" in result
        assert "1:01:01" in result


# ---------------------------------------------------------------------------
# _format_time
# ---------------------------------------------------------------------------


class TestFormatTime:
    def test_minutes_seconds(self):
        assert _format_time(65) == "1:05"
        assert _format_time(0) == "0:00"
        assert _format_time(3599) == "59:59"

    def test_hours_minutes_seconds(self):
        assert _format_time(3600) == "1:00:00"
        assert _format_time(3661) == "1:01:01"
        assert _format_time(7200) == "2:00:00"


# ---------------------------------------------------------------------------
# extract() routing
# ---------------------------------------------------------------------------


class TestExtractRouting:
    @pytest.mark.asyncio
    async def test_short_video_uses_single_extraction(self):
        """Videos <30 min without chapters should use single/overflow extraction."""
        mock_llm = AsyncMock()
        mock_llm.model = "anthropic/claude-sonnet-4-6"
        mock_llm.fast_model = "anthropic/claude-haiku-4-5-20251001"

        triage = _make_triage()
        short_transcript = " ".join(["word"] * 1000)  # <5.3K words → single
        video_data = {"title": "Short Video", "duration": 600}  # 10 min

        extraction_data = {"key_points": [{"text": "test"}]}

        with patch(
            "src.services.pipeline.extractor.call_llm_with_retry",
            new_callable=AsyncMock,
            return_value=json.dumps(extraction_data),
        ):
            with patch(
                "src.services.pipeline.extractor.validate_domain_output",
                return_value=extraction_data,
            ):
                events = []
                async for evt in extract(mock_llm, triage, short_transcript, video_data):
                    events.append(evt)

        # Should have progress events + extraction_complete
        complete_events = [e for e in events if e["event"] == "extraction_complete"]
        assert len(complete_events) == 1
        assert complete_events[0]["data"] == extraction_data

    @pytest.mark.asyncio
    async def test_long_video_with_chapters_uses_chunked(self):
        """Videos >30 min with chapters should use chunked extraction."""
        mock_llm = AsyncMock()
        mock_llm.model = "anthropic/claude-sonnet-4-6"
        mock_llm.fast_model = "anthropic/claude-haiku-4-5-20251001"

        triage = _make_triage()
        transcript = " ".join(["word"] * 10000)
        video_data = {"title": "Long Video", "duration": 5400}  # 90 min
        chapters = [_make_chapter(i) for i in range(6)]

        extraction_data = {"key_points": [{"text": "test"}]}

        with patch(
            "src.services.pipeline.extractor.call_llm_with_retry",
            new_callable=AsyncMock,
            return_value=json.dumps(extraction_data),
        ):
            with patch(
                "src.services.pipeline.extractor.validate_domain_output",
                return_value=extraction_data,
            ):
                events = []
                async for evt in extract(
                    mock_llm, triage, transcript, video_data, chapters=chapters
                ):
                    events.append(evt)

        complete_events = [e for e in events if e["event"] == "extraction_complete"]
        assert len(complete_events) == 1

    @pytest.mark.asyncio
    async def test_long_video_without_chapters_uses_overflow(self):
        """Videos >30 min but no chapters should use overflow extraction."""
        mock_llm = AsyncMock()
        mock_llm.model = "anthropic/claude-sonnet-4-6"
        mock_llm.fast_model = "anthropic/claude-haiku-4-5-20251001"

        triage = _make_triage()
        transcript = " ".join(["word"] * 10000)
        video_data = {"title": "Long Video", "duration": 5400}  # 90 min

        extraction_data = {"key_points": [{"text": "test"}]}

        with patch(
            "src.services.pipeline.extractor.call_llm_with_retry",
            new_callable=AsyncMock,
            return_value=json.dumps(extraction_data),
        ):
            with patch(
                "src.services.pipeline.extractor.validate_domain_output",
                return_value=extraction_data,
            ):
                events = []
                async for evt in extract(mock_llm, triage, transcript, video_data, chapters=None):
                    events.append(evt)

        complete_events = [e for e in events if e["event"] == "extraction_complete"]
        assert len(complete_events) == 1


# ---------------------------------------------------------------------------
# _resolve_strategy — single-batch chunked detection + force-split fallback
# ---------------------------------------------------------------------------


def _make_sentence_transcript(words: int) -> str:
    out = []
    for i in range(words):
        out.append(f"w{i}")
        if (i + 1) % 10 == 0:
            out[-1] += "."
    return " ".join(out)


class TestResolveStrategy:
    """Validates the routing fix for the Matt-Pocock-style single-batch case."""

    def test_single_batch_chunked_with_long_transcript_force_splits(self):
        """The 108-min livestream regression: 22 chapters all fit under
        MAX_TOKENS_PER_BATCH → batch_chapters returns 1 batch. Old code
        fell to overflow (one $0.20 Sonnet call). New code force-splits
        AND signals one_chunk_per_batch=True so the chunks aren't
        re-collapsed downstream."""
        chapters = [_make_chapter(i, token_estimate=1000) for i in range(8)]
        transcript = _make_sentence_transcript(20_000)
        strategy, chunks, one_chunk_per_batch = _resolve_strategy(
            use_chunked=True,
            chapters=chapters,
            word_count=20_000,
            transcript=transcript,
            duration_seconds=6480,
        )
        assert strategy == "chunked"
        assert chunks is not None
        assert len(chunks) >= 2
        assert one_chunk_per_batch is True

    def test_single_batch_chunked_with_short_transcript_uses_single(self):
        """Genuinely small transcript with chapters → single, not force-split."""
        chapters = [_make_chapter(i, token_estimate=500) for i in range(2)]
        transcript = _make_sentence_transcript(SINGLE_THRESHOLD - 500)
        strategy, chunks, one_chunk_per_batch = _resolve_strategy(
            use_chunked=True,
            chapters=chapters,
            word_count=SINGLE_THRESHOLD - 500,
            transcript=transcript,
            duration_seconds=1800,
        )
        assert strategy == "single"
        assert chunks is None
        assert one_chunk_per_batch is False

    def test_force_split_target_chunks_aligned_with_config(self):
        """Force-split honors EXTRACTION_FORCE_SPLIT_CHUNKS (default 4)."""
        chapters = [_make_chapter(i, token_estimate=500) for i in range(4)]
        transcript = _make_sentence_transcript(20_000)
        _, chunks, _flag = _resolve_strategy(
            use_chunked=True,
            chapters=chapters,
            word_count=20_000,
            transcript=transcript,
            duration_seconds=6000,
        )
        assert chunks is not None
        # ~4 chunks (allow some slack for sentence-boundary snapping)
        assert 3 <= len(chunks) <= 6

    def test_multi_batch_chunked_does_not_set_one_chunk_per_batch(self):
        """When natural chapter batching already yields N batches, the flag
        stays False so batch_chapters keeps grouping for cost efficiency."""
        # 60K tokens worth of chapters → batch_chapters yields >1 batch
        chapters = [_make_chapter(i, token_estimate=20_000) for i in range(3)]
        strategy, chunks, one_chunk_per_batch = _resolve_strategy(
            use_chunked=True,
            chapters=chapters,
            word_count=50_000,
            transcript="",
            duration_seconds=3600,
        )
        assert strategy == "chunked"
        assert chunks is chapters
        assert one_chunk_per_batch is False


# ---------------------------------------------------------------------------
# _percent_for_batch — progress band clamping
# ---------------------------------------------------------------------------


class TestPercentForBatch:
    def test_first_batch_is_inside_band(self):
        assert 5 <= _percent_for_batch(1, 4) <= 70

    def test_last_batch_is_seventy(self):
        assert _percent_for_batch(4, 4) == 70

    def test_zero_total_returns_floor(self):
        assert _percent_for_batch(0, 0) == 5

    def test_clamped_when_overcounted(self):
        assert _percent_for_batch(10, 4) == 70


# ---------------------------------------------------------------------------
# _chunked_extraction — per-batch SSE + rate-limit fallback
# ---------------------------------------------------------------------------


class TestChunkedExtractionStreamingProgress:
    @pytest.mark.asyncio
    async def test_emits_per_batch_progress_events(self):
        """Each completed batch must produce one extraction_progress event
        carrying ``batch`` + ``of`` so the UI can render N/M ticks."""
        mock_llm = AsyncMock()
        mock_llm.model = "anthropic/claude-sonnet-4-6"
        mock_llm.fast_model = "anthropic/claude-haiku-4-5-20251001"

        triage = _make_triage()
        # Each chapter exceeds MAX_TOKENS_PER_BATCH (50K) → 1 chapter per batch.
        chapters = [_make_chapter(i, token_estimate=60_000) for i in range(4)]
        extraction_data = {"learning": {"keyPoints": [{"title": "A"}]}}

        with (
            patch(
                "src.services.pipeline.extractor.call_llm_with_retry",
                new_callable=AsyncMock,
                return_value=json.dumps(extraction_data),
            ),
            patch(
                "src.services.pipeline.extractor.validate_domain_output",
                return_value=extraction_data,
            ),
        ):
            events = []
            async for evt in _chunked_extraction(mock_llm, triage, _PROMPT, chapters):
                events.append(evt)

        progress = [
            e
            for e in events
            if e.get("event") == "extraction_progress" and e.get("section") == "chunked"
        ]
        # Initial kickoff (batch=0) + one per completed batch.
        assert progress[0]["batch"] == 0
        assert progress[0]["of"] == 4
        # At least the kickoff + 4 completion events.
        assert len(progress) >= 5
        # Last "chunked" event reaches 70%.
        assert progress[-1]["percent"] == 70

    @pytest.mark.asyncio
    async def test_extraction_complete_carries_batch_counts(self):
        """extraction_complete must report batches_total + batches_succeeded so
        the phase can surface dropped batches in the coverage metric."""
        mock_llm = AsyncMock()
        mock_llm.model = "anthropic/claude-sonnet-4-6"
        mock_llm.fast_model = "anthropic/claude-haiku-4-5-20251001"

        triage = _make_triage()
        chapters = [_make_chapter(i, token_estimate=60_000) for i in range(3)]
        extraction_data = {"learning": {"keyPoints": [{"title": "A"}]}}

        with (
            patch(
                "src.services.pipeline.extractor.call_llm_with_retry",
                new_callable=AsyncMock,
                return_value=json.dumps(extraction_data),
            ),
            patch(
                "src.services.pipeline.extractor.validate_domain_output",
                return_value=extraction_data,
            ),
        ):
            events = []
            async for evt in _chunked_extraction(mock_llm, triage, _PROMPT, chapters):
                events.append(evt)

        complete = [e for e in events if e["event"] == "extraction_complete"][0]
        assert complete["batches_total"] == 3
        assert complete["batches_succeeded"] == 3

    @pytest.mark.asyncio
    async def test_dropped_batch_warns_and_reports_shortfall(self):
        """A batch that returns no parseable data is dropped — the run still
        completes but logs a warning and reports the shortfall."""
        mock_llm = AsyncMock()
        mock_llm.model = "anthropic/claude-sonnet-4-6"
        mock_llm.fast_model = "anthropic/claude-haiku-4-5-20251001"

        triage = _make_triage()
        chapters = [_make_chapter(i, token_estimate=60_000) for i in range(3)]
        ok = json.dumps({"learning": {"keyPoints": [{"title": "OK"}]}})

        # Batch index 1 returns empty → _run_batch_extraction yields None (dropped).
        results = [ok, "", ok]

        async def fake_call(*_args, **_kwargs):
            return results.pop(0)

        with (
            patch(
                "src.services.pipeline.extractor.call_llm_with_retry",
                new=fake_call,
            ),
            patch(
                "src.services.pipeline.extractor.validate_domain_output",
                return_value={"learning": {"keyPoints": []}},
            ),
            patch(
                "src.services.pipeline.extractor.logger.warning",
            ) as mock_warn,
        ):
            events = []
            async for evt in _chunked_extraction(mock_llm, triage, _PROMPT, chapters):
                events.append(evt)

        complete = [e for e in events if e["event"] == "extraction_complete"][0]
        assert complete["batches_total"] == 3
        assert complete["batches_succeeded"] == 2
        # The dropped batch must surface a warning.
        assert any("dropped" in str(c.args[0]).lower() for c in mock_warn.call_args_list)

    @pytest.mark.asyncio
    async def test_rate_limited_batch_runs_in_sequential_fallback(self):
        """A 429 on one parallel batch must NOT abort the whole extraction —
        the batch is queued for a sequential second pass with backoff."""
        mock_llm = AsyncMock()
        mock_llm.model = "anthropic/claude-sonnet-4-6"
        mock_llm.fast_model = "anthropic/claude-haiku-4-5-20251001"

        triage = _make_triage()
        # Each chapter > MAX_TOKENS_PER_BATCH so each becomes its own batch.
        chapters = [_make_chapter(i, token_estimate=60_000) for i in range(3)]

        ok_payload = json.dumps({"learning": {"keyPoints": [{"title": "OK"}]}})

        # First three calls: batches 0-2 in parallel. Batch 1 raises 429.
        # Fourth call: sequential retry of batch 1 succeeds.
        call_results = [
            ok_payload,
            RateLimitError(
                message="429", model="anthropic/claude-sonnet-4-6", llm_provider="anthropic"
            ),
            ok_payload,
            ok_payload,  # sequential retry
        ]

        async def fake_call(*_args, **_kwargs):
            outcome = call_results.pop(0)
            if isinstance(outcome, RateLimitError):
                raise outcome
            return outcome

        with (
            patch(
                "src.services.pipeline.extractor.call_llm_with_retry",
                new=fake_call,
            ),
            patch(
                "src.services.pipeline.extractor.validate_domain_output",
                return_value={"learning": {"keyPoints": []}},
            ),
            patch(
                "src.services.pipeline.extractor._RATE_LIMIT_BACKOFF_SECONDS",
                0.0,
            ),
        ):
            events = []
            async for evt in _chunked_extraction(mock_llm, triage, _PROMPT, chapters):
                events.append(evt)

        # The sequential fallback must have fired with the dedicated section name
        # so the frontend can show "(running sequentially due to upstream load)".
        seq_events = [e for e in events if e.get("section") == "chunked-sequential"]
        assert len(seq_events) == 1
        assert seq_events[0]["batch"] == 2  # 1-indexed → batch idx 1 surfaces as batch=2

        # Extraction still completes successfully.
        complete = [e for e in events if e["event"] == "extraction_complete"]
        assert len(complete) == 1

    @pytest.mark.asyncio
    async def test_parallel_limit_honors_config(self):
        """EXTRACTION_PARALLEL_BATCHES=1 must serialize batches; a higher
        setting must allow more concurrency. We assert the *limit*, not the
        literal call count, to keep the test deterministic."""
        mock_llm = AsyncMock()
        mock_llm.model = "anthropic/claude-sonnet-4-6"
        mock_llm.fast_model = "anthropic/claude-haiku-4-5-20251001"

        import asyncio as _asyncio

        triage = _make_triage()
        # Each chapter > MAX_TOKENS_PER_BATCH so each batch is one chapter.
        chapters = [_make_chapter(i, token_estimate=60_000) for i in range(4)]

        active = 0
        max_active = 0

        async def slow_call(*_a, **_kw):
            nonlocal active, max_active
            active += 1
            max_active = max(max_active, active)
            await _asyncio.sleep(0.01)
            active -= 1
            return json.dumps({"learning": {"keyPoints": []}})

        with (
            patch(
                "src.services.pipeline.extractor.call_llm_with_retry",
                new=slow_call,
            ),
            patch(
                "src.services.pipeline.extractor.validate_domain_output",
                return_value={"learning": {"keyPoints": []}},
            ),
            patch(
                "src.services.pipeline.extractor.settings.EXTRACTION_PARALLEL_BATCHES",
                1,
            ),
        ):
            async for _ in _chunked_extraction(mock_llm, triage, _PROMPT, chapters):
                pass

        assert max_active == 1, f"Sequential mode should peak at 1, peaked at {max_active}"

    @pytest.mark.asyncio
    async def test_unexpected_exception_does_not_stall_progress(self):
        """A non-rate-limit exception in one batch must NOT prevent the
        progress event from firing for that slot — otherwise the UI bar
        gets stuck mid-extraction. We assert that all N batches eventually
        emit a progress event regardless of which ones blew up."""
        mock_llm = AsyncMock()
        mock_llm.model = "anthropic/claude-sonnet-4-6"
        mock_llm.fast_model = "anthropic/claude-haiku-4-5-20251001"

        triage = _make_triage()
        chapters = [_make_chapter(i, token_estimate=60_000) for i in range(3)]

        ok_payload = json.dumps({"learning": {"keyPoints": [{"title": "OK"}]}})
        # Batch 1 raises a generic RuntimeError (e.g., a programming bug).
        call_results = [ok_payload, RuntimeError("unexpected"), ok_payload]

        async def fake_call(*_args, **_kwargs):
            outcome = call_results.pop(0)
            if isinstance(outcome, BaseException):
                raise outcome
            return outcome

        with (
            patch(
                "src.services.pipeline.extractor.call_llm_with_retry",
                new=fake_call,
            ),
            patch(
                "src.services.pipeline.extractor.validate_domain_output",
                return_value={"learning": {"keyPoints": []}},
            ),
        ):
            events = []
            async for evt in _chunked_extraction(mock_llm, triage, _PROMPT, chapters):
                events.append(evt)

        # All three slots must have fired their per-batch progress event so
        # the UI never appears stuck. Plus the kickoff (batch=0).
        chunked_progress = [
            e
            for e in events
            if e.get("event") == "extraction_progress" and e.get("section") == "chunked"
        ]
        completed_batches = [e["batch"] for e in chunked_progress if e["batch"] > 0]
        assert sorted(completed_batches) == [1, 2, 3]

        # Extraction still completes from the surviving batches.
        complete = [e for e in events if e["event"] == "extraction_complete"]
        assert len(complete) == 1

    @pytest.mark.asyncio
    async def test_sequential_fallback_walks_progress_band(self):
        """Multi-batch sequential retries must spread across 70-85% so the
        UI advances per retry rather than appearing stuck at 70."""
        mock_llm = AsyncMock()
        mock_llm.model = "anthropic/claude-sonnet-4-6"
        mock_llm.fast_model = "anthropic/claude-haiku-4-5-20251001"

        triage = _make_triage()
        chapters = [_make_chapter(i, token_estimate=60_000) for i in range(4)]

        ok_payload = json.dumps({"learning": {"keyPoints": [{"title": "OK"}]}})
        # 4 parallel calls (2 fail), then 2 sequential retries succeed.
        call_results = [
            ok_payload,
            RateLimitError(
                message="429", model="anthropic/claude-sonnet-4-6", llm_provider="anthropic"
            ),
            ok_payload,
            RateLimitError(
                message="429", model="anthropic/claude-sonnet-4-6", llm_provider="anthropic"
            ),
            ok_payload,  # sequential retry for batch idx 1
            ok_payload,  # sequential retry for batch idx 3
        ]

        async def fake_call(*_args, **_kwargs):
            outcome = call_results.pop(0)
            if isinstance(outcome, BaseException):
                raise outcome
            return outcome

        with (
            patch(
                "src.services.pipeline.extractor.call_llm_with_retry",
                new=fake_call,
            ),
            patch(
                "src.services.pipeline.extractor.validate_domain_output",
                return_value={"learning": {"keyPoints": []}},
            ),
            patch(
                "src.services.pipeline.extractor._RATE_LIMIT_BACKOFF_SECONDS",
                0.0,
            ),
        ):
            events = []
            async for evt in _chunked_extraction(mock_llm, triage, _PROMPT, chapters):
                events.append(evt)

        seq_events = [e for e in events if e.get("section") == "chunked-sequential"]
        assert len(seq_events) == 2
        # First retry should sit below 85; second should land at 85.
        assert 70 < seq_events[0]["percent"] < 85
        assert seq_events[1]["percent"] == 85
        # And monotonic non-decreasing within the band.
        assert seq_events[0]["percent"] <= seq_events[1]["percent"]


# ---------------------------------------------------------------------------
# Phase 6 — batch-aware extraction prompt
# ---------------------------------------------------------------------------


class TestBuildBatchContext:
    """``_build_batch_context`` produces the partial-extraction guidance block."""

    def test_empty_for_single_batch(self):
        """Single-batch runs (single/overflow paths) must get an empty
        string so the prompt renders cleanly with no partial-context note."""
        batch = [_make_chapter(0)]
        assert _build_batch_context(0, 1, batch, full_duration_seconds=600.0) == ""

    def test_empty_for_zero_batches(self):
        assert _build_batch_context(0, 0, [], full_duration_seconds=0.0) == ""

    def test_includes_batch_indicator_for_multi_batch(self):
        batch = [_make_chapter(0)]
        ctx = _build_batch_context(0, 4, batch, full_duration_seconds=6000.0)
        assert "BATCH 1 of 4" in ctx
        assert "<batch_partial_context>" in ctx
        assert "</batch_partial_context>" in ctx

    def test_tells_batch_to_return_empty_arrays_for_fields_outside_its_part(self):
        """Empty arrays are expected for fields this slice does not cover."""
        ctx = _build_batch_context(1, 4, [_make_chapter(0)], full_duration_seconds=6000.0)
        assert "Return empty arrays for fields this part does not cover" in ctx

    def test_tells_batch_not_to_pad_toward_whole_video_counts(self):
        """Briefs/expect/caps describe the whole video — a slice must not pad toward them."""
        ctx = _build_batch_context(1, 4, [_make_chapter(0)], full_duration_seconds=6000.0)
        assert "never pad toward a count" in ctx
        assert "a merger combines every batch's output" in ctx

    def test_computes_batch_minutes_from_chapter_range(self):
        """Each ChapterChunk has start/end seconds — the helper sums the
        slice durations so the model knows what fraction of the video it
        is seeing."""
        # 2 chapters covering 0-300s and 300-600s → 10-min slice
        batch = [_make_chapter(0), _make_chapter(1)]
        ctx = _build_batch_context(0, 4, batch, full_duration_seconds=6000.0)
        # 0..600s = 10 min of a 100-min full video
        assert "~10 minutes (0:00–10:00) of the full 100-minute" in ctx


class TestChunkedExtractionInjectsBatchContext:
    """The chunked extraction orchestrator must pass per-batch context to
    each batch's LLM call so the model treats its input as partial."""

    @pytest.mark.asyncio
    async def test_each_parallel_batch_receives_partial_context(self):
        mock_llm = AsyncMock()
        mock_llm.model = "anthropic/claude-sonnet-4-6"
        mock_llm.fast_model = "anthropic/claude-haiku-4-5-20251001"

        triage = _make_triage()
        # Each chapter > MAX_TOKENS_PER_BATCH → 1 chapter per batch.
        chapters = [_make_chapter(i, token_estimate=60_000) for i in range(3)]
        extraction_data = {"learning": {"keyPoints": []}}

        # The prompt's job tail carries {batch_context}: verify the per-batch
        # partial-context block reaches call_llm_with_retry.
        captured_prompts: list[str] = []

        async def capture_call(_llm_service, prompt, *_args, **_kwargs):
            captured_prompts.append(_text(prompt))
            return json.dumps(extraction_data)

        with (
            patch(
                "src.services.pipeline.extractor.call_llm_with_retry",
                new=capture_call,
            ),
            patch(
                "src.services.pipeline.extractor.validate_domain_output",
                return_value=extraction_data,
            ),
        ):
            async for _ in _chunked_extraction(mock_llm, triage, _PROMPT, chapters):
                pass

        assert len(captured_prompts) == 3, "expected one LLM call per batch"
        for idx, prompt in enumerate(captured_prompts, start=1):
            assert "<batch_partial_context>" in prompt, (
                f"batch {idx} prompt missing partial-context block"
            )
            assert f"BATCH {idx} of 3" in prompt, f"batch {idx} prompt missing batch indicator"
            assert "Return empty arrays" in prompt, (
                f"batch {idx} prompt missing override of density rule"
            )

    @pytest.mark.asyncio
    async def test_sequential_fallback_also_carries_batch_context(self):
        """When a parallel batch hits a rate limit and gets re-run sequentially,
        the second attempt must keep the partial-context block — otherwise
        the retry would inherit the broken assumption that the model sees
        the full video."""
        mock_llm = AsyncMock()
        mock_llm.model = "anthropic/claude-sonnet-4-6"
        mock_llm.fast_model = "anthropic/claude-haiku-4-5-20251001"

        triage = _make_triage()
        chapters = [_make_chapter(i, token_estimate=60_000) for i in range(2)]

        ok = json.dumps({"learning": {"keyPoints": []}})
        # Batch 0 succeeds, batch 1 raises 429 in parallel pass then succeeds
        # on sequential retry. Capture the SEQUENTIAL call's prompt.
        captured_seq_prompt: list[str] = []
        call_count = {"n": 0}

        async def fake_call(_llm_service, prompt, *_args, **kwargs):
            call_count["n"] += 1
            if call_count["n"] == 2:  # parallel batch 1
                raise RateLimitError(
                    message="429",
                    model="anthropic/claude-sonnet-4-6",
                    llm_provider="anthropic",
                )
            stage_name = kwargs.get("stage_name", "")
            if stage_name.endswith("_seq"):
                captured_seq_prompt.append(_text(prompt))
            return ok

        with (
            patch(
                "src.services.pipeline.extractor.call_llm_with_retry",
                new=fake_call,
            ),
            patch(
                "src.services.pipeline.extractor.validate_domain_output",
                return_value={"learning": {"keyPoints": []}},
            ),
            patch(
                "src.services.pipeline.extractor._RATE_LIMIT_BACKOFF_SECONDS",
                0.0,
            ),
        ):
            async for _ in _chunked_extraction(mock_llm, triage, _PROMPT, chapters):
                pass

        assert len(captured_seq_prompt) == 1, "sequential fallback must run once"
        assert "<batch_partial_context>" in captured_seq_prompt[0]
        assert "BATCH 2 of 2" in captured_seq_prompt[0]
