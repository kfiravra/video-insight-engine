"""English text for the RAG index from a non-English transcript (pipeline-1min 2.3).

``english_blocks`` merges caption segments into sentence-sized blocks, keeps
each block's time span, and translates the blocks on the fast model with the
``translate_flat`` prompt. These tests fake the one translation call.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from src.services.transcript import english
from src.services.transcript.english import (
    RAG_TRANSLATION_STAGE,
    _batches,
    _blocks,
    english_blocks,
    is_english_text,
)


def _seg(text: str, start: float, duration: float = 2.0) -> dict[str, Any]:
    return {"text": text, "start": start, "duration": duration}


def _sentence(n: int) -> str:
    """One ~54-character Arabic sentence ending in a full stop."""
    return f"هذه جملة رقم {n} في النص المترجم من الفيديو الطويل جدا."


def _segments(count: int) -> list[dict[str, Any]]:
    return [_seg(_sentence(i), start=i * 3.0) for i in range(count)]


async def _to_english(_llm: Any, texts: list[str], *_args: Any, **_kwargs: Any) -> list[str]:
    return [f"English {i}: {len(text)} chars" for i, text in enumerate(texts)]


@pytest.fixture
def translator() -> Iterator[MagicMock]:
    """The one LLM call, faked; tests swap its side effect."""
    with patch.object(english, "_translate_flat_list") as fake:
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

    def test_should_strip_the_bidi_marks_around_subtitle_lines(self):
        segments = [_seg("‫שורה ראשונה‬ ‫שורה שנייה‬", 0.0)]

        assert _blocks(segments)[0]["text"] == "שורה ראשונה שורה שנייה"

    def test_should_skip_segments_without_speech(self):
        segments = [_seg("", 0.0), _seg(" \n ", 1.0), _seg("טקסט", 2.0)]

        assert [b["text"] for b in _blocks(segments)] == ["טקסט"]

    def test_should_accept_millisecond_shaped_segments(self):
        segments = [{"text": "שלום", "startMs": 4000, "endMs": 6500}]

        assert (_blocks(segments)[0]["startMs"], _blocks(segments)[0]["endMs"]) == (4000, 6500)


class TestBatches:
    def test_should_keep_every_batch_within_the_character_budget(self):
        texts = ["x" * 900] * 10

        assert all(sum(len(t) for t in batch) <= 4000 for batch in _batches(texts))

    def test_should_keep_the_texts_in_order(self):
        texts = [f"{i}" * 700 for i in range(10)]

        assert [t for batch in _batches(texts) for t in batch] == texts


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

        assert await english_blocks(MagicMock(), _segments(8), "ar") is None

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
