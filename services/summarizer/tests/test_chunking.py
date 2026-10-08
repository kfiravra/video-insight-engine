"""Tests for transcript chunking service."""

import pytest

from src.services.vector.chunking import (
    assign_chunk_timestamps,
    chunk_transcript,
    chunk_visual_entries,
)


class TestChunkTranscript:
    """Test chunk_transcript function."""

    def test_empty_text_returns_empty(self):
        assert chunk_transcript("") == []
        assert chunk_transcript("   ") == []
        assert chunk_transcript(None) == []

    def test_short_text_single_chunk(self):
        text = "Hello world. This is a test."
        chunks = chunk_transcript(text, max_chunk_chars=1000)
        assert len(chunks) == 1
        assert chunks[0]["text"] == text
        assert chunks[0]["start_char"] == 0

    def test_long_text_multiple_chunks(self):
        # Create text that exceeds max_chunk_chars
        sentences = [f"Sentence number {i} with some content." for i in range(50)]
        text = " ".join(sentences)
        chunks = chunk_transcript(text, max_chunk_chars=200, overlap_chars=50)
        assert len(chunks) > 1
        # All chunks should have text
        for chunk in chunks:
            assert chunk["text"]
            assert chunk["start_char"] >= 0
            assert chunk["end_char"] > chunk["start_char"]

    def test_overlap_between_chunks(self):
        sentences = [f"Sentence {i} has some words." for i in range(20)]
        text = " ".join(sentences)
        chunks = chunk_transcript(text, max_chunk_chars=100, overlap_chars=30)

        if len(chunks) >= 2:
            # Second chunk should start before first chunk ends (overlap)
            assert chunks[1]["start_char"] < chunks[0]["end_char"]

    def test_sentence_boundary_splitting(self):
        text = "First sentence. Second sentence. Third sentence."
        chunks = chunk_transcript(text, max_chunk_chars=35, overlap_chars=0)
        # Should split at sentence boundaries, not mid-sentence
        for chunk in chunks:
            # Each chunk should end with a complete word
            assert not chunk["text"].endswith(" ")

    def test_no_sentences_still_works(self):
        text = "just some text without any sentence boundaries at all"
        chunks = chunk_transcript(text, max_chunk_chars=1000)
        assert len(chunks) == 1
        assert chunks[0]["text"] == text

    def test_start_end_chars_are_monotonic(self):
        sentences = [f"Test sentence number {i}." for i in range(30)]
        text = " ".join(sentences)
        chunks = chunk_transcript(text, max_chunk_chars=150, overlap_chars=30)
        # start_char should be non-decreasing
        for i in range(1, len(chunks)):
            assert chunks[i]["start_char"] >= chunks[i - 1]["start_char"]


class TestAssignChunkTimestamps:
    """Test mapping chunk char spans onto the segment timeline."""

    def test_empty_segments_leaves_chunks_untouched(self):
        chunks = [{"text": "hi", "start_char": 0, "end_char": 2}]
        result = assign_chunk_timestamps(chunks, [])
        assert "start_time" not in result[0]
        assert "end_time" not in result[0]

    def test_empty_chunks_returns_empty(self):
        assert assign_chunk_timestamps([], [{"text": "x", "start": 0, "duration": 5}]) == []

    def test_single_chunk_spans_full_timeline(self):
        segments = [
            {"text": "First segment here.", "start": 0.0, "duration": 10.0},
            {"text": "Second segment here.", "start": 10.0, "duration": 10.0},
        ]
        text = "First segment here. Second segment here."
        chunks = chunk_transcript(text, max_chunk_chars=1000)

        assign_chunk_timestamps(chunks, segments)

        assert chunks[0]["start_time"] == 0.0
        assert chunks[0]["end_time"] == 20.0

    def test_multiple_chunks_map_to_correct_segment_starts(self):
        # Four segments, 25 chars each, 10 seconds each.
        segments = [
            {"text": "a" * 25, "start": 0.0, "duration": 10.0},
            {"text": "b" * 25, "start": 10.0, "duration": 10.0},
            {"text": "c" * 25, "start": 20.0, "duration": 10.0},
            {"text": "d" * 25, "start": 30.0, "duration": 10.0},
        ]
        # Chunk char space matches segment char space (26 chars per segment
        # incl. joining space, 104 total; chunks span 0..104 → scale 1.0).
        chunks = [
            {"text": "x", "start_char": 0, "end_char": 50},
            {"text": "y", "start_char": 40, "end_char": 104},
        ]

        assign_chunk_timestamps(chunks, segments)

        assert chunks[0]["start_time"] == 0.0  # begins in segment 1
        assert chunks[0]["end_time"] == 20.0  # ends in segment 2
        assert chunks[1]["start_time"] == 10.0  # begins in segment 2
        assert chunks[1]["end_time"] == 40.0  # ends in last segment

    def test_supports_start_ms_segment_shape(self):
        segments = [
            {"text": "a" * 50, "startMs": 0, "endMs": 12000},
            {"text": "b" * 50, "startMs": 12000, "endMs": 30000},
        ]
        chunks = [
            {"text": "x", "start_char": 0, "end_char": 50},
            {"text": "y", "start_char": 51, "end_char": 102},
        ]

        assign_chunk_timestamps(chunks, segments)

        assert chunks[0]["start_time"] == 0.0
        assert chunks[1]["start_time"] == 12.0
        assert chunks[1]["end_time"] == 30.0

    def test_start_times_are_monotonic_across_real_chunks(self):
        segments = [
            {"text": f"Sentence number {i} with some content.", "start": i * 5.0, "duration": 5.0}
            for i in range(50)
        ]
        text = " ".join(s["text"] for s in segments)
        chunks = chunk_transcript(text, max_chunk_chars=200, overlap_chars=50)
        assert len(chunks) > 1

        assign_chunk_timestamps(chunks, segments)

        for i in range(1, len(chunks)):
            assert chunks[i]["start_time"] >= chunks[i - 1]["start_time"]
        # First chunk starts at the video start; a later chunk is deep in.
        assert chunks[0]["start_time"] == 0.0
        assert chunks[-1]["start_time"] > 0.0
        for chunk in chunks:
            assert chunk["end_time"] >= chunk["start_time"]

    def test_scales_when_chunked_text_differs_in_length(self):
        """Cleaned/translated text length differs from raw segments — the
        proportional projection must still land in the right region."""
        segments = [
            {"text": "a" * 100, "start": 0.0, "duration": 30.0},
            {"text": "b" * 100, "start": 30.0, "duration": 30.0},
        ]
        # Chunked (cleaned) text is half the length of the segment text.
        chunks = [
            {"text": "x", "start_char": 0, "end_char": 50},
            {"text": "y", "start_char": 51, "end_char": 101},
        ]

        assign_chunk_timestamps(chunks, segments)

        assert chunks[0]["start_time"] == 0.0
        assert chunks[1]["start_time"] == 30.0  # second half → second segment

    def test_blank_segments_are_skipped(self):
        segments = [
            {"text": "   ", "start": 0.0, "duration": 5.0},
            {"text": "real content", "start": 5.0, "duration": 5.0},
        ]
        chunks = [{"text": "x", "start_char": 0, "end_char": 12}]

        assign_chunk_timestamps(chunks, segments)

        assert chunks[0]["start_time"] == 5.0


class TestChunkVisualEntries:
    """Packing rendered visual-annotation entries into labelled chunks."""

    def test_should_return_no_chunks_for_no_entries(self):
        assert chunk_visual_entries([]) == []

    def test_should_open_every_chunk_with_the_on_screen_label(self):
        chunks = chunk_visual_entries([(12, "[0:12] Code"), (40, "[0:40] Diagram")])

        assert chunks[0]["text"] == "On screen:\n[0:12] Code\n[0:40] Diagram"

    def test_should_span_from_the_first_to_the_last_entry(self):
        chunks = chunk_visual_entries([(12, "[0:12] Code"), (40, "[0:40] Diagram")])

        assert (chunks[0]["start_time"], chunks[0]["end_time"]) == (12.0, 40.0)

    def test_should_start_a_new_chunk_when_the_budget_is_reached(self):
        entries = [(10 * i, f"[0:{10 * i:02d}] " + "x" * 40) for i in range(4)]

        chunks = chunk_visual_entries(entries, max_chunk_chars=120)

        assert [(c["start_time"], c["end_time"]) for c in chunks] == [(0.0, 10.0), (20.0, 30.0)]

    def test_should_never_split_an_entry_longer_than_the_budget(self):
        long_entry = "[0:05] " + "y" * 300

        chunks = chunk_visual_entries([(5, long_entry)], max_chunk_chars=100)

        assert chunks[0]["text"] == f"On screen:\n{long_entry}"
