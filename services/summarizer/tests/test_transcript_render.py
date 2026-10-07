"""Tests for render_transcript — prompt-only [m:ss] markers (pipeline-1min 1a.4)."""

import pytest

from src.services.transcript.render import (
    MARKER_PATTERN,
    format_marker,
    marker_seconds,
    render_transcript,
)
from src.services.transcription.transcript import clean_transcript


def _segments(starts: list[float], duration: float = 4.0) -> list[dict]:
    """Raw pipeline segments ``{text, start, duration}`` named after their start."""
    return [{"text": f"s{int(s)}", "start": s, "duration": duration} for s in starts]


def _strip_markers(rendered: str) -> str:
    """The rendered text without markers, lines joined the way clean_text joins."""
    return " ".join(MARKER_PATTERN.sub("", line).strip() for line in rendered.splitlines())


class TestFormatMarker:
    def test_should_use_m_ss_below_one_hour(self):
        assert format_marker(65.9) == "[1:05]"

    def test_should_use_h_mm_ss_from_one_hour_on(self):
        assert format_marker(3600 + 2 * 60 + 3) == "[1:02:03]"

    def test_should_clamp_negative_times_to_zero(self):
        assert format_marker(-3) == "[0:00]"


class TestMarkerSeconds:
    def test_should_read_both_marker_formats_in_order(self):
        assert marker_seconds("[0:21] a\n[59:59] b\n[1:02:03] c") == [21, 3599, 3723]


class TestRenderTranscriptSpacing:
    def test_should_open_a_block_at_the_first_segment_of_each_boundary(self):
        rendered = render_transcript(_segments([0, 5, 10, 15, 20, 25, 30, 35, 40]))

        assert rendered.splitlines() == [
            "[0:00] s0 s5 s10 s15",
            "[0:20] s20 s25 s30 s35",
            "[0:40] s40",
        ]

    def test_should_show_the_segment_start_not_the_boundary(self):
        rendered = render_transcript(_segments([2.5, 12.0, 23.7, 33.0]))

        assert marker_seconds(rendered) == [2, 23]

    def test_should_place_one_marker_when_a_segment_spans_several_boundaries(self):
        rendered = render_transcript(_segments([0, 18, 75], duration=57.0))

        assert rendered.splitlines() == ["[0:00] s0 s18", "[1:15] s75"]

    def test_should_honor_a_custom_interval(self):
        rendered = render_transcript(_segments([0, 30, 60, 90, 120]), every=60)

        assert marker_seconds(rendered) == [0, 60, 120]

    def test_should_never_move_time_backwards_on_out_of_order_captions(self):
        rendered = render_transcript(_segments([0, 21, 19, 41]))

        assert marker_seconds(rendered) == [0, 21, 41]

    def test_should_reject_a_non_positive_interval(self):
        with pytest.raises(ValueError):
            render_transcript(_segments([0]), every=0)


class TestRenderTranscriptAbsoluteTimes:
    def test_should_keep_absolute_times_on_a_slice_of_a_long_video(self):
        chunk = _segments([3601, 3610, 3620, 3645])

        rendered = render_transcript(chunk)

        assert marker_seconds(rendered) == [3601, 3620, 3645]

    def test_should_format_slices_past_one_hour_as_h_mm_ss(self):
        rendered = render_transcript(_segments([3725]))

        assert rendered == "[1:02:05] s3725"

    def test_should_render_startms_segments_like_start_segments(self):
        raw = _segments([0, 12, 25, 47])
        normalized = [
            {"text": s["text"], "startMs": int(s["start"] * 1000), "endMs": 0} for s in raw
        ]

        assert render_transcript(normalized) == render_transcript(raw)


class TestRenderTranscriptMatchesCleanText:
    def test_should_lose_and_duplicate_nothing_versus_clean_text(self):
        segments = [
            {"text": "[Music]", "start": 0.0, "duration": 3.0},
            {"text": "welcome  back to the", "start": 3.0, "duration": 4.0},
            {"text": "channel [Applause]", "start": 7.0, "duration": 5.0},
            {"text": "today we bake bread", "start": 19.5, "duration": 4.0},
            {"text": "[Laughter]", "start": 21.0, "duration": 2.0},
            {"text": "  with a starter", "start": 40.2, "duration": 6.0},
        ]
        clean_text = clean_transcript(" ".join(s["text"] for s in segments))

        rendered = render_transcript(segments)

        assert _strip_markers(rendered) == clean_text

    def test_should_not_open_a_block_on_a_segment_that_cleans_to_nothing(self):
        segments = [
            {"text": "intro words", "start": 0.0, "duration": 5.0},
            {"text": "[Music]", "start": 20.0, "duration": 5.0},
            {"text": "first step", "start": 26.0, "duration": 5.0},
        ]

        rendered = render_transcript(segments)

        assert rendered.splitlines() == ["[0:00] intro words", "[0:26] first step"]

    def test_should_return_empty_text_without_segments(self):
        assert render_transcript([]) == ""

    def test_should_return_empty_text_when_no_segment_has_words(self):
        assert render_transcript([{"text": "[Music]", "start": 0, "duration": 9}]) == ""
