"""Tests for transcript_chunker — chapter splitting with fallback chain."""

import pytest
from unittest.mock import AsyncMock, patch

from src.services.transcript.render import marker_seconds, render_transcript
from src.config import settings
from src.services.transcription import transcript_chunker as chunker_module
from src.services.transcription.transcript_chunker import (
    ChapterChunk,
    _build_sampled_excerpts,
    _chapters_cover_duration,
    _from_memory_outline,
    _from_timestamp_markers,
    _from_youtube_chapters,
    _subdivide_oversized_chapters,
    _time_split_chapters,
    force_split_by_sentences,
    needs_chapter_batching,
    split_transcript_into_chapters,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_segments(duration_seconds: int, words_per_second: float = 2.5) -> list[dict]:
    """Create mock transcript segments spanning the given duration."""
    segments = []
    interval_ms = 5000  # 5-second segments
    for start_ms in range(0, duration_seconds * 1000, interval_ms):
        end_ms = min(start_ms + interval_ms, duration_seconds * 1000)
        word_count = int(words_per_second * (interval_ms / 1000))
        text = " ".join(f"word{i}" for i in range(word_count))
        segments.append(
            {
                "text": text,
                "startMs": start_ms,
                "endMs": end_ms,
            }
        )
    return segments


def _make_youtube_chapters(count: int, duration: float) -> list[dict]:
    """Create mock YouTube chapters evenly distributed."""
    chapter_duration = duration / count
    return [
        {
            "title": f"Chapter {i + 1}",
            "start_time": i * chapter_duration,
            "end_time": (i + 1) * chapter_duration,
        }
        for i in range(count)
    ]


# ---------------------------------------------------------------------------
# YouTube chapters path
# ---------------------------------------------------------------------------


class TestFromYouTubeChapters:
    def test_converts_youtube_chapters_to_chunks(self):
        duration = 2700  # 45 min
        segments = _make_segments(duration)
        chapters = _make_youtube_chapters(6, duration)

        result = _from_youtube_chapters(chapters, segments, duration)

        assert result is not None
        assert len(result) == 6
        assert all(ch.source == "youtube" for ch in result)
        assert result[0].title == "Chapter 1"
        assert result[0].start_seconds == 0
        assert result[0].index == 0
        assert result[-1].end_seconds == duration

    def test_returns_none_for_single_chapter(self):
        duration = 2700
        segments = _make_segments(duration)
        chapters = [{"title": "Only One", "start_time": 0, "end_time": duration}]

        result = _from_youtube_chapters(chapters, segments, duration)

        assert result is None

    def test_returns_none_for_empty_chapters(self):
        result = _from_youtube_chapters([], [], 2700)
        assert result is None

    def test_skips_chapters_with_no_text(self):
        # Segments only cover first 100 seconds
        segments = _make_segments(100)
        chapters = [
            {"title": "Has text", "start_time": 0, "end_time": 50},
            {"title": "Has text too", "start_time": 50, "end_time": 100},
            {"title": "No text", "start_time": 500, "end_time": 600},
        ]

        result = _from_youtube_chapters(chapters, segments, 600)

        assert result is not None
        assert len(result) == 2

    def test_token_estimate_is_positive(self):
        duration = 2700
        segments = _make_segments(duration)
        chapters = _make_youtube_chapters(4, duration)

        result = _from_youtube_chapters(chapters, segments, duration)

        assert result is not None
        for ch in result:
            assert ch.token_estimate > 0


# ---------------------------------------------------------------------------
# Time-based splitting
# ---------------------------------------------------------------------------


class TestTimeSplitChapters:
    def test_splits_45min_video_into_chunks(self):
        duration = 2700  # 45 min
        segments = _make_segments(duration)

        result = _time_split_chapters(duration, segments)

        assert len(result) >= 2
        assert all(ch.source == "time_split" for ch in result)
        assert result[0].start_seconds == 0
        # All titled "Part N"
        assert result[0].title == "Part 1"
        assert result[-1].title == f"Part {len(result)}"

    def test_splits_3hour_video(self):
        duration = 10800  # 3 hours
        segments = _make_segments(duration)

        result = _time_split_chapters(duration, segments, target_minutes=5)

        # ~36 chunks of 5 min each
        assert len(result) >= 30
        assert len(result) <= 40

    def test_short_video_gets_minimum_2_chunks(self):
        duration = 300  # 5 min
        segments = _make_segments(duration)

        result = _time_split_chapters(duration, segments, target_minutes=5)

        assert len(result) >= 2

    def test_returns_empty_for_no_segments(self):
        result = _time_split_chapters(2700, [])
        assert len(result) == 0

    def test_custom_target_minutes(self):
        duration = 3600  # 1 hour
        segments = _make_segments(duration)

        result = _time_split_chapters(duration, segments, target_minutes=10)

        # ~6 chunks of 10 min
        assert 4 <= len(result) <= 8


# ---------------------------------------------------------------------------
# Main orchestrator
# ---------------------------------------------------------------------------


class TestSplitTranscriptIntoChapters:
    @pytest.mark.asyncio
    async def test_uses_youtube_chapters_when_available(self):
        duration = 2700
        segments = _make_segments(duration)
        video_data = {
            "duration": duration,
            "chapters": _make_youtube_chapters(6, duration),
        }

        result = await split_transcript_into_chapters(video_data, segments, "full transcript")

        assert len(result) == 6
        assert all(ch.source == "youtube" for ch in result)

    @pytest.mark.asyncio
    async def test_falls_back_to_time_split_when_no_chapters(self):
        duration = 2700
        segments = _make_segments(duration)
        video_data = {"duration": duration}

        result = await split_transcript_into_chapters(video_data, segments, "full transcript")

        assert len(result) >= 2
        assert all(ch.source == "time_split" for ch in result)

    @pytest.mark.asyncio
    async def test_falls_back_to_single_chunk_when_no_duration(self):
        video_data = {"duration": 0}

        result = await split_transcript_into_chapters(video_data, [], "full transcript text")

        assert len(result) == 1
        assert result[0].source == "full"
        assert result[0].text == "full transcript text"

    @pytest.mark.asyncio
    async def test_ai_detection_path(self):
        duration = 2700
        segments = _make_segments(duration)
        video_data = {"duration": duration, "title": "Test Video"}

        mock_llm = AsyncMock()
        mock_llm.model = "anthropic/claude-sonnet-4-6"
        mock_llm.fast_model = "anthropic/claude-haiku-4-5-20251001"

        ai_json = '[{"title":"Intro","startSeconds":0,"endSeconds":900},{"title":"Main","startSeconds":900,"endSeconds":1800},{"title":"Conclusion","startSeconds":1800,"endSeconds":2700}]'

        with patch(
            "src.services.transcription.transcript_chunker._detect_chapters_with_ai",
            new_callable=AsyncMock,
        ) as mock_detect:
            from src.services.transcription.transcript_chunker import (
                ChapterChunk as CC,
                _time_split_chapters,
            )

            # Build the expected result from AI detection
            mock_detect.return_value = [
                CC(
                    0,
                    "Intro",
                    0,
                    900,
                    " ".join(s["text"] for s in segments if s["startMs"] < 900000),
                    "ai_detected",
                    5000,
                ),
                CC(
                    1,
                    "Main",
                    900,
                    1800,
                    " ".join(s["text"] for s in segments if 900000 <= s["startMs"] < 1800000),
                    "ai_detected",
                    5000,
                ),
                CC(
                    2,
                    "Conclusion",
                    1800,
                    2700,
                    " ".join(s["text"] for s in segments if s["startMs"] >= 1800000),
                    "ai_detected",
                    5000,
                ),
            ]
            result = await split_transcript_into_chapters(
                video_data,
                segments,
                "full transcript",
                llm_service=mock_llm,
            )

        assert len(result) == 3
        assert all(ch.source == "ai_detected" for ch in result)

    @pytest.mark.asyncio
    async def test_ai_detection_failure_falls_back_to_time_split(self):
        duration = 2700
        segments = _make_segments(duration)
        video_data = {"duration": duration, "title": "Test Video"}

        mock_llm = AsyncMock()
        mock_llm.model = "anthropic/claude-sonnet-4-6"
        mock_llm.fast_model = "anthropic/claude-haiku-4-5-20251001"

        with patch(
            "src.services.transcription.transcript_chunker._detect_chapters_with_ai",
            new_callable=AsyncMock,
            return_value=None,  # AI detection fails
        ):
            result = await split_transcript_into_chapters(
                video_data,
                segments,
                "full transcript",
                llm_service=mock_llm,
            )

        assert len(result) >= 2
        assert all(ch.source == "time_split" for ch in result)

    @pytest.mark.asyncio
    async def test_always_returns_at_least_one_chunk(self):
        result = await split_transcript_into_chapters({}, [], "some text")
        assert len(result) >= 1

    @pytest.mark.asyncio
    async def test_segments_with_start_duration_format_are_normalized(self):
        """Segments using start/duration (yt-dlp format) should be normalized to startMs/endMs."""
        duration = 2700  # 45 min
        # Create segments in yt-dlp format (start in seconds, duration in seconds)
        segments = []
        interval = 5  # 5-second segments
        for start in range(0, duration, interval):
            segments.append(
                {
                    "text": f"word{start // interval}",
                    "start": float(start),
                    "duration": float(interval),
                }
            )

        video_data = {"duration": duration}

        result = await split_transcript_into_chapters(video_data, segments, "full transcript")

        # Should produce multiple time_split chapters (not fall through to single chunk)
        assert len(result) >= 2
        assert all(ch.source == "time_split" for ch in result)
        # Each chunk should have text (normalization allowed slicing to work)
        for ch in result:
            assert ch.text.strip()

    @pytest.mark.asyncio
    async def test_ai_chapter_detection_uses_fast_model(self):
        """``_detect_chapters_with_ai`` must route through the fast model.

        Chapter detection is a low-stakes parse — Sonnet is overkill.
        Switching to Haiku/mini saves ~$0.009 per long video (over 80%
        of the chapter_detect line item).
        """
        from src.services.transcription import transcript_chunker as tc

        duration = 2700
        segments = _make_segments(duration)
        video_data = {"duration": duration, "title": "Test Video"}

        mock_llm = AsyncMock()
        mock_llm.model = "anthropic/claude-sonnet-4-6"
        mock_llm.fast_model = "anthropic/claude-haiku-4-5-20251001"

        with patch(
            "src.services.transcription.transcript_chunker.call_llm_with_retry",
            new_callable=AsyncMock,
            return_value=None,  # short-circuit; we only care about kwargs
        ) as mock_call:
            tc._CHAPTER_DETECT_PROMPT = (
                "{title} {description} {transcript_samples} {duration_minutes}"
            )
            await tc._detect_chapters_with_ai(
                title="t",
                description="d",
                transcript="word " * 100,
                duration=duration,
                segments=segments,
                llm_service=mock_llm,
            )

            assert mock_call.await_count == 1
            kwargs = mock_call.await_args.kwargs
            assert kwargs.get("use_fast_model") is True
            assert kwargs.get("stage_name") == "chapter_detect"

    @pytest.mark.asyncio
    async def test_ai_chapter_detection_tags_feature(self):
        """``_detect_chapters_with_ai`` must set llm_feature_var to
        ``summarize:chapter_detect`` for the duration of its LLM call so
        the cost appears under its own line item in admin's
        ``/usage/by-feature`` instead of being absorbed by the outer
        ``summarize:extraction`` tag set in `phases/extraction.py`."""
        from src.services.transcription import transcript_chunker as tc
        from llm_common.context import llm_feature_var

        duration = 1800
        segments = _make_segments(duration)
        mock_llm = AsyncMock()

        observed_feature: list[str | None] = []

        async def _capture_feature(*_args, **_kwargs):
            observed_feature.append(llm_feature_var.get())
            return None

        # Pre-set outer feature to mimic the extraction phase wrapper
        outer_token = llm_feature_var.set("summarize:extraction")
        try:
            with patch(
                "src.services.transcription.transcript_chunker.call_llm_with_retry",
                side_effect=_capture_feature,
            ):
                tc._CHAPTER_DETECT_PROMPT = (
                    "{title} {description} {first_500_words} {last_500_words} {duration_minutes}"
                )
                await tc._detect_chapters_with_ai(
                    title="t",
                    description="d",
                    transcript="word " * 50,
                    duration=duration,
                    segments=segments,
                    llm_service=mock_llm,
                )
        finally:
            llm_feature_var.reset(outer_token)

        assert observed_feature == ["summarize:chapter_detect"]
        # Outer feature must be restored after the call returns
        # (we reset outer_token above; just confirm the var no longer
        # holds chapter_detect — i.e., the inner reset ran).
        assert llm_feature_var.get() != "summarize:chapter_detect"

    @pytest.mark.asyncio
    async def test_segments_with_start_duration_produce_correct_chapter_text(self):
        """Verify that start/duration segments produce distinct text per chapter."""
        duration = 600  # 10 min
        segments = []
        for start in range(0, duration, 5):
            segments.append(
                {
                    "text": f"seg_at_{start}s",
                    "start": float(start),
                    "duration": 5.0,
                }
            )

        chapters = [
            {"title": "Part 1", "start_time": 0, "end_time": 300},
            {"title": "Part 2", "start_time": 300, "end_time": 600},
        ]
        video_data = {"duration": duration, "chapters": chapters}

        result = await split_transcript_into_chapters(video_data, segments, "full transcript")

        assert len(result) == 2
        # Part 1 should contain segments from 0-295s but not 300+
        assert "seg_at_0s" in result[0].text
        assert "seg_at_295s" in result[0].text
        assert "seg_at_300s" not in result[0].text
        # Part 2 should contain segments from 300-595s
        assert "seg_at_300s" in result[1].text
        assert "seg_at_595s" in result[1].text


# ---------------------------------------------------------------------------
# Description-timestamp path (Tier 2)
# ---------------------------------------------------------------------------


class TestFromDescriptionTimestamps:
    def test_converts_markers_to_chunks_with_derived_ends(self):
        duration = 600
        segments = _make_segments(duration)
        markers = [
            {"seconds": 0, "label": "Intro"},
            {"seconds": 200, "label": "Setup"},
            {"seconds": 400, "label": "Demo"},
        ]

        result = _from_timestamp_markers(markers, segments, duration, "description")

        assert result is not None
        assert len(result) == 3
        assert all(ch.source == "description" for ch in result)
        assert result[0].start_seconds == 0
        assert result[0].end_seconds == 200  # derived from next marker
        assert result[-1].end_seconds == duration  # last ends at duration
        assert result[0].title == "Intro"

    def test_sorts_unordered_markers(self):
        duration = 600
        segments = _make_segments(duration)
        markers = [
            {"seconds": 400, "label": "Demo"},
            {"seconds": 0, "label": "Intro"},
            {"seconds": 200, "label": "Setup"},
        ]

        result = _from_timestamp_markers(markers, segments, duration, "description")

        assert result is not None
        assert [ch.start_seconds for ch in result] == [0, 200, 400]

    def test_returns_none_for_single_marker(self):
        segments = _make_segments(600)
        markers = [{"seconds": 0, "label": "Only one"}]

        assert _from_timestamp_markers(markers, segments, 600, "description") is None

    def test_dedups_equal_start_offsets(self):
        duration = 600
        segments = _make_segments(duration)
        markers = [
            {"seconds": 0, "label": "Intro"},
            {"seconds": 0, "label": "Duplicate"},
            {"seconds": 300, "label": "Middle"},
        ]

        result = _from_timestamp_markers(markers, segments, duration, "description")

        assert result is not None
        assert len(result) == 2


# ---------------------------------------------------------------------------
# Full-transcript sampling + coverage validation (Tier 3 rework)
# ---------------------------------------------------------------------------


class TestBuildSampledExcerpts:
    def test_excerpts_span_the_whole_video(self):
        duration = 16789  # 4.5h — the regression case
        segments = _make_segments(duration)

        excerpts = _build_sampled_excerpts(segments, duration, "ignored", num_samples=12)

        # Markers should reach deep into the video, not just the first minutes.
        assert "[at 0:00]" in excerpts
        # A late-video marker (past 4 hours) must be present.
        assert "[at 4:" in excerpts

    def test_falls_back_to_transcript_when_no_duration(self):
        excerpts = _build_sampled_excerpts([], 0, "some transcript text")
        assert excerpts == "some transcript text"


class TestChaptersCoverDuration:
    def _chunks(self, ranges: list[tuple[float, float]]) -> list[ChapterChunk]:
        return [
            ChapterChunk(
                index=i,
                title=f"c{i}",
                start_seconds=s,
                end_seconds=e,
                text="x",
                source="ai_detected",
                token_estimate=1,
            )
            for i, (s, e) in enumerate(ranges)
        ]

    def test_true_when_chapters_span_full_duration(self):
        chunks = self._chunks([(0, 8000), (8000, 16700)])
        assert _chapters_cover_duration(chunks, 16789) is True

    def test_false_when_chapters_only_cover_start(self):
        # The bug signature: all chapters clustered in the first third.
        chunks = self._chunks([(0, 2500), (2500, 5696)])
        assert _chapters_cover_duration(chunks, 16789) is False

    def test_false_for_empty_or_zero_duration(self):
        assert _chapters_cover_duration([], 16789) is False
        assert _chapters_cover_duration(self._chunks([(0, 100)]), 0) is False


class TestAIDetectionCoverageGate:
    @pytest.mark.asyncio
    async def test_returns_none_when_ai_chapters_undercover(self):
        """AI chapters that stop a third of the way in must be discarded so
        the chain falls through to the reliable time-split path."""
        from src.services.transcription import transcript_chunker as tc

        duration = 16789
        segments = _make_segments(duration)
        # LLM returns chapters covering only 0–5696s (the 1:34 of 4:39 bug).
        ai_json = (
            '[{"title":"A","startSeconds":0,"endSeconds":2800},'
            '{"title":"B","startSeconds":2800,"endSeconds":5696}]'
        )

        with patch(
            "src.services.transcription.transcript_chunker.call_llm_with_retry",
            new_callable=AsyncMock,
            return_value=ai_json,
        ):
            result = await tc._detect_chapters_with_ai(
                title="t",
                description="d",
                transcript="word " * 500,
                duration=duration,
                segments=segments,
                llm_service=AsyncMock(),
            )

        assert result is None

    @pytest.mark.asyncio
    async def test_returns_chunks_when_ai_chapters_cover_full(self):
        from src.services.transcription import transcript_chunker as tc

        duration = 16789
        segments = _make_segments(duration)
        ai_json = (
            '[{"title":"A","startSeconds":0,"endSeconds":8000},'
            '{"title":"B","startSeconds":8000,"endSeconds":16700}]'
        )

        with patch(
            "src.services.transcription.transcript_chunker.call_llm_with_retry",
            new_callable=AsyncMock,
            return_value=ai_json,
        ):
            result = await tc._detect_chapters_with_ai(
                title="t",
                description="d",
                transcript="word " * 500,
                duration=duration,
                segments=segments,
                llm_service=AsyncMock(),
            )

        assert result is not None
        assert len(result) == 2
        assert all(ch.source == "ai_detected" for ch in result)


class TestDescriptionPathOrdering:
    @pytest.mark.asyncio
    async def test_description_chapters_used_before_ai_detection(self):
        """When description timestamps are supplied, they win over AI detection
        (and the AI path must never be invoked)."""
        duration = 1800
        segments = _make_segments(duration)
        markers = [
            {"seconds": 0, "label": "Intro"},
            {"seconds": 600, "label": "Body"},
            {"seconds": 1200, "label": "Outro"},
        ]

        with patch(
            "src.services.transcription.transcript_chunker._detect_chapters_with_ai",
            new_callable=AsyncMock,
        ) as mock_ai:
            result = await split_transcript_into_chapters(
                video_data={"duration": duration, "title": "T", "chapters": None},
                segments=segments,
                transcript="full transcript",
                llm_service=AsyncMock(),
                description_chapters=markers,
            )

        assert all(ch.source == "description" for ch in result)
        mock_ai.assert_not_awaited()


# ---------------------------------------------------------------------------
# Oversized-chapter subdivision (Round 2 — the giant-tail-chapter bug)
# ---------------------------------------------------------------------------


class TestSubdivideOversizedChapters:
    def test_splits_giant_chapter_into_span_bounded_pieces(self):
        duration = 16789  # 4.5h
        segments = _make_segments(duration)
        # One small chapter + one 3-hour chapter (the AI giant-tail signature).
        chapters = [
            ChapterChunk(0, "Intro", 0, 1380, "x", "ai_detected", 100),
            ChapterChunk(1, "Final", 6000, 16789, "x", "ai_detected", 40000),
        ]

        result = _subdivide_oversized_chapters(chapters, segments, max_minutes=40)

        max_seconds = 40 * 60
        assert all((c.end_seconds - c.start_seconds) <= max_seconds + 1 for c in result)
        # Small chapter untouched, giant one split into multiple parts.
        assert len(result) > 2
        # Indices are sequential after subdivision.
        assert [c.index for c in result] == list(range(len(result)))
        # The subdivided pieces still cover the giant chapter's full range.
        finals = [c for c in result if c.title.startswith("Final")]
        assert finals[0].start_seconds == 6000
        assert finals[-1].end_seconds == 16789
        assert all("(part " in c.title for c in finals)

    def test_leaves_normal_chapters_untouched(self):
        segments = _make_segments(3000)
        chapters = [
            ChapterChunk(0, "A", 0, 1380, "x", "ai_detected", 100),
            ChapterChunk(1, "B", 1380, 2760, "x", "ai_detected", 100),
        ]

        result = _subdivide_oversized_chapters(chapters, segments, max_minutes=40)

        assert len(result) == 2
        assert [c.title for c in result] == ["A", "B"]

    def test_no_op_without_segments(self):
        chapters = [ChapterChunk(0, "Giant", 0, 16789, "x", "ai_detected", 40000)]
        result = _subdivide_oversized_chapters(chapters, [], max_minutes=40)
        assert result == chapters

    @pytest.mark.asyncio
    async def test_split_pipeline_yields_only_span_bounded_chunks(self):
        """End-to-end: an AI giant-tail chapter must be subdivided so every
        returned chunk fits the batch span cap."""
        from src.services.transcription import transcript_chunker as tc
        from src.config import settings

        duration = 16789
        segments = _make_segments(duration)
        # AI returns 2 chapters, the second spanning 1:40:00 → end (3h).
        ai_json = (
            '[{"title":"Intro","startSeconds":0,"endSeconds":6000},'
            '{"title":"Final","startSeconds":6000,"endSeconds":16789}]'
        )

        with patch(
            "src.services.transcription.transcript_chunker.call_llm_with_retry",
            new_callable=AsyncMock,
            return_value=ai_json,
        ):
            result = await split_transcript_into_chapters(
                video_data={"duration": duration, "title": "T", "chapters": None},
                segments=segments,
                transcript="word " * 2000,
                llm_service=AsyncMock(),
            )

        max_seconds = settings.MAX_MINUTES_PER_BATCH * 60
        assert all((c.end_seconds - c.start_seconds) <= max_seconds + 1 for c in result)
        assert result[0].start_seconds == 0
        assert result[-1].end_seconds == duration


# ---------------------------------------------------------------------------
# Chunk texts carry absolute [m:ss] markers (pipeline-1min 1a.4)
# ---------------------------------------------------------------------------


def _sentence_segments(count: int, seconds_each: float = 5.0, words: int = 10) -> list[dict]:
    """Raw ``{text, start, duration}`` segments, one punctuated sentence each."""
    return [
        {
            "text": " ".join(f"w{i}x{j}" for j in range(words)) + ".",
            "start": i * seconds_each,
            "duration": seconds_each,
        }
        for i in range(count)
    ]


def _markers_within_bounds(chunk: ChapterChunk) -> bool:
    return all(chunk.start_seconds <= t <= chunk.end_seconds for t in marker_seconds(chunk.text))


class TestChunkTextsCarryMarkers:
    @pytest.mark.asyncio
    async def test_should_open_each_time_split_chunk_on_its_absolute_start(self):
        result = await split_transcript_into_chapters(
            {"duration": 2700}, _make_segments(2700), "full transcript"
        )

        assert [marker_seconds(ch.text)[0] for ch in result] == [
            int(ch.start_seconds) for ch in result
        ]

    @pytest.mark.asyncio
    async def test_should_keep_every_chunk_marker_inside_its_chapter_range(self):
        result = await split_transcript_into_chapters(
            {"duration": 2700, "chapters": _make_youtube_chapters(6, 2700)},
            _make_segments(2700),
            "full transcript",
        )

        assert all(_markers_within_bounds(ch) for ch in result)

    def test_should_render_chunks_past_one_hour_as_h_mm_ss(self):
        chapters = [
            {"title": "Early", "start_time": 0, "end_time": 3600},
            {"title": "Late", "start_time": 3600, "end_time": 3900},
        ]

        result = _from_youtube_chapters(chapters, _make_segments(3900), 3900)

        assert result is not None
        assert result[1].text.startswith("[1:00:00] ")


class TestForceSplitBoundsFromMarkers:
    def _split(self, segment_count: int = 1200, target_chunks: int = 4) -> list[ChapterChunk]:
        segments = _sentence_segments(segment_count)
        duration = segment_count * 5.0
        marked = render_transcript(segments)
        return force_split_by_sentences(marked, duration, target_chunks=target_chunks)

    def test_should_keep_every_marker_inside_its_chunk_bounds(self):
        chunks = self._split()

        assert len(chunks) >= 2
        assert all(_markers_within_bounds(ch) for ch in chunks)

    def test_should_make_bounds_contiguous_from_zero_to_duration(self):
        chunks = self._split()

        assert chunks[0].start_seconds == 0.0
        assert chunks[-1].end_seconds == 6000.0
        assert all(a.end_seconds == b.start_seconds for a, b in zip(chunks, chunks[1:]))

    def test_should_start_a_chunk_that_opens_on_a_marker_at_that_marker(self):
        chunks = self._split()  # 4 sentences per block, 300 per chunk → block-aligned

        assert [c.start_seconds for c in chunks[1:]] == [
            marker_seconds(c.text)[0] for c in chunks[1:]
        ]

    def test_should_start_a_chunk_that_opens_mid_block_at_the_previous_marker(self):
        chunks = self._split(segment_count=1203, target_chunks=3)  # 401 per chunk

        assert not chunks[1].text.startswith("[")
        assert chunks[1].start_seconds == marker_seconds(chunks[0].text)[-1]

    def test_should_keep_proportional_bounds_for_unmarked_text(self):
        text = " ".join(s["text"] for s in _sentence_segments(1200))

        chunks = force_split_by_sentences(text, 6000.0, target_chunks=4)

        assert [c.start_seconds for c in chunks] == [0.0, 1500.0, 3000.0, 4500.0]


# ---------------------------------------------------------------------------
# chapter_detect gate (pipeline-1min 1b.6, D6/C7)
# ---------------------------------------------------------------------------

_OUTLINE = [
    {"start": "0:00", "end": "9:30", "title": "Intro"},
    {"start": "10:00", "end": "29:00", "title": "Main"},
    {"start": "30:00", "end": "44:00", "title": "Tips"},
]


def _detect_mock(result: list[ChapterChunk] | None = None) -> AsyncMock:
    return AsyncMock(return_value=result)


class TestNeedsChapterBatching:
    def test_should_need_batching_when_the_video_outlasts_one_batch_span(self):
        duration = settings.MAX_MINUTES_PER_BATCH * 60 + 1

        assert needs_chapter_batching(duration, "short transcript") is True

    def test_should_need_batching_when_tokens_exceed_one_batch_budget(self):
        with patch.object(settings, "MAX_TOKENS_PER_BATCH", 100):
            assert needs_chapter_batching(600, "word " * 100) is True

    def test_should_not_need_batching_when_one_batch_covers_the_video(self):
        duration = settings.MAX_MINUTES_PER_BATCH * 60

        assert needs_chapter_batching(duration, "word " * 1000) is False


class TestChapterDetectGate:
    async def _split(
        self, duration: int, detect: AsyncMock, outline: list[dict] | None = None, **video
    ) -> list[ChapterChunk]:
        with patch.object(chunker_module, "_detect_chapters_with_ai", detect):
            return await split_transcript_into_chapters(
                {"duration": duration, "title": "T", **video},
                _make_segments(duration),
                "word " * 500,
                llm_service=AsyncMock(),
                memory_outline=outline,
            )

    @pytest.mark.asyncio
    async def test_should_skip_chapter_detect_when_one_batch_covers_the_video(self):
        detect = _detect_mock()

        result = await self._split(1800, detect)

        detect.assert_not_awaited()
        assert {ch.source for ch in result} == {"time_split"}

    @pytest.mark.asyncio
    async def test_should_call_chapter_detect_for_a_long_video_without_outline(self):
        detect = _detect_mock()

        await self._split(2700, detect)

        detect.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_should_call_chapter_detect_when_tokens_exceed_one_batch(self):
        detect = _detect_mock()

        with patch.object(settings, "MAX_TOKENS_PER_BATCH", 100):
            await self._split(1800, detect)

        detect.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_should_use_the_memory_outline_instead_of_chapter_detect(self):
        detect = _detect_mock()

        result = await self._split(2700, detect, outline=_OUTLINE)

        detect.assert_not_awaited()
        assert [ch.title for ch in result] == ["Intro", "Main", "Tips"]

    @pytest.mark.asyncio
    async def test_should_fall_back_to_chapter_detect_when_the_outline_is_unusable(self):
        detect = _detect_mock()

        await self._split(2700, detect, outline=[{"start": "0:00", "title": "Only one"}])

        detect.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_should_ignore_the_outline_when_one_batch_covers_the_video(self):
        detect = _detect_mock()

        result = await self._split(1800, detect, outline=_OUTLINE)

        assert {ch.source for ch in result} == {"time_split"}

    @pytest.mark.asyncio
    async def test_should_keep_youtube_chapters_ahead_of_the_outline(self):
        detect = _detect_mock()

        result = await self._split(
            2700, detect, outline=_OUTLINE, chapters=_make_youtube_chapters(6, 2700)
        )

        assert {ch.source for ch in result} == {"youtube"}

    @pytest.mark.asyncio
    async def test_should_hand_chapter_detect_the_video_description(self):
        detect = _detect_mock()

        await self._split(2700, detect, description="Neapolitan dough, 72 h cold rise")

        assert detect.await_args.kwargs["description"] == "Neapolitan dough, 72 h cold rise"

    @pytest.mark.asyncio
    async def test_should_put_the_description_into_the_chapter_detect_prompt(self):
        call = AsyncMock(return_value=None)

        with (
            patch.object(chunker_module, "_CHAPTER_DETECT_PROMPT", "D={description}"),
            patch.object(chunker_module, "prompts_from_disk", return_value=False),
            patch.object(chunker_module, "call_llm_with_retry", call),
        ):
            await chunker_module._detect_chapters_with_ai(
                title="t",
                description="Neapolitan dough",
                transcript="word " * 100,
                duration=2700,
                segments=_make_segments(2700),
                llm_service=AsyncMock(),
            )

        assert call.await_args.args[1] == "D=Neapolitan dough"


class TestMemoryOutlineChapters:
    def test_should_start_each_chapter_at_its_section_start(self):
        result = _from_memory_outline(_OUTLINE, _make_segments(2700), 2700)

        assert result is not None
        assert [ch.start_seconds for ch in result] == [0.0, 600.0, 1800.0]

    def test_should_end_each_chapter_where_the_next_starts_and_the_last_at_the_end(self):
        result = _from_memory_outline(_OUTLINE, _make_segments(2700), 2700)

        assert result is not None
        assert [ch.end_seconds for ch in result] == [600.0, 1800.0, 2700.0]

    def test_should_pull_the_first_section_to_zero(self):
        outline = [{"start": "0:30", "title": "A"}, {"start": "20:00", "title": "B"}]

        result = _from_memory_outline(outline, _make_segments(2700), 2700)

        assert result is not None
        assert result[0].start_seconds == 0.0

    def test_should_read_hour_long_starts(self):
        outline = [{"start": "0:00", "title": "A"}, {"start": "1:05:00", "title": "B"}]

        result = _from_memory_outline(outline, _make_segments(4500), 4500)

        assert result is not None
        assert result[1].start_seconds == 3900.0

    def test_should_skip_sections_with_malformed_starts(self):
        outline = [
            {"start": "0:00", "title": "A"},
            {"start": "abc", "title": "bad"},
            {"start": None, "title": "missing"},
            {"start": "15:00", "title": "B"},
        ]

        result = _from_memory_outline(outline, _make_segments(2700), 2700)

        assert result is not None
        assert [ch.title for ch in result] == ["A", "B"]

    def test_should_return_none_with_fewer_than_two_usable_sections(self):
        outline = [{"start": "0:00", "title": "A"}, {"start": "x", "title": "B"}]

        assert _from_memory_outline(outline, _make_segments(2700), 2700) is None

    def test_should_open_each_outline_chunk_on_its_absolute_marker(self):
        result = _from_memory_outline(_OUTLINE, _make_segments(2700), 2700)

        assert result is not None
        assert [marker_seconds(ch.text)[0] for ch in result] == [0, 600, 1800]
