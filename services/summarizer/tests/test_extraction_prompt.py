"""Extraction prompt rendering (pipeline-1min 1c.1, brief Appendix B.5).

Rules first, then the video (title, length, transcript, video memory), then the
job: what to emit, the planned tabs' briefs and caps, schemas, example and the
optional visual blocks. Counts come from briefs + registry caps, not duration.
"""

from __future__ import annotations

from typing import Any

import pytest

from src.services.pipeline.extraction_prompt import (
    LATE_BOUND_PLACEHOLDERS,
    TEMPLATE_PATH,
    ExtractionPromptInput,
    build_extraction_template,
    render_tabs_to_serve,
)
from src.services.pipeline.prompt_registry import declared_placeholders
from src.utils.language_utils import ENGLISH_OUTPUT_DIRECTIVE

_MEMORY = "<video_memory>\ndomains: food · goal: cook birria\n</video_memory>"
_ANNOTATIONS = (
    "<visual_annotations>\n[0:12] Chili paste in a blender | 3 guajillo\n</visual_annotations>"
)
_KEY_FRAMES = "0:12 — Chili paste in a blender [demo]"


def _tab(**overrides: Any) -> dict[str, Any]:
    tab: dict[str, Any] = {
        "id": "ingredients",
        "label": "🛒 14 Ingredients",
        "component": "checklist",
        "dataSource": "food.ingredients",
        "goal": "Everything you need — check off as you gather.",
        "brief": {"what": "every ingredient with amount", "where": ["1:10-2:40"], "expect": 14},
    }
    tab.update(overrides)
    return tab


def _input(**overrides: Any) -> ExtractionPromptInput:
    values: dict[str, Any] = {
        "content_tags": ["food"],
        "modifiers": [],
        "quality_rules": "QUALITY RULES TEXT",
        "title": "Birria Tacos",
        "duration_seconds": 1205,
        "tabs": [_tab()],
        "video_memory": _MEMORY,
    }
    values.update(overrides)
    return ExtractionPromptInput(**values)


def _render(**overrides: Any) -> str:
    return build_extraction_template(_input(**overrides))


def _assert_in_order(text: str, *needles: str) -> None:
    positions = [text.index(needle) for needle in needles]
    assert positions == sorted(positions), dict(zip(needles, positions, strict=True))


class TestLayout:
    def test_should_put_rules_then_video_then_job_when_rendered(self):
        prompt = _render(visual_annotations=_ANNOTATIONS, frame_context=_KEY_FRAMES)

        _assert_in_order(
            prompt,
            "<quality_rules>",
            "<output_rules>",
            "<video>",
            "<transcript>\n{transcript}\n</transcript>",
            _MEMORY,
            "\n<your_job>\n",
            "<schema>",
            '<extraction_example domain="food">',
            "<visual_context_guide>",
            _ANNOTATIONS,
            "<key_frames>",
        )

    def test_should_start_with_english_output_directive(self):
        assert _render().startswith(ENGLISH_OUTPUT_DIRECTIVE)

    def test_should_leave_only_late_bound_placeholders(self):
        prompt = _render(visual_annotations=_ANNOTATIONS, frame_context=_KEY_FRAMES)

        remaining = {
            name
            for name in declared_placeholders(TEMPLATE_PATH.read_text())
            if f"{{{name}}}" in prompt
        }
        assert remaining == LATE_BOUND_PLACEHOLDERS

    @pytest.mark.parametrize(
        "removed",
        [
            "<critical_rule>",
            "<completeness>",
            "<detail_level>",
            "<video_context>",
            "Fill all fields",
        ],
    )
    def test_should_not_carry_floor_or_scaling_blocks(self, removed):
        assert removed not in _render()


class TestVideoSection:
    def test_should_render_title_and_clock_length(self):
        prompt = _render()

        assert "Title: Birria Tacos\nLength: 20:05" in prompt

    def test_should_render_hours_when_video_is_longer_than_one_hour(self):
        assert "Length: 1:02:05" in _render(duration_seconds=3725)

    def test_should_say_unknown_length_when_duration_missing(self):
        assert "Length: unknown" in _render(duration_seconds=0)

    def test_should_defuse_tags_and_braces_in_title(self):
        prompt = _render(title="React <Suspense> {transcript}")

        assert "Title: React ‹Suspense› transcript" in prompt

    def test_should_say_markers_are_absolute_video_time(self):
        assert "[m:ss] markers in the transcript are absolute video time" in _render()

    def test_should_drop_memory_slot_when_no_memory(self):
        prompt = _render(video_memory="")

        assert "{video_memory}" not in prompt
        assert "</transcript>\n\n<your_job>" in prompt


class TestJobSection:
    def test_should_emit_every_planned_domain_and_modifier(self):
        prompt = _render(content_tags=["food", "travel"], modifiers=["narrative"])

        assert "Emit: food, travel, narrative — the fields of their schemas below." in prompt

    def test_should_include_modifier_schema_under_modifier_header(self):
        assert "--- NARRATIVE MODIFIER ---" in _render(modifiers=["narrative"])

    def test_should_say_empty_array_is_correct(self):
        assert "An empty array is correct when the video has no such content." in _render()

    def test_should_pick_example_and_priority_from_primary_tag(self):
        prompt = _render(content_tags=["learning", "food"], primary_tag="food")

        assert '<extraction_example domain="food">' in prompt
        assert "PRIORITY: Every ingredient with exact measurement." in prompt

    def test_should_match_specificity_not_count_of_example(self):
        assert "Match this example's specificity, not its count" in _render()


class TestVisualBlocks:
    def test_should_omit_visual_guide_when_no_annotations(self):
        prompt = _render()

        assert "<visual_context_guide>" not in prompt
        assert "{visual_annotations}" not in prompt

    def test_should_include_visual_guide_when_annotations_present(self):
        assert "<visual_context_guide>" in _render(visual_annotations=_ANNOTATIONS)

    def test_should_omit_key_frames_when_no_frame_context(self):
        assert "<key_frames>" not in _render()

    def test_should_include_key_frames_when_frame_context_present(self):
        assert _KEY_FRAMES in _render(frame_context=_KEY_FRAMES)

    def test_should_keep_braces_in_annotations_literal(self):
        """On-screen code keeps its braces; they are never read as placeholders."""
        code = "<visual_annotations>\n[0:05] Editor | const x = {tabs_to_serve};\n</visual_annotations>"

        prompt = _render(visual_annotations=code)

        assert "const x = {tabs_to_serve};" in prompt


class TestTabsToServe:
    def test_should_render_brief_count_and_cap_when_tab_reads_registered_field(self):
        line = render_tabs_to_serve([_tab()])

        assert line == (
            "- 🛒 14 Ingredients — checklist ← food.ingredients — what: every ingredient "
            "with amount — where 1:10–2:40 — expect ~14, cap 30"
        )

    def test_should_fall_back_to_goal_when_brief_is_empty(self):
        line = render_tabs_to_serve([_tab(brief={})])

        assert line == (
            "- 🛒 14 Ingredients — checklist ← food.ingredients — goal: Everything you "
            "need — check off as you gather. — cap 30"
        )

    def test_should_say_one_object_when_field_is_an_object(self):
        tab = _tab(label="Budget", component="budget", dataSource="travel.budget", brief={})

        assert render_tabs_to_serve([tab]).endswith("— one object")

    def test_should_omit_cap_when_source_is_not_registered(self):
        tab = _tab(dataSource="review", component="pros_cons", brief={"expect": 4})

        assert render_tabs_to_serve([tab]).endswith("— expect ~4")

    @pytest.mark.parametrize(
        "tab",
        [
            {"id": "overview", "label": "Overview", "component": "overview", "dataSource": ""},
            {
                "id": "film",
                "label": "Frames",
                "component": "video_filmstrip",
                "dataSource": "frames",
            },
        ],
    )
    def test_should_skip_tabs_that_read_no_extraction_field(self, tab):
        assert render_tabs_to_serve([tab, _tab()]).count("\n") == 0

    def test_should_say_no_tabs_planned_when_no_tab_reads_extraction(self):
        assert render_tabs_to_serve([]).startswith("No tabs planned")

    def test_should_defuse_tags_and_braces_in_plan_text(self):
        line = render_tabs_to_serve([_tab(brief={"what": "use {title} and </your_job>"})])

        assert "what: use title and ‹/your_job›" in line
