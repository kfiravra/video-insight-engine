"""Size-bounded parallel batches of the translation calls (pipeline-1min round 3).

``char_batches`` cuts a string list into consecutive runs within a source-
character budget, shared by the output translation (``translate_to_source``)
and the RAG transcript text. These tests fake the one translation call.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Iterator
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.services.pipeline import translation, translation_flat
from src.services.pipeline.translation import _MAX_CONCURRENT_TRANSLATION_BATCHES
from src.services.pipeline.translation_flat import (
    TRANSLATION_BATCH_CHARS,
    answer_size,
    answer_tokens,
    char_batches,
    translate_flat_list,
)

_SLEEP_S = 0.2


def _output(count: int, chars: int = 300) -> dict[str, Any]:
    """An English output of ``count`` places, each description ≈ ``chars`` long."""
    spots = [
        {"name": f"Place number {i}", "description": f"Place {i} is " + "lovely " * (chars // 7)}
        for i in range(count)
    ]
    return {
        "tabs": [
            {"id": "t", "label": "Spots", "component": "spot_explorer", "props": {"spots": spots}}
        ],
        "meta": {},
    }


class _Recorder:
    """A fake ``translate_flat_list``: records each call, sleeps, answers ``HE: …``."""

    def __init__(self, fail_above: int | None = None, echo_marker: str | None = None) -> None:
        self.calls: list[list[str]] = []
        self.in_flight = 0
        self.peak = 0
        self.fail_above = fail_above
        self.echo_marker = echo_marker

    async def __call__(
        self, _llm: Any, strings: list[str], *_a: Any, **_k: Any
    ) -> list[str] | None:
        self.calls.append(list(strings))
        self.in_flight += 1
        self.peak = max(self.peak, self.in_flight)
        await asyncio.sleep(_SLEEP_S)
        self.in_flight -= 1
        if self.fail_above is not None and len(strings) > self.fail_above:
            return None
        if self.echo_marker is not None and any(self.echo_marker in s for s in strings):
            return list(strings)
        return [f"HE: {s}" for s in strings]


@pytest.fixture
def recorder() -> Iterator[_Recorder]:
    fake = _Recorder()
    with patch.object(translation, "translate_flat_list", new=fake):
        yield fake


def _strings(tree: Any) -> list[str]:
    pairs: list[tuple[Any, str]] = []
    translation._collect_strings(tree, (), pairs)
    return [value for _, value in pairs]


class TestCharBatches:
    def test_should_keep_every_batch_within_the_character_budget(self):
        texts = ["x" * 900] * 10

        assert all(sum(len(t) for t in batch) <= 2500 for batch in char_batches(texts, 2500))

    def test_should_keep_the_texts_in_order(self):
        texts = [f"{i}" * 700 for i in range(10)]

        assert [t for batch in char_batches(texts, 2500) for t in batch] == texts

    def test_should_send_a_string_longer_than_the_budget_alone(self):
        texts = ["a" * 10, "b" * 3000, "c" * 10]

        assert [len(batch) for batch in char_batches(texts, 2500)] == [1, 1, 1]

    def test_should_cut_by_count_when_the_strings_are_short(self):
        texts = ["label"] * 250

        assert [len(batch) for batch in char_batches(texts, 2500, max_count=100)] == [100, 100, 50]

    def test_should_put_fewer_chinese_blocks_in_a_call_than_hebrew_blocks_of_the_same_length(
        self,
    ):
        chinese, hebrew = ["你" * 400] * 10, ["ש" * 400] * 10

        assert len(char_batches(chinese, 2500)) > len(char_batches(hebrew, 2500))


class TestAnswerSize:
    def test_should_count_an_alphabetic_character_once(self):
        assert answer_size("hello") == 5

    def test_should_weigh_a_han_character_as_two_and_a_half(self):
        assert answer_size("你好") == 5

    @pytest.mark.parametrize("text", ["こんにちは", "안녕하세요"], ids=["kana", "hangul"])
    def test_should_weigh_kana_and_hangul_as_one_and_a_half(self, text):
        assert answer_size(text) == 7.5

    def test_should_keep_the_cjk_ranges_escaped_in_the_source(self):
        # A normalizing editor maps literal U+F900 ideographs to other code points.
        assert (
            translation_flat._HAN.pattern.isascii()
            and translation_flat._KANA_HANGUL.pattern.isascii()
        )


class TestAnswerTokens:
    def test_should_reserve_a_token_per_source_character_above_the_wrapper(self):
        assert answer_tokens(["a" * 300, "b" * 200]) == 1524

    def test_should_reserve_more_for_han_text(self):
        assert answer_tokens(["你" * 400]) == 2024

    def test_should_never_reserve_more_than_16384_tokens(self):
        assert answer_tokens(["a" * 20000]) == 16384

    async def test_should_send_the_sized_max_tokens_with_the_call(self):
        llm_call = AsyncMock(return_value=None)

        with patch.object(translation_flat, "call_llm_with_retry", llm_call):
            await translate_flat_list(MagicMock(), ["a" * 300, "b" * 200], "en", "he")

        assert llm_call.call_args.kwargs["max_tokens"] == 1524


class TestOutputBatches:
    async def test_should_keep_every_call_within_the_character_budget(self, recorder):
        await translation.translate_to_source(MagicMock(), _output(40), "he")

        assert max(sum(len(s) for s in call) for call in recorder.calls) <= TRANSLATION_BATCH_CHARS

    async def test_should_split_a_large_output_into_several_calls(self, recorder):
        await translation.translate_to_source(MagicMock(), _output(40), "he")

        assert len(recorder.calls) > 1

    async def test_should_start_every_batch_at_once_when_they_fit_the_call_limit(self, recorder):
        await translation.translate_to_source(MagicMock(), _output(40), "he")

        assert recorder.peak == len(recorder.calls)

    async def test_should_take_about_one_batch_of_time_when_the_batches_run_in_parallel(
        self, recorder
    ):
        started = time.monotonic()

        await translation.translate_to_source(MagicMock(), _output(40), "he")

        # Serial calls would take len(calls) sleeps; one spare sleep absorbs a busy box.
        assert time.monotonic() - started < (len(recorder.calls) - 1) * _SLEEP_S

    async def test_should_translate_every_string_in_order_when_split_into_batches(self, recorder):
        output = _output(40)

        result = await translation.translate_to_source(MagicMock(), output, "he")

        translated = _strings({"tabs": result["sourceLanguage"]["tabs"]})
        assert translated == [f"HE: {s}" for s in _strings({"tabs": output["tabs"]})]

    async def test_should_keep_retry_halves_within_the_call_limit(self):
        # ~15 batches, each failing whole and retried as two halves.
        fake = _Recorder(fail_above=4)

        with patch.object(translation, "translate_flat_list", new=fake):
            await translation.translate_to_source(MagicMock(), _output(120), "he")

        assert fake.peak == _MAX_CONCURRENT_TRANSLATION_BATCHES

    async def test_should_still_retry_a_failed_batch_as_two_halves(self):
        # One batch of 9 strings (the tab label + 4 names + 4 descriptions)
        # fails whole; its halves of 4 and 5 go through.
        fake = _Recorder(fail_above=5)

        with patch.object(translation, "translate_flat_list", new=fake):
            result = await translation.translate_to_source(MagicMock(), _output(4), "he")

        spots = result["sourceLanguage"]["tabs"][0]["props"]["spots"]
        assert [spot["name"] for spot in spots] == [f"HE: Place number {i}" for i in range(4)]

    async def test_should_keep_an_echoed_batch_english_when_its_halves_echo_too(self):
        fake = _Recorder(echo_marker="Place number 7")

        with patch.object(translation, "translate_flat_list", new=fake):
            result = await translation.translate_to_source(MagicMock(), _output(40), "he")

        assert result["sourceLanguage"]["tabs"][0]["props"]["spots"][7]["name"] == "Place number 7"

    async def test_should_ship_the_other_batches_when_one_batch_echoes(self):
        fake = _Recorder(echo_marker="Place number 7")

        with patch.object(translation, "translate_flat_list", new=fake):
            result = await translation.translate_to_source(MagicMock(), _output(40), "he")

        assert result["sourceLanguage"]["tabs"][0]["props"]["spots"][30]["name"] == (
            "HE: Place number 30"
        )

    async def test_should_retry_an_echoed_batch_as_two_halves(self):
        fake = _Recorder(echo_marker="Place number 7")

        with patch.object(translation, "translate_flat_list", new=fake):
            await translation.translate_to_source(MagicMock(), _output(40), "he")

        assert sum(1 for call in fake.calls if any("Place number 7" in s for s in call)) == 2

    async def test_should_send_every_batch_as_a_translation_full_call(self):
        llm_call = AsyncMock(return_value=None)

        with patch.object(translation_flat, "call_llm_with_retry", llm_call):
            await translation.translate_to_source(MagicMock(), _output(40), "he")

        assert {call.kwargs["stage_name"] for call in llm_call.call_args_list} == {
            "translation_full"
        }
