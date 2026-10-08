"""Synthesis service — prompt, validator, compact extraction, call, memory merge (1d.3).

Synthesis writes masterSummary + seoDescription only; it adds tldr/keyTakeaways
when memory left one empty, and the merge keeps memory's hero per field.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.models.memory_types import MemoryResult
from src.models.pipeline_types import SynthesisResult
from src.services.pipeline import synthesis as synthesis_mod
from src.services.pipeline.prompt_registry import declared_placeholders
from src.services.pipeline.synthesis import (
    EXTRACTION_SUMMARY_MAX_CHARS,
    PROMPT_PATH,
    SEO_MAX_CHARS,
    SYNTHESIS_KEYS,
    SynthesisInput,
    build_synthesis_dict,
    build_synthesis_prompt,
    compact_extraction,
    parse_synthesis_output,
    synthesize,
)

_VIDEO_MEMORY = "<video_memory>\ndomains: food · goal: Cook lasagna\n</video_memory>"
_RESPONSE = {
    "masterSummary": "Chris builds a lasagna in three layers.\n\nOpen the Steps tab.",
    "seoDescription": "Bake BA's best lasagna in three layers",
}
_TLDR_RULE = "- tldr: one sentence"
_TAKEAWAYS_RULE = "- keyTakeaways: 3-5 items"


@pytest.fixture(autouse=True)
def _disk_prompt():
    """Render the shipped .txt — the registry is an external service here."""
    with patch.object(synthesis_mod, "_load_synthesis_prompt", PROMPT_PATH.read_text):
        yield


def _request(**overrides: Any) -> SynthesisInput:
    fields: dict[str, Any] = {
        "title": "BA's Best Lasagna",
        "channel": "Chris",
        "duration": 1001,
        "output_type": "food",
        "extraction_summary": '{"food":{"steps":["boil"]}}',
        "video_context": _VIDEO_MEMORY,
        "tab_labels": ("🛒 14 Ingredients", "👨‍🍳 Steps"),
    }
    return SynthesisInput(**{**fields, **overrides})


class TestBuildSynthesisPrompt:
    def test_should_request_only_summary_and_seo_when_hero_fallback_is_false(self) -> None:
        prompt = build_synthesis_prompt(_request())

        assert '"masterSummary" and "seoDescription"' in prompt
        assert "tldr" not in prompt and "keyTakeaways" not in prompt

    def test_should_add_the_tldr_and_takeaway_rules_when_hero_fallback_is_true(self) -> None:
        prompt = build_synthesis_prompt(_request(hero_fallback=True))

        assert _TLDR_RULE in prompt and _TAKEAWAYS_RULE in prompt
        assert '"tldr" and "keyTakeaways"' in prompt

    def test_should_place_the_video_memory_block_verbatim(self) -> None:
        prompt = build_synthesis_prompt(_request())

        assert _VIDEO_MEMORY in prompt
        assert "<video_context>" not in prompt

    def test_should_say_not_available_when_there_is_no_video_memory(self) -> None:
        prompt = build_synthesis_prompt(_request(video_context=""))

        assert "<video_memory>\nNot available\n</video_memory>" in prompt

    def test_should_list_each_tab_label_on_its_own_line(self) -> None:
        prompt = build_synthesis_prompt(_request())

        assert "<tabs>\n- 🛒 14 Ingredients\n- 👨‍🍳 Steps\n</tabs>" in prompt

    def test_should_say_none_when_there_are_no_tabs(self) -> None:
        prompt = build_synthesis_prompt(_request(tab_labels=()))

        assert "<tabs>\n(none)\n</tabs>" in prompt

    def test_should_render_unknown_channel_and_duration_when_missing(self) -> None:
        prompt = build_synthesis_prompt(_request(channel=None, duration=None))

        assert "Channel: Unknown" in prompt and "Duration: unknown minutes" in prompt

    @pytest.mark.parametrize("hero_fallback", [False, True])
    def test_should_leave_no_placeholder_unreplaced(self, hero_fallback: bool) -> None:
        prompt = build_synthesis_prompt(_request(hero_fallback=hero_fallback))

        slots = declared_placeholders(PROMPT_PATH.read_text())
        assert [slot for slot in slots if f"{{{slot}}}" in prompt] == []

    def test_should_cap_the_extraction_summary(self) -> None:
        prompt = build_synthesis_prompt(_request(extraction_summary="x" * 10_000))

        assert "x" * EXTRACTION_SUMMARY_MAX_CHARS in prompt
        assert "x" * (EXTRACTION_SUMMARY_MAX_CHARS + 1) not in prompt


class TestParseSynthesisOutput:
    @pytest.mark.parametrize(
        "data", [None, [], {"seoDescription": "s"}, {"masterSummary": "  "}, {"masterSummary": 3}]
    )
    def test_should_raise_when_there_is_no_master_summary(self, data: object) -> None:
        with pytest.raises(ValueError):
            parse_synthesis_output(data)

    def test_should_cut_the_seo_description_at_a_word_boundary(self) -> None:
        result = parse_synthesis_output({**_RESPONSE, "seoDescription": "word " * 60})

        assert len(result.seo_description) <= SEO_MAX_CHARS
        assert result.seo_description.endswith("word…")

    def test_should_keep_tldr_and_takeaways_when_the_model_returns_them(self) -> None:
        result = parse_synthesis_output(
            {**_RESPONSE, "tldr": "Three layers, no ricotta", "keyTakeaways": ["a 1", "b 2", "c 3"]}
        )

        assert (result.tldr, result.key_takeaways) == (
            "Three layers, no ricotta",
            ["a 1", "b 2", "c 3"],
        )

    def test_should_default_wrongly_typed_fields(self) -> None:
        result = parse_synthesis_output(
            {**_RESPONSE, "seoDescription": 7, "tldr": ["x"], "keyTakeaways": "a"}
        )

        assert (result.seo_description, result.tldr, result.key_takeaways) == ("", "", [])


class TestCompactExtraction:
    @staticmethod
    def _large() -> dict[str, Any]:
        items = [{"text": f"item number {i} with some detail", "timestamp": i} for i in range(40)]
        return {
            "food": {"ingredients": list(items), "steps": list(items), "tips": list(items)},
            "travel": {"spots": list(items)},
        }

    def test_should_keep_every_domain_and_field_when_lists_are_capped(self) -> None:
        compacted = json.loads(compact_extraction(self._large(), max_chars=1500))

        assert {d: sorted(f) for d, f in compacted.items()} == {
            "food": ["ingredients", "steps", "tips"],
            "travel": ["spots"],
        }

    def test_should_mark_capped_lists_with_the_number_left_out(self) -> None:
        compacted = json.loads(compact_extraction(self._large(), max_chars=4000))

        assert compacted["food"]["steps"][-1] == "+32 more"

    def test_should_stay_within_max_chars(self) -> None:
        assert len(compact_extraction(self._large(), max_chars=1500)) <= 1500

    def test_should_drop_empty_values(self) -> None:
        extraction = {
            "food": {"steps": [], "tips": [{"text": " "}], "meta": {"yield": "4"}},
            "x": None,
        }

        assert compact_extraction(extraction) == '{"food":{"meta":{"yield":"4"}}}'

    def test_should_be_deterministic(self) -> None:
        assert compact_extraction(self._large(), max_chars=1500) == compact_extraction(
            self._large(), max_chars=1500
        )

    @pytest.mark.parametrize("extraction", [None, {}, {"food": {"steps": []}}])
    def test_should_return_empty_text_when_nothing_was_extracted(self, extraction: Any) -> None:
        assert compact_extraction(extraction) == ""


class TestSynthesize:
    @staticmethod
    async def _call(response: str | None, **kwargs: Any) -> tuple[SynthesisResult, AsyncMock]:
        call = AsyncMock(return_value=response)
        with patch.object(synthesis_mod, "call_llm_with_retry", call):
            result = await synthesize(
                MagicMock(), "Lasagna", "Chris", 1001, "food", "{}", _VIDEO_MEMORY, **kwargs
            )
        return result, call

    async def test_should_call_the_fast_model_with_the_trimmed_budget(self) -> None:
        _, call = await self._call(json.dumps(_RESPONSE))

        kwargs = call.await_args.kwargs
        assert (kwargs["max_tokens"], kwargs["timeout"], kwargs["max_retries"]) == (1200, 20.0, 1)
        assert (kwargs["use_fast_model"], kwargs["json_mode"], kwargs["stage_name"]) == (
            True,
            True,
            "synthesis",
        )

    async def test_should_return_the_parsed_summary(self) -> None:
        result, _ = await self._call(json.dumps(_RESPONSE))

        assert result.master_summary.startswith("Chris builds a lasagna")

    async def test_should_ask_for_the_hero_when_hero_fallback_is_set(self) -> None:
        _, call = await self._call(json.dumps(_RESPONSE), hero_fallback=True)

        assert _TLDR_RULE in call.await_args.args[1]

    async def test_should_raise_when_the_response_is_not_json(self) -> None:
        with pytest.raises(ValueError, match="Failed to parse"):
            await self._call("Sorry, I cannot do that.")

    async def test_should_raise_when_every_attempt_failed(self) -> None:
        with pytest.raises(ValueError, match="failed after retries"):
            await self._call(None)


class TestBuildSynthesisDict:
    _RESULT = SynthesisResult(
        master_summary="Summary.",
        seo_description="SEO.",
        tldr="Synthesis tldr",
        key_takeaways=["s1", "s2", "s3"],
    )

    def test_should_take_the_hero_from_memory_and_the_text_from_synthesis(self) -> None:
        memory = MemoryResult(tldr="Memory tldr", takeaways=["m1", "m2", "m3"])

        assert build_synthesis_dict(memory, self._RESULT) == {
            "tldr": "Memory tldr",
            "keyTakeaways": ["m1", "m2", "m3"],
            "masterSummary": "Summary.",
            "seoDescription": "SEO.",
        }

    def test_should_fill_each_hero_field_memory_left_empty_from_synthesis(self) -> None:
        merged = build_synthesis_dict(MemoryResult(tldr="Memory tldr"), self._RESULT)

        assert (merged["tldr"], merged["keyTakeaways"]) == ("Memory tldr", ["s1", "s2", "s3"])

    def test_should_use_the_synthesis_hero_when_memory_failed(self) -> None:
        merged = build_synthesis_dict(None, self._RESULT)

        assert (merged["tldr"], merged["keyTakeaways"]) == ("Synthesis tldr", ["s1", "s2", "s3"])

    def test_should_keep_the_meta_key_order(self) -> None:
        assert tuple(build_synthesis_dict(None, self._RESULT)) == SYNTHESIS_KEYS

    def test_should_return_an_empty_dict_when_every_field_is_empty(self) -> None:
        assert build_synthesis_dict(MemoryResult(), None) == {}
