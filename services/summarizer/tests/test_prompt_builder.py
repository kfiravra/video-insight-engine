"""Tests for prompt_builder's extraction key-frames block (the template itself: test_extraction_prompt)."""

from src.services.pipeline.prompt_builder import format_gallery_frames_for_extraction


class TestFormatGalleryFramesForExtraction:
    """1E: gallery frames folded into the extraction prompt."""

    def test_empty_returns_empty_string(self):
        assert format_gallery_frames_for_extraction([]) == ""

    def test_formats_timestamp_and_ocr_caption(self):
        frames = [{"timestamp": 65, "ocr_text": "def binary_search(arr)"}]
        out = format_gallery_frames_for_extraction(frames)
        assert out == "1:05 — def binary_search(arr)"

    def test_vision_caption_wins_over_ocr_and_adds_scene(self):
        frames = [{"timestamp": 12, "ocr_text": "raw ocr"}]
        descriptions = [
            {
                "timestamp_sec": 12,
                "content": "A flowchart of the algorithm",
                "scene_type": "diagram",
            }
        ]
        out = format_gallery_frames_for_extraction(frames, descriptions)
        assert out == "0:12 — A flowchart of the algorithm [diagram]"

    def test_frames_without_caption_are_skipped(self):
        frames = [{"timestamp": 10}, {"timestamp": 20, "ocr_text": "Slide B"}]
        out = format_gallery_frames_for_extraction(frames)
        assert out == "0:20 — Slide B"

    def test_capped_at_twelve_frames(self):
        frames = [{"timestamp": i, "ocr_text": f"f{i}"} for i in range(30)]
        out = format_gallery_frames_for_extraction(frames)
        assert len(out.splitlines()) == 12

    def test_caption_length_capped(self):
        frames = [{"timestamp": 0, "ocr_text": "x" * 300}]
        out = format_gallery_frames_for_extraction(frames)
        caption = out.split(" — ", 1)[1]
        assert len(caption) <= 90

    def test_sorted_by_timestamp(self):
        frames = [{"timestamp": 90, "ocr_text": "late"}, {"timestamp": 5, "ocr_text": "early"}]
        out = format_gallery_frames_for_extraction(frames)
        assert out.splitlines()[0].startswith("0:05")

    def test_formats_hours_like_transcript_markers_when_past_one_hour(self):
        frames = [{"timestamp": 3725, "ocr_text": "late slide"}]
        out = format_gallery_frames_for_extraction(frames)
        assert out == "1:02:05 — late slide"
