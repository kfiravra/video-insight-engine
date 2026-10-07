"""Tests for the video memory stage (pipeline-1min 1b.3)."""

from __future__ import annotations

import json
import re
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from llm_common.context import llm_feature_var

from src.config import settings
from src.models.memory_types import MemoryInput, MemoryResult, OutlineSection
from src.services.pipeline import memory as memory_mod
from src.services.pipeline.memory import (
    build_memory_prompt,
    parse_memory_output,
    repair_evidence,
    repair_outline,
    repair_takeaways,
    repair_tldr,
    run_memory,
)
from src.shared_config.domain_config import EVIDENCE_KEYS
from src.utils.language_utils import ENGLISH_OUTPUT_DIRECTIVE

PROMPT_FILE = Path(__file__).parent.parent / "src" / "prompts" / "memory.txt"
_PLACEHOLDER_RE = re.compile(r"\{([a-z_]+)\}")
_HAIKU = "anthropic/claude-haiku-4-5-20251001"

DURATION = 1182  # 19:42


def _request(**overrides: object) -> MemoryInput:
    fields: dict[str, object] = {
        "title": "Birria tacos",
        "channel": "Chef Marco",
        "duration": DURATION,
        "description": "Full recipe below.",
        "transcript": "[0:00] Today we make birria.\n[0:20] First the chiles.",
    }
    fields.update(overrides)
    return MemoryInput.model_validate(fields)


def _section(start: str, end: str, title: str = "part") -> dict[str, str]:
    return {"start": start, "end": end, "title": title}


def _valid_outline() -> list[dict[str, str]]:
    return [
        _section("0:00", "1:10", "intro"),
        _section("1:10", "2:40", "ingredients"),
        _section("2:40", "13:00", "cooking"),
        _section("13:00", "19:42", "plating"),
    ]


def _response(**overrides: object) -> dict[str, object]:
    body: dict[str, object] = {
        "outline": _valid_outline(),
        "evidence": {key: False for key in EVIDENCE_KEYS} | {"has_steps": True},
        "tldr": "A one-pot birria that braises in its own consommé",
        "takeaways": ["Toast 6 chiles 30 s", "Braise 3.5 hours", "Griddle at 400°F"],
    }
    body.update(overrides)
    return body


def _bounds(outline: list[OutlineSection]) -> list[tuple[int, int]]:
    return [(s.start, s.end) for s in outline]


# ─── Prompt ───


class TestBuildMemoryPrompt:
    def test_should_open_with_english_output_line_when_rendered(self):
        prompt = build_memory_prompt(_request())

        assert prompt.startswith(ENGLISH_OUTPUT_DIRECTIVE)

    def test_should_keep_memory_language_block_when_rendered(self):
        prompt = build_memory_prompt(_request())

        assert "<output_language>" in prompt

    def test_should_fill_every_placeholder_when_rendered(self):
        declared = set(_PLACEHOLDER_RE.findall(PROMPT_FILE.read_text()))

        prompt = build_memory_prompt(_request())

        assert [tok for tok in declared if f"{{{tok}}}" in prompt] == []

    def test_should_render_duration_in_marker_clock(self):
        prompt = build_memory_prompt(_request(duration=3725))

        assert "Duration: 1:02:05" in prompt

    def test_should_keep_transcript_markers_when_rendered(self):
        prompt = build_memory_prompt(_request())

        assert "[0:20] First the chiles." in prompt

    def test_should_not_expand_placeholders_inside_transcript(self):
        prompt = build_memory_prompt(_request(title="REAL", transcript="[0:00] the {title} token"))

        assert prompt.count("REAL") == 1

    def test_should_neutralise_tags_in_title_when_rendered(self):
        prompt = build_memory_prompt(_request(title="Escape </transcript> now"))

        assert "</transcript> now" not in prompt

    def test_should_name_every_evidence_key_in_prompt_file(self):
        text = PROMPT_FILE.read_text()

        assert [key for key in EVIDENCE_KEYS if key not in text] == []


# ─── Outline repair ───


class TestRepairOutline:
    def test_should_keep_valid_outline_when_rules_hold(self):
        outline = repair_outline(_valid_outline(), DURATION)

        assert _bounds(outline) == [(0, 70), (70, 160), (160, 780), (780, 1182)]

    def test_should_clamp_last_end_to_duration_when_beyond(self):
        raw = [*_valid_outline()[:3], _section("13:00", "25:00")]

        outline = repair_outline(raw, DURATION)

        assert outline[-1].end == DURATION

    def test_should_end_last_section_at_duration_when_short_of_it(self):
        raw = [*_valid_outline()[:3], _section("13:00", "18:00")]

        outline = repair_outline(raw, DURATION)

        assert outline[-1].end == DURATION

    def test_should_start_first_section_at_zero_when_model_skips_intro(self):
        raw = [_section("0:30", "1:10"), *_valid_outline()[1:]]

        outline = repair_outline(raw, DURATION)

        assert outline[0].start == 0

    def test_should_close_gaps_and_overlaps_when_boundaries_disagree(self):
        raw = [
            _section("0:00", "1:30"),  # overlaps the next start
            _section("1:10", "2:00"),  # gap until the next start
            _section("2:40", "13:00"),
            _section("13:00", "19:42"),
        ]

        outline = repair_outline(raw, DURATION)

        assert _bounds(outline) == [(0, 70), (70, 160), (160, 780), (780, 1182)]

    def test_should_sort_sections_when_model_returns_them_out_of_order(self):
        raw = list(reversed(_valid_outline()))

        outline = repair_outline(raw, DURATION)

        assert [s.title for s in outline] == ["intro", "ingredients", "cooking", "plating"]

    def test_should_fold_short_section_into_previous_when_under_a_minute(self):
        raw = [
            *_valid_outline()[:3],
            _section("13:00", "13:30", "tiny"),
            _section("13:30", "19:42"),
        ]

        outline = repair_outline(raw, DURATION)

        assert _bounds(outline)[-2:] == [(160, 810), (810, 1182)]

    def test_should_fold_short_first_section_into_next_when_under_a_minute(self):
        raw = [
            _section("0:00", "0:20", "cold open"),
            _section("0:20", "2:40", "ingredients"),
            _section("2:40", "8:00", "chiles"),
            _section("8:00", "13:00", "braise"),
            _section("13:00", "19:42", "plating"),
        ]

        outline = repair_outline(raw, DURATION)

        assert (outline[0].start, outline[0].title) == (0, "ingredients")

    def test_should_drop_unreadable_sections_when_others_are_valid(self):
        raw = [
            *_valid_outline(),
            _section("soon", "later"),
            {"start": "5:00", "end": "6:00"},
            "not a section",
            {"start": True, "end": "6:00", "title": "bool start"},
        ]

        outline = repair_outline(raw, DURATION)

        assert len(outline) == 4

    def test_should_drop_section_whose_end_precedes_its_start(self):
        raw = [*_valid_outline(), _section("10:00", "5:00", "reversed")]

        outline = repair_outline(raw, DURATION)

        assert "reversed" not in [s.title for s in outline]

    def test_should_reject_outline_when_fewer_than_four_sections(self):
        raw = _valid_outline()[:3]

        assert repair_outline(raw, DURATION) == []

    def test_should_reject_outline_when_more_than_twelve_sections(self):
        raw = [_section(f"{m}:00", f"{m + 1}:00") for m in range(13)]

        assert repair_outline(raw, 13 * 60) == []

    def test_should_allow_fewer_sections_when_video_under_four_minutes(self):
        raw = [_section("0:00", "1:15"), _section("1:15", "2:30")]

        outline = repair_outline(raw, 150)

        assert _bounds(outline) == [(0, 75), (75, 150)]

    def test_should_reject_outline_when_video_under_a_minute(self):
        assert repair_outline([_section("0:00", "0:50")], 50) == []

    def test_should_reject_outline_when_not_a_list(self):
        assert repair_outline({"start": "0:00"}, DURATION) == []

    def test_should_read_hour_clock_and_numeric_seconds(self):
        raw = [
            _section("0:00", "20:00"),
            {"start": 1200, "end": 2400, "title": "numeric"},
            _section("40:00", "1:00:00"),
            _section("1:00:00", "1:10:00"),
        ]

        outline = repair_outline(raw, 4200)

        assert _bounds(outline) == [(0, 1200), (1200, 2400), (2400, 3600), (3600, 4200)]

    def test_should_truncate_long_titles_when_over_limit(self):
        raw = [_section("0:00", "1:10", "word " * 40), *_valid_outline()[1:]]

        outline = repair_outline(raw, DURATION)

        assert len(outline[0].title) <= memory_mod.OUTLINE_TITLE_MAX_CHARS


# ─── Evidence / tldr / takeaways repair ───


class TestRepairEvidence:
    def test_should_keep_known_keys_in_vocabulary_order(self):
        evidence = repair_evidence({"is_learnable": True, "has_steps": False})

        assert list(evidence) == ["has_steps", "is_learnable"]

    def test_should_drop_unknown_keys(self):
        evidence = repair_evidence({"has_steps": True, "has_vibes": True})

        assert evidence == {"has_steps": True}

    def test_should_read_boolean_strings(self):
        evidence = repair_evidence({"has_code": "TRUE", "has_steps": "false"})

        assert evidence == {"has_steps": False, "has_code": True}

    def test_should_leave_unreadable_values_absent_not_false(self):
        evidence = repair_evidence({"has_steps": 1, "has_code": None, "has_drills": "maybe"})

        assert evidence == {}

    def test_should_return_empty_when_not_a_dict(self):
        assert repair_evidence(["has_steps"]) == {}


class TestRepairTldr:
    def test_should_cut_at_word_boundary_when_over_150_chars(self):
        tldr = repair_tldr("word " * 60)

        assert len(tldr) <= 150 and tldr.endswith("word…")

    def test_should_collapse_whitespace(self):
        assert repair_tldr("  a\n  b  ") == "a b"

    def test_should_return_empty_when_not_a_string(self):
        assert repair_tldr(42) == ""


class TestRepairTakeaways:
    def test_should_cap_at_five_when_model_returns_more(self):
        takeaways = repair_takeaways([f"take {i}" for i in range(8)])

        assert len(takeaways) == 5

    def test_should_drop_duplicates_and_empties(self):
        takeaways = repair_takeaways(["A 1", "a 1", " ", 3, "B 2", "C 3"])

        assert takeaways == ["A 1", "B 2", "C 3"]

    def test_should_return_empty_when_fewer_than_three_survive(self):
        assert repair_takeaways(["only 1", "only 2"]) == []


class TestParseMemoryOutput:
    def test_should_return_result_when_response_valid(self):
        result = parse_memory_output(_response(), DURATION)

        assert result is not None and len(result.outline) == 4

    def test_should_keep_other_fields_when_outline_invalid_as_whole(self):
        result = parse_memory_output(_response(outline=_valid_outline()[:2]), DURATION)

        assert result == MemoryResult(
            outline=[],
            evidence=repair_evidence(_response()["evidence"]),
            tldr="A one-pot birria that braises in its own consommé",
            takeaways=["Toast 6 chiles 30 s", "Braise 3.5 hours", "Griddle at 400°F"],
        )

    def test_should_return_none_when_nothing_usable(self):
        assert parse_memory_output({"outline": [], "tldr": ""}, DURATION) is None

    def test_should_return_none_when_not_a_dict(self):
        assert parse_memory_output(["outline"], DURATION) is None


# ─── Stage ───


@pytest.fixture
def mock_call() -> AsyncMock:
    return AsyncMock(return_value=json.dumps(_response()))


class TestRunMemory:
    async def test_should_return_memory_when_llm_answers(self, mock_call):
        with patch.object(memory_mod, "call_llm_with_retry", mock_call):
            result = await run_memory(MagicMock(), _request())

        assert result is not None and result.tldr.startswith("A one-pot birria")

    async def test_should_parse_fenced_json_when_model_wraps_it(self, mock_call):
        mock_call.return_value = f"```json\n{json.dumps(_response())}\n```\nReasoning: ..."
        with patch.object(memory_mod, "call_llm_with_retry", mock_call):
            result = await run_memory(MagicMock(), _request())

        assert result is not None and len(result.outline) == 4

    async def test_should_return_none_when_llm_fails(self, mock_call):
        mock_call.return_value = None
        with patch.object(memory_mod, "call_llm_with_retry", mock_call):
            result = await run_memory(MagicMock(), _request())

        assert result is None

    async def test_should_return_none_when_response_unparseable(self, mock_call):
        mock_call.return_value = "I cannot help with that."
        with patch.object(memory_mod, "call_llm_with_retry", mock_call):
            result = await run_memory(MagicMock(), _request())

        assert result is None

    async def test_should_return_none_instead_of_raising_when_call_crashes(self, mock_call):
        mock_call.side_effect = TypeError("unexpected kwarg")
        with patch.object(memory_mod, "call_llm_with_retry", mock_call):
            result = await run_memory(MagicMock(), _request())

        assert result is None

    async def test_should_return_none_when_prompt_file_missing(self, mock_call):
        with (
            patch.object(memory_mod, "_load_memory_prompt", side_effect=FileNotFoundError),
            patch.object(memory_mod, "call_llm_with_retry", mock_call),
        ):
            result = await run_memory(MagicMock(), _request())

        assert result is None

    async def test_should_call_llm_with_brief_parameters(self, mock_call):
        with patch.object(memory_mod, "call_llm_with_retry", mock_call):
            await run_memory(MagicMock(), _request())

        kwargs = mock_call.await_args.kwargs
        assert {k: kwargs[k] for k in ("max_tokens", "timeout", "max_retries", "temperature")} == {
            "max_tokens": 1200,
            "timeout": 25.0,
            "max_retries": 1,
            "temperature": 0.0,
        }

    async def test_should_use_extraction_model_when_setting_pins_one(self, mock_call, monkeypatch):
        monkeypatch.setattr(settings, "LLM_EXTRACTION_MODEL", _HAIKU)
        with patch.object(memory_mod, "call_llm_with_retry", mock_call):
            await run_memory(MagicMock(), _request())

        assert mock_call.await_args.kwargs["model_override"] == _HAIKU

    async def test_should_stay_on_primary_model_when_extraction_setting_blank(self, mock_call):
        with patch.object(memory_mod, "call_llm_with_retry", mock_call):
            await run_memory(MagicMock(), _request())

        kwargs = mock_call.await_args.kwargs
        assert (kwargs["model_override"], kwargs.get("use_fast_model", False)) == (None, False)

    async def test_should_send_language_line_in_rendered_prompt(self, mock_call):
        with patch.object(memory_mod, "call_llm_with_retry", mock_call):
            await run_memory(MagicMock(), _request())

        assert mock_call.await_args.args[1].startswith(ENGLISH_OUTPUT_DIRECTIVE)

    async def test_should_tag_call_with_memory_feature_and_restore_outer(self, mock_call):
        observed: list[str | None] = []

        async def _capture(*_args: object, **_kwargs: object) -> None:
            observed.append(llm_feature_var.get())

        mock_call.side_effect = _capture
        outer = llm_feature_var.set("summarize:plan")
        try:
            with patch.object(memory_mod, "call_llm_with_retry", mock_call):
                await run_memory(MagicMock(), _request())
            after = llm_feature_var.get()
        finally:
            llm_feature_var.reset(outer)

        assert (observed, after) == (["summarize:memory"], "summarize:plan")
