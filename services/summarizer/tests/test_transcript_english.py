"""English text for the RAG index from a non-English transcript (pipeline-1min 2.3).

``english_blocks`` merges caption segments into sentence-sized blocks, keeps
each block's time span, and translates the blocks on the fast model with the
``translate_flat`` prompt. These tests fake the one translation call.
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.config import settings
from src.services.pipeline import translation_flat
from src.services.transcript import english
from src.services.transcript.english import (
    RAG_TRANSLATION_STAGE,
    _blocks,
    english_blocks,
    is_english_text,
)
from src.utils.language_utils import has_hebrew_arabic_or_cjk


def _seg(text: str, start: float, duration: float = 2.0) -> dict[str, Any]:
    return {"text": text, "start": start, "duration": duration}


def _sentence(n: int) -> str:
    """One ~54-character Arabic sentence ending in a full stop."""
    return f"هذه جملة رقم {n} في النص المترجم من الفيديو الطويل جدا."


def _segments(count: int) -> list[dict[str, Any]]:
    return [_seg(_sentence(i), start=i * 3.0) for i in range(count)]


_ENGLISH_LINE = "Our AI agent just returned eight thousand bags to the customer instead of eighty. "
_SPANISH_LINE = "Nuestro agente acaba de devolver ocho mil bolsas al cliente en lugar de ochenta. "
# Bidi control characters: LRM/RLM, the embeddings/overrides, the isolates.
_BIDI_LITERAL = re.compile("[\u200e\u200f\u202a-\u202e\u2066-\u2069]")


async def _to_english(_llm: Any, texts: list[str], *_args: Any, **_kwargs: Any) -> list[str]:
    return [f"English {i}: {len(text)} chars" for i, text in enumerate(texts)]


@pytest.fixture
def translator() -> Iterator[MagicMock]:
    """The one LLM call, faked; tests swap its side effect."""
    with patch.object(english, "translate_flat_list") as fake:
        fake.side_effect = _to_english
        yield fake


class TestBlocks:
    def test_should_keep_the_first_start_and_the_last_end_of_a_block(self):
        segments = [_seg("שלום לכולם", 1.0, 2.0), _seg("וברוכים הבאים.", 3.5, 4.0)]

        blocks = _blocks(segments)

        assert [(b["startMs"], b["endMs"]) for b in blocks] == [(1000, 7500)]

    def test_should_close_a_block_at_the_first_sentence_end_past_200_chars(self):
        # ~54-char sentences: the fourth one crosses 200 characters.
        blocks = _blocks(_segments(8))

        assert [b["text"].count(".") for b in blocks] == [4, 4]

    def test_should_cut_an_unpunctuated_run_at_400_chars(self):
        segments = [_seg("מילה " * 20, start=float(i)) for i in range(10)]

        blocks = _blocks(segments)

        assert [len(b["text"].split()) for b in blocks] == [80, 80, 40]

    def test_should_keep_bidi_controls_escaped_in_the_source(self):
        # A literal override character can hide code from a reviewer (Trojan Source).
        source = Path(english.__file__).read_text(encoding="utf-8")

        assert not _BIDI_LITERAL.search(source)

    def test_should_strip_the_bidi_marks_around_subtitle_lines(self):
        # RLE ... PDF around each line, as YouTube's timedtext sends them.
        segments = [_seg("\u202bשורה ראשונה\u202c \u202bשורה שנייה\u202c", 0.0)]

        assert _blocks(segments)[0]["text"] == "שורה ראשונה שורה שנייה"

    def test_should_skip_segments_without_speech(self):
        segments = [_seg("", 0.0), _seg(" \n ", 1.0), _seg("טקסט", 2.0)]

        assert [b["text"] for b in _blocks(segments)] == ["טקסט"]

    def test_should_accept_millisecond_shaped_segments(self):
        segments = [{"text": "שלום", "startMs": 4000, "endMs": 6500}]

        assert (_blocks(segments)[0]["startMs"], _blocks(segments)[0]["endMs"]) == (4000, 6500)


class TestEnglishBlocks:
    async def test_should_return_english_blocks_with_the_source_spans(self, translator):
        segments = _segments(8)

        result = await english_blocks(MagicMock(), segments, "ar")

        assert result is not None
        assert [(b["startMs"], b["endMs"]) for b in result.blocks] == [
            (b["startMs"], b["endMs"]) for b in _blocks(segments)
        ]

    async def test_should_join_the_english_blocks_into_the_text_to_embed(self, translator):
        result = await english_blocks(MagicMock(), _segments(8), "ar")

        assert result is not None and result.text.startswith("English 0:")

    async def test_should_translate_from_the_source_language_into_english_as_rag_calls(
        self, translator
    ):
        await english_blocks(MagicMock(), _segments(3), "ar")

        args, kwargs = translator.call_args
        assert (args[2], args[3], kwargs["stage_name"]) == ("ar", "en", RAG_TRANSLATION_STAGE)

    async def test_should_retry_a_failed_call_as_two_halves(self, translator):
        calls: list[int] = []

        async def _fail_first(_llm: Any, texts: list[str], *a: Any, **k: Any) -> list[str] | None:
            calls.append(len(texts))
            return None if len(calls) == 1 else await _to_english(_llm, texts)

        translator.side_effect = _fail_first

        result = await english_blocks(MagicMock(), _segments(12), "ar")

        assert result is not None and len(calls) == 3

    async def test_should_keep_the_source_text_of_a_part_that_fails_again(self, translator):
        # Block 2 (sentences 4-7) fails alone too; block 1 translates in the retry.
        async def _fail(_llm: Any, texts: list[str], *a: Any, **k: Any) -> list[str] | None:
            if len(texts) > 1 or "رقم 5" in texts[0]:
                return None
            return await _to_english(_llm, texts)

        translator.side_effect = _fail

        result = await english_blocks(MagicMock(), _segments(8), "ar")

        assert result is not None
        assert [b["text"].startswith("English") for b in result.blocks] == [True, False]

    async def test_should_return_none_when_nothing_could_be_translated(self, translator):
        translator.side_effect = None
        translator.return_value = None

        result = await english_blocks(MagicMock(), _segments(8), "ar")

        # One batch call and its two halves; no block-by-block retry when no batch answered.
        assert (result, translator.call_count) == (None, 3)

    async def test_should_count_an_echoed_batch_as_untranslated(self, translator):
        async def _echo(_llm: Any, texts: list[str], *a: Any, **k: Any) -> list[str]:
            return list(texts)

        translator.side_effect = _echo

        assert await english_blocks(MagicMock(), _segments(8), "ar") is None

    async def test_should_return_none_when_the_provider_raises(self, translator):
        translator.side_effect = RuntimeError("provider down")

        assert await english_blocks(MagicMock(), _segments(8), "ar") is None

    async def test_should_return_none_without_speech(self, translator):
        assert await english_blocks(MagicMock(), [_seg("", 0.0)], "ar") is None

    async def test_should_keep_retry_halves_within_the_call_limit(self, translator, monkeypatch):
        # Two ~220-char blocks per batch → 8 batches, each retried as two halves.
        monkeypatch.setattr(english, "TRANSLATION_BATCH_CHARS", 500)
        in_flight = peak = 0

        async def _count(_llm: Any, texts: list[str], *a: Any, **k: Any) -> list[str] | None:
            nonlocal in_flight, peak
            in_flight += 1
            peak = max(peak, in_flight)
            await asyncio.sleep(0.01)
            in_flight -= 1
            return None if len(texts) > 1 else await _to_english(_llm, texts)

        translator.side_effect = _count

        await english_blocks(MagicMock(), _segments(64), "ar")

        assert peak == english._CONCURRENCY


def _hebrew_segments(count: int) -> list[dict[str, Any]]:
    """Unpunctuated Hebrew ASR lines (~33 chars), as YouTube's ``iw`` track sends them."""
    return [_seg(f"זה משפט מספר {i} בתמלול של הסרטון", start=i * 3.0) for i in range(count)]


def _spanish_segments(count: int) -> list[dict[str, Any]]:
    """~62-character Spanish sentences: four to a block."""
    return [
        _seg(f"Esta es la frase número {i} del texto traducido del video.", start=i * 3.0)
        for i in range(count)
    ]


_INPUT = re.compile(r"<input>\s*(\[.*\])\s*</input>", re.DOTALL)


def _short_when(marker: str) -> Any:
    """The model answering a call of several blocks one entry short when it holds
    ``marker``; everything else, and a single block, answered whole."""

    async def _answer(_llm: Any, prompt: str, **_kwargs: Any) -> str:
        match = _INPUT.search(prompt)
        assert match is not None
        sent = json.loads(match.group(1))
        answer = [f"English sentence {i}." for i in range(len(sent))]
        short = len(sent) > 1 and any(marker in text for text in sent)
        return json.dumps({"translations": answer[:-1] if short else answer})

    return _answer


def _leaves_in_batches(marker: str, remnant: str) -> Any:
    """A translator that keeps ``remnant`` in the block holding ``marker`` when it
    comes with others, and translates it whole when it comes alone."""

    async def _answer(_llm: Any, texts: list[str], *a: Any, **k: Any) -> list[str]:
        return [
            f"We met there, {remnant}." if marker in text and len(texts) > 1 else f"English {i}."
            for i, text in enumerate(texts)
        ]

    return _answer


def _calls_with(translator: MagicMock, marker: str) -> int:
    return sum(1 for call in translator.call_args_list if any(marker in t for t in call.args[1]))


class TestUntranslatedBlocks:
    """Blocks left untranslated by their batch are retried once, one block per call."""

    async def test_should_translate_every_block_when_its_batch_is_answered_short(self):
        # 10 blocks in two batches of 5; the first batch and its first half miscount.
        short = _short_when("מספר 0 ")

        with patch.object(translation_flat, "call_llm_with_retry", side_effect=short):
            result = await english_blocks(MagicMock(), _hebrew_segments(120), "he")

        assert result is not None
        assert not any(has_hebrew_arabic_or_cjk(block["text"]) for block in result.blocks)

    async def test_should_retry_a_block_whose_english_keeps_a_single_hebrew_word(self, translator):
        translator.side_effect = _leaves_in_batches("מספר 20 ", "at the שוק")

        result = await english_blocks(MagicMock(), _hebrew_segments(24), "he")

        assert result is not None
        assert not any(has_hebrew_arabic_or_cjk(block["text"]) for block in result.blocks)

    @pytest.mark.parametrize(
        "remnant",
        ["مرحبا", "\ufee3\ufeae\ufea3\ufe92\ufe8e", "你好", "こんにちは", "안녕하세요"],
        ids=["arabic", "arabic-presentation-forms", "han", "kana", "hangul"],
    )
    async def test_should_retry_a_block_whose_english_keeps_arabic_or_cjk(
        self, translator, remnant
    ):
        translator.side_effect = _leaves_in_batches("رقم 5 ", remnant)

        result = await english_blocks(MagicMock(), _segments(8), "ar")

        assert result is not None and remnant not in result.blocks[1]["text"]

    async def test_should_not_retry_a_block_twice_when_its_retry_is_still_hebrew(self, translator):
        translator.side_effect = self._always_hebrew("מספר 20 ")

        await english_blocks(MagicMock(), _hebrew_segments(24), "he")

        assert _calls_with(translator, "מספר 20 ") == 2

    async def test_should_keep_the_batch_answer_of_a_block_still_hebrew_after_its_retry(
        self, translator
    ):
        translator.side_effect = self._always_hebrew("מספר 20 ")

        result = await english_blocks(MagicMock(), _hebrew_segments(24), "he")

        assert result is not None and result.blocks[1]["text"] == "English, but שוק (2)"

    async def test_should_count_the_blocks_left_untranslated_in_one_log_line(
        self, translator, caplog
    ):
        translator.side_effect = self._always_hebrew("מספר 20 ")

        with caplog.at_level("INFO", logger=english.__name__):
            await english_blocks(MagicMock(), _hebrew_segments(24), "he")

        assert [r.getMessage() for r in caplog.records if "one by one" in r.getMessage()] == [
            "RAG translation retried 1/2 blocks one by one (0 past the cap of 8 not retried): "
            "1 still untranslated, kept as they were"
        ]

    async def test_should_retry_a_latin_block_that_came_back_as_its_source_text(self, translator):
        async def _echo_in_batches(_llm: Any, texts: list[str], *a: Any, **k: Any) -> list[str]:
            return [
                text if "número 5 " in text and len(texts) > 1 else f"English {i}."
                for i, text in enumerate(texts)
            ]

        translator.side_effect = _echo_in_batches

        result = await english_blocks(MagicMock(), _spanish_segments(16), "es")

        assert result is not None and result.blocks[1]["text"] == "English 0."

    async def test_should_make_one_call_per_batch_when_every_block_comes_back_english(
        self, translator
    ):
        await english_blocks(MagicMock(), _segments(8), "ar")

        assert translator.call_count == 1

    async def test_should_keep_single_block_retries_within_the_call_limit(self, translator):
        in_flight = peak = 0

        async def _count(_llm: Any, texts: list[str], *a: Any, **k: Any) -> list[str] | None:
            nonlocal in_flight, peak
            in_flight += 1
            peak = max(peak, in_flight)
            await asyncio.sleep(0.01)
            in_flight -= 1
            return None if len(texts) > 1 else await _to_english(_llm, texts)

        translator.side_effect = _count

        await english_blocks(MagicMock(), _segments(64), "ar")

        assert peak == english._CONCURRENCY

    async def test_should_retry_at_most_half_the_blocks_when_more_are_untranslated(
        self, translator
    ):
        # 40 blocks, every one answered with Arabic left in it: the cap is 20.
        async def _arabic_left(_llm: Any, texts: list[str], *a: Any, **k: Any) -> list[str]:
            return ["We met there, مرحبا." if len(texts) > 1 else "English." for _ in texts]

        translator.side_effect = _arabic_left

        await english_blocks(MagicMock(), _segments(160), "ar")

        assert sum(1 for call in translator.call_args_list if len(call.args[1]) == 1) == 20

    async def test_should_retry_a_batch_blocks_before_a_slower_batch_returns(
        self, translator, monkeypatch
    ):
        # Two blocks per batch: blocks 0-1 (sentences 0-7), blocks 2-3 (8-15).
        monkeypatch.setattr(english, "TRANSLATION_BATCH_CHARS", 500)
        events: list[str] = []

        async def _answer(_llm: Any, texts: list[str], *a: Any, **k: Any) -> list[str]:
            if len(texts) > 1 and any("رقم 9 " in t for t in texts):
                await asyncio.sleep(0.2)
                events.append("slow batch answered")
            if len(texts) == 1:
                events.append("early block retried")
            return [
                "We met there, مرحبا." if "رقم 1 " in t and len(texts) > 1 else f"English {i}."
                for i, t in enumerate(texts)
            ]

        translator.side_effect = _answer

        await english_blocks(MagicMock(), _segments(16), "ar")

        assert events == ["early block retried", "slow batch answered"]

    async def test_should_send_the_single_block_retries_as_rag_calls(self, translator):
        translator.side_effect = _leaves_in_batches("رقم 5 ", "مرحبا")

        await english_blocks(MagicMock(), _segments(8), "ar")

        assert {call.kwargs["stage_name"] for call in translator.call_args_list} == {
            RAG_TRANSLATION_STAGE
        }

    @staticmethod
    def _always_hebrew(marker: str) -> Any:
        """A translator that never translates the block holding ``marker`` fully."""

        async def _answer(_llm: Any, texts: list[str], *a: Any, **k: Any) -> list[str]:
            return [
                f"English, but שוק ({len(texts)})" if marker in text else f"English {i}."
                for i, text in enumerate(texts)
            ]

        return _answer


class TestTranslationModel:
    async def test_should_run_on_the_translation_model_when_it_is_set(self, monkeypatch):
        monkeypatch.setattr(settings, "LLM_TRANSLATION_MODEL", "openai/gpt-4o-mini")
        llm_call = AsyncMock(return_value=None)

        with patch.object(translation_flat, "call_llm_with_retry", llm_call):
            await english_blocks(MagicMock(), _segments(3), "ar")

        assert llm_call.call_args.kwargs["model_override"] == "openai/gpt-4o-mini"

    async def test_should_run_on_the_fast_model_when_no_translation_model_is_set(self):
        llm_call = AsyncMock(return_value=None)

        with patch.object(translation_flat, "call_llm_with_retry", llm_call):
            await english_blocks(MagicMock(), _segments(3), "ar")

        kwargs = llm_call.call_args.kwargs
        assert (kwargs["model_override"], kwargs["use_fast_model"]) == (None, True)


class TestIsEnglishText:
    def test_should_accept_an_english_transcript(self):
        text = "Our AI agent just returned eight thousand bags to the customer instead of eighty."

        assert is_english_text(text)

    def test_should_reject_a_hebrew_transcript(self):
        assert not is_english_text("הסוכן שלנו החזיר שמונת אלפים שקיות ללקוח במקום שמונים " * 3)

    def test_should_reject_a_spanish_transcript(self):
        text = "Nuestro agente acaba de devolver ocho mil bolsas al cliente en lugar de ochenta."

        assert not is_english_text(text)

    def test_should_reject_text_too_short_to_judge(self):
        assert not is_english_text("Hello there")

    def test_should_accept_a_long_english_transcript(self):
        assert is_english_text(_ENGLISH_LINE * 150)

    def test_should_reject_a_spanish_transcript_behind_an_english_intro(self):
        # The ~5.9k-char intro fills langdetect's whole 5,000-char sample.
        assert not is_english_text(_ENGLISH_LINE * 70 + _SPANISH_LINE * 300)
