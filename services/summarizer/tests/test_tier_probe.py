"""Tests for the tier probe (pipeline-1min 1b.1): input windows, prompt, validator, call.

The LLM boundary (``call_llm_with_retry``) and the prompt registry are patched;
everything else runs as production code.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.models.probe_types import TierProbe
from src.services.pipeline import tier_probe
from src.services.pipeline.tier_probe import (
    TierProbeInput,
    parse_tier_probe,
    render_tier_probe_prompt,
    run_tier_probe,
    transcript_windows,
)
from src.services.video.youtube import VideoContext, VideoData

_TEMPLATE = tier_probe.PROMPT_PATH.read_text(encoding="utf-8")
_PLACEHOLDER = re.compile(r"\{[a-z_]+\}")
_HAIKU_TAIL = "\n```\n\n**Reasoning:** The video shows a chef preparing a dish step by"


def _answer(**overrides: object) -> str:
    data: dict[str, object] = {
        "domain": "food",
        "format": "tutorial",
        "has_visual_demo": True,
        "confidence": 0.9,
    }
    data.update(overrides)
    return json.dumps(data)


def _input(transcript: str = "", **overrides: object) -> TierProbeInput:
    base: dict[str, object] = {
        "title": "Knife skills",
        "channel": "Chef",
        "duration": 600,
        "youtube_category": "Howto & Style",
        "description": "A video.",
        "transcript": transcript,
        "tags": ["knife", "cooking"],
    }
    base.update(overrides)
    return TierProbeInput(**base)


def _long_speech(words: int = 3000) -> str:
    return " ".join(f"word{i}" for i in range(words))


# ─── Validator ───────────────────────────────────────────────────────────────


class TestParseTierProbe:
    def test_should_accept_bare_json(self) -> None:
        assert parse_tier_probe(_answer()) == TierProbe(
            domain="food", format="tutorial", has_visual_demo=True, confidence=0.9
        )

    def test_should_accept_json_in_code_fences(self) -> None:
        probe = parse_tier_probe("```json\n" + _answer(domain="tech") + "\n```")
        assert probe is not None and probe.domain == "tech"

    def test_should_accept_json_followed_by_reasoning_cut_at_max_tokens(self) -> None:
        probe = parse_tier_probe("```json\n" + _answer(format="vlog") + _HAIKU_TAIL)
        assert probe is not None and probe.format == "vlog"

    def test_should_accept_json_followed_by_plain_prose(self) -> None:
        probe = parse_tier_probe(_answer() + "\nThis is clearly a cooking video.")
        assert probe is not None and probe.has_visual_demo is True

    def test_should_reject_a_domain_outside_the_registry(self) -> None:
        assert parse_tier_probe(_answer(domain="entertainment")) is None

    @pytest.mark.parametrize("key", ["domain", "format", "has_visual_demo", "confidence"])
    def test_should_reject_an_answer_missing_a_key(self, key: str) -> None:
        data = json.loads(_answer())
        del data[key]
        assert parse_tier_probe(json.dumps(data)) is None

    def test_should_reject_an_object_truncated_mid_answer(self) -> None:
        assert parse_tier_probe('```json\n{"domain": "food", "format": "tut') is None

    def test_should_reject_text_without_json(self) -> None:
        assert parse_tier_probe("I cannot classify this video.") is None

    def test_should_reject_a_non_numeric_confidence(self) -> None:
        assert parse_tier_probe(_answer(confidence="high")) is None

    def test_should_reject_a_non_boolean_visual_demo(self) -> None:
        assert parse_tier_probe(_answer(has_visual_demo="sometimes")) is None

    def test_should_default_an_unknown_format_to_commentary(self) -> None:
        probe = parse_tier_probe(_answer(format="essay"))
        assert probe is not None and probe.format == "commentary"

    def test_should_normalise_enum_case_and_spacing(self) -> None:
        probe = parse_tier_probe(_answer(domain=" Food ", format="TUTORIAL"))
        assert probe is not None and (probe.domain, probe.format) == ("food", "tutorial")

    @pytest.mark.parametrize(("raw", "expected"), [(1.5, 1.0), (-0.2, 0.0), (0.42, 0.42)])
    def test_should_clamp_confidence_to_unit_range(self, raw: float, expected: float) -> None:
        probe = parse_tier_probe(_answer(confidence=raw))
        assert probe is not None and probe.confidence == expected

    def test_should_ignore_extra_keys(self) -> None:
        probe = parse_tier_probe(_answer(reasoning="because", traits={"has_steps": True}))
        assert probe is not None and probe.domain == "food"


# ─── Transcript windows ──────────────────────────────────────────────────────


class TestTranscriptWindows:
    def test_should_split_short_transcript_without_duplication(self) -> None:
        text = " ".join(f"w{i}" for i in range(100))
        start, mid, end = transcript_windows(text)
        assert (start + mid + end).replace(" ", "") == text.replace(" ", "")

    def test_should_cap_each_window_at_700_chars_when_transcript_is_long(self) -> None:
        windows = transcript_windows(_long_speech())
        assert all(600 < len(w) <= 700 for w in windows)

    def test_should_anchor_windows_at_start_and_end(self) -> None:
        start, _mid, end = transcript_windows(_long_speech())
        assert start.startswith("word0 ") and end.endswith("word2999")

    def test_should_center_the_middle_window(self) -> None:
        text = _long_speech()
        center = len(text) // 2
        _start, mid, _end = transcript_windows(text)
        assert text[center - 40 : center + 40] in mid

    def test_should_cut_windows_on_word_boundaries(self) -> None:
        windows = transcript_windows(_long_speech())
        assert all(re.fullmatch(r"word\d+( word\d+)*", w) for w in windows)

    def test_should_collapse_whitespace_when_building_windows(self) -> None:
        assert transcript_windows("a  b\nc") == ("a", "b", "c")

    def test_should_return_empty_windows_for_empty_transcript(self) -> None:
        assert transcript_windows("") == ("", "", "")


# ─── Prompt ──────────────────────────────────────────────────────────────────


class TestRenderTierProbePrompt:
    def test_should_fill_every_placeholder_of_the_shipped_template(self) -> None:
        prompt = render_tier_probe_prompt(_TEMPLATE, _input(_long_speech()))
        assert _PLACEHOLDER.findall(prompt) == []

    def test_should_carry_the_english_output_language_line(self) -> None:
        prompt = render_tier_probe_prompt(_TEMPLATE, _input("שלום עולם " * 300))
        assert "Write every JSON value in English, whatever the language" in prompt

    def test_should_ask_for_bare_json_without_fences(self) -> None:
        prompt = render_tier_probe_prompt(_TEMPLATE, _input("speech"))
        assert "exactly one bare JSON object" in prompt and "no code fences" in prompt

    def test_should_put_the_output_format_after_the_video_data(self) -> None:
        prompt = render_tier_probe_prompt(_TEMPLATE, _input("speech"))
        assert prompt.rindex("</transcript_windows>") < prompt.rindex("<output_format>")

    def test_should_cap_description_at_500_chars(self) -> None:
        prompt = render_tier_probe_prompt("D:{description}|", _input(description="x" * 2000))
        assert prompt == "D:" + "x" * 500 + "|"

    def test_should_cap_tags_at_15(self) -> None:
        tags = [f"t{i}" for i in range(20)]
        prompt = render_tier_probe_prompt("{tags}", _input(tags=tags))
        assert prompt == ", ".join(tags[:15])

    def test_should_sanitize_braces_and_tags_in_metadata(self) -> None:
        prompt = render_tier_probe_prompt("T:{title}", _input(title="<b>{window_start}</b>"))
        assert prompt == "T:‹b›window_start‹/b›"

    def test_should_mark_missing_transcript(self) -> None:
        prompt = render_tier_probe_prompt("{window_start}|{window_end}", _input(""))
        assert prompt == "(no transcript)|(no transcript)"

    def test_should_mark_unknown_metadata(self) -> None:
        item = _input(duration=0, youtube_category=None, tags=[], description=" ", channel="")
        rendered = render_tier_probe_prompt(
            "{duration_minutes}|{youtube_category}|{tags}|{description}|{channel}", item
        )
        assert rendered == "unknown|unknown|none|none|Unknown"


class TestTierProbeInputFromVideo:
    @staticmethod
    def _video(context: VideoContext | None) -> VideoData:
        return VideoData(
            video_id="abc",
            title="Birria tacos",
            channel="Chef",
            duration=900,
            thumbnail_url=None,
            description="Recipe below",
            context=context,
        )

    def test_should_take_category_and_raw_tags_from_the_video_context(self) -> None:
        context = VideoContext("Howto & Style", "cooking", ["birria", "tacos"], ["birria"])
        item = TierProbeInput.from_video(self._video(context), "clean text")
        assert (item.youtube_category, item.tags, item.transcript) == (
            "Howto & Style",
            ["birria", "tacos"],
            "clean text",
        )

    def test_should_tolerate_a_video_without_context(self) -> None:
        item = TierProbeInput.from_video(self._video(None), "")
        assert (item.youtube_category, item.tags) == (None, [])


# ─── LLM call ────────────────────────────────────────────────────────────────


@pytest.fixture
def disk_prompt() -> Iterator[None]:
    with patch.object(tier_probe, "load_prompt_text", lambda path: Path(path).read_text()):
        yield


@pytest.mark.usefixtures("disk_prompt")
class TestRunTierProbe:
    async def test_should_return_the_validated_probe(self) -> None:
        llm = AsyncMock(return_value="```json\n" + _answer() + _HAIKU_TAIL)
        with patch.object(tier_probe, "call_llm_with_retry", llm):
            probe = await run_tier_probe(_input("speech"), MagicMock())
        assert probe is not None and (probe.domain, probe.has_visual_demo) == ("food", True)

    async def test_should_call_at_temperature_zero_with_80_max_tokens(self) -> None:
        llm = AsyncMock(return_value=_answer())
        with patch.object(tier_probe, "call_llm_with_retry", llm):
            await run_tier_probe(_input("speech"), MagicMock())
        kwargs = llm.await_args.kwargs
        assert (kwargs["temperature"], kwargs["max_tokens"]) == (0.0, 80)

    async def test_should_give_up_after_one_short_attempt(self) -> None:
        llm = AsyncMock(return_value=_answer())
        with patch.object(tier_probe, "call_llm_with_retry", llm):
            await run_tier_probe(_input("speech"), MagicMock())
        kwargs = llm.await_args.kwargs
        assert kwargs["max_retries"] == 0 and kwargs["timeout"] <= 5.0

    async def test_should_route_through_the_tier_probe_stage_model(self) -> None:
        llm = AsyncMock(return_value=_answer())
        stage_models = MagicMock()
        stage_models.get_stage_model.side_effect = {"tier_probe": "m/probe"}.get
        with (
            patch.object(tier_probe, "call_llm_with_retry", llm),
            patch.object(tier_probe, "settings", stage_models),
        ):
            await run_tier_probe(_input("speech"), MagicMock())
        kwargs = llm.await_args.kwargs
        assert (kwargs["stage_name"], kwargs["model_override"]) == ("tier_probe", "m/probe")

    async def test_should_send_the_rendered_windows_not_the_full_transcript(self) -> None:
        llm = AsyncMock(return_value=_answer())
        with patch.object(tier_probe, "call_llm_with_retry", llm):
            await run_tier_probe(_input(_long_speech(20000)), MagicMock())
        assert "word10000 " not in llm.await_args.args[1]

    async def test_should_reach_the_llm_service_at_temperature_zero(self) -> None:
        service = MagicMock()
        service.call_llm_fast = AsyncMock(return_value=_answer())
        no_override = MagicMock()
        no_override.get_stage_model.return_value = None
        with patch.object(tier_probe, "settings", no_override):
            probe = await run_tier_probe(_input("speech"), service)
        assert probe is not None and service.call_llm_fast.await_args.kwargs["temperature"] == 0.0

    async def test_should_return_none_when_the_llm_gives_nothing(self) -> None:
        with patch.object(tier_probe, "call_llm_with_retry", AsyncMock(return_value=None)):
            assert await run_tier_probe(_input("speech"), MagicMock()) is None

    async def test_should_return_none_when_the_call_raises(self) -> None:
        llm = AsyncMock(side_effect=TypeError("unexpected keyword argument"))
        with patch.object(tier_probe, "call_llm_with_retry", llm):
            assert await run_tier_probe(_input("speech"), MagicMock()) is None

    async def test_should_return_none_when_the_answer_is_invalid(self) -> None:
        llm = AsyncMock(return_value=_answer(domain="astrology"))
        with patch.object(tier_probe, "call_llm_with_retry", llm):
            assert await run_tier_probe(_input("speech"), MagicMock()) is None
