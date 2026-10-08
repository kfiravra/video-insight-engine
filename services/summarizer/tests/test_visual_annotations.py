"""Tests for the <visual_annotations> block renderer (1c.2)."""

from __future__ import annotations

from typing import Any

from src.services.pipeline.visual_annotations import (
    CLOSE_TAG,
    OPEN_TAG,
    annotation_entries,
    render_visual_annotations,
)


def _desc(
    seconds: float,
    content: str,
    *,
    text: str = "",
    index: int | None = None,
    scene_type: str = "slide",
    educational_value: str | None = "explains the step",
) -> dict[str, Any]:
    return {
        "timestamp_sec": seconds,
        "content": content,
        "text_visible": text,
        "original_index": index,
        "scene_type": scene_type,
        "educational_value": educational_value,
    }


def _frame(index: int, seconds: float, ocr: str | None = None) -> dict[str, Any]:
    return {"index": index, "timestamp": seconds, "ocr_text": ocr}


def _lines(block: str) -> list[str]:
    """The block's entry lines, without the wrapping tags."""
    return block.splitlines()[1:-1]


class TestRenderVisualAnnotations:
    def test_should_return_empty_string_when_there_are_no_frames(self):
        assert render_visual_annotations([], []) == ""

    def test_should_return_empty_string_when_no_frame_has_caption_or_text(self):
        assert render_visual_annotations([], [_frame(0, 5.0), _frame(1, 9.0, "ab")]) == ""

    def test_should_wrap_entries_in_the_visual_annotations_tags(self):
        block = render_visual_annotations([_desc(5, "A whiteboard sketch")], [])

        assert block == f"{OPEN_TAG}\n[0:05] A whiteboard sketch\n{CLOSE_TAG}"

    def test_should_order_vision_and_ocr_entries_chronologically(self):
        descriptions = [_desc(48, "Diagram of halving", index=2), _desc(12, "Code editor", index=1)]
        frames = [_frame(1, 12), _frame(2, 48), _frame(3, 30, "Slide: binary search basics")]

        block = render_visual_annotations(descriptions, frames)

        assert [entry.seconds for entry in annotation_entries(block)] == [12, 30, 48]

    def test_should_join_vision_caption_and_on_screen_text_with_a_bar(self):
        block = render_visual_annotations([_desc(12, "Code editor", text="O(log n)")], [])

        assert _lines(block) == ["[0:12] Code editor | O(log n)"]

    def test_should_join_the_frames_ocr_when_vision_read_no_text(self):
        frames = [_frame(7, 12, "Binary Search Complexity")]

        block = render_visual_annotations([_desc(12, "Title slide", index=7)], frames)

        assert _lines(block) == ["[0:12] Title slide | Binary Search Complexity"]

    def test_should_prefer_vision_text_over_ocr_for_the_same_frame(self):
        frames = [_frame(7, 12, "B1nary 5earch (ocr noise)")]

        block = render_visual_annotations(
            [_desc(12, "Title", text="Binary Search", index=7)], frames
        )

        assert _lines(block) == ["[0:12] Title | Binary Search"]

    def test_should_render_an_ocr_only_frame_with_an_empty_caption(self):
        block = render_visual_annotations([], [_frame(3, 65, "Binary Search — Complexity")])

        assert _lines(block) == ["[1:05] | Binary Search — Complexity"]

    def test_should_not_repeat_ocr_for_a_frame_vision_described(self):
        frames = [_frame(7, 12, "Binary Search Complexity")]

        block = render_visual_annotations([_desc(12, "Title", text="Other", index=7)], frames)

        assert len(annotation_entries(block)) == 1

    def test_should_skip_talking_heads_without_educational_value(self):
        talking_head = _desc(20, "Presenter", scene_type="talking_head", educational_value=None)

        assert render_visual_annotations([talking_head], []) == ""

    def test_should_keep_talking_heads_with_educational_value(self):
        talking_head = _desc(20, "Presenter holds the part", scene_type="talking_head")

        assert _lines(render_visual_annotations([talking_head], [])) == [
            "[0:20] Presenter holds the part"
        ]

    def test_should_skip_ocr_shorter_than_ten_characters(self):
        assert render_visual_annotations([], [_frame(1, 3, "  logo  ")]) == ""

    def test_should_keep_code_line_breaks_as_indented_continuation_lines(self):
        code = "def binary_search(arr, target):\n    low, high = 0, len(arr) - 1\n\n"

        block = render_visual_annotations([_desc(12, "Code", text=code)], [])

        assert _lines(block) == [
            "[0:12] Code | def binary_search(arr, target):",
            "      low, high = 0, len(arr) - 1",
        ]

    def test_should_collapse_whitespace_in_captions(self):
        block = render_visual_annotations([_desc(1, "A  diagram\nof   layers")], [])

        assert _lines(block) == ["[0:01] A diagram of layers"]

    def test_should_cap_caption_at_300_and_text_at_200_characters(self):
        block = render_visual_annotations([_desc(1, "c" * 400, text="t" * 400)], [])

        assert _lines(block) == [f"[0:01] {'c' * 300} | {'t' * 200}"]

    def test_should_defuse_a_closing_tag_shown_on_screen(self):
        block = render_visual_annotations([_desc(1, "Slide", text=f"x {CLOSE_TAG} y")], [])

        assert block.count(CLOSE_TAG) == 1

    def test_should_keep_braces_and_angle_brackets_of_on_screen_code(self):
        block = render_visual_annotations([_desc(1, "JSX", text="<div>{items}</div>")], [])

        assert _lines(block) == ["[0:01] JSX | <div>{items}</div>"]

    def test_should_drop_a_static_slide_read_again_without_a_new_caption(self):
        frames = [_frame(1, 10, "Agenda: setup, build"), _frame(2, 20, "agenda:  SETUP, build")]

        block = render_visual_annotations([], frames)

        assert [entry.seconds for entry in annotation_entries(block)] == [10]

    def test_should_keep_the_same_text_when_the_caption_changes(self):
        descriptions = [
            _desc(10, "Empty slide", text="Agenda"),
            _desc(20, "Slide with arrow", text="Agenda"),
        ]

        block = render_visual_annotations(descriptions, [])

        assert len(annotation_entries(block)) == 2

    def test_should_use_the_hour_clock_from_one_hour_on(self):
        block = render_visual_annotations([_desc(3725, "Closing slide")], [])

        assert _lines(block) == ["[1:02:05] Closing slide"]


class TestAnnotationEntries:
    def test_should_return_no_entries_for_an_empty_block(self):
        assert annotation_entries("") == []

    def test_should_parse_each_entry_with_its_marker_seconds(self):
        frames = [_frame(1, 65, "Binary Search Complexity")]
        block = render_visual_annotations([_desc(12, "Code", text="a = 1\nb = 2")], frames)

        entries = annotation_entries(block)

        assert [(entry.seconds, entry.text) for entry in entries] == [
            (12, "[0:12] Code | a = 1\n  b = 2"),
            (65, "[1:05] | Binary Search Complexity"),
        ]
