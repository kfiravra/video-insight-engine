"""The EN→source translation leaves a speaker's verbatim line alone (output-quality 4.2, D12).

On a non-English run a ``quote`` the quote pass found is already in the source
language: sent through translation the model could rewrite it, and its echo counts
toward the mirror check. A quote flagged ``quoteTranslated`` is English prose and is
translated; a ``question`` the question check verified is the host's own words and is
not. ``quoteEn`` / ``questionEn`` are English: never sent, and left out of the
source-language copy, so the source view shows the verbatim line alone.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from src.services.pipeline.translation import _collect_strings, translate_to_source

_HE = "הכסף הוא רק הסכמה"
_EN = "Money is only an agreement"
_Q_HE = "מה זה בעצם כסף?"
_Q_EN = "What is money, really?"


def _spot(**extra: Any) -> dict[str, Any]:
    return {"name": "Money is trust", "description": "Why it holds", "quote": _HE, **extra}


def _output(*items: dict[str, Any]) -> dict[str, Any]:
    tab = {"id": "ideas", "label": "Key ideas", "component": "spot_explorer",
           "props": {"spots": list(items)}}  # fmt: skip
    return {"tabs": [tab], "meta": {"tldr": "A history of money", "language": "en"}}


def _collected(obj: Any) -> list[str]:
    pairs: list[tuple[tuple[str | int, ...], str]] = []
    _collect_strings(obj, (), pairs)
    return [text for _, text in pairs]


@pytest.fixture
def fake_translate():
    async def fake(_llm, strings, *_a, **_k):
        return [f"HE: {s}" for s in strings]

    with patch("src.services.pipeline.translation.translate_flat_list") as mock_flat:
        mock_flat.side_effect = fake
        yield mock_flat


class TestCollect:
    def test_should_not_send_a_verbatim_source_language_quote(self):
        assert _HE not in _collected(_spot())

    def test_should_translate_a_quote_flagged_quote_translated(self):
        assert _EN in _collected(_spot(quote=_EN, quoteTranslated=True))

    def test_should_not_send_quote_en(self):
        assert _EN not in _collected(_spot(quoteEn=_EN))

    def test_should_not_send_a_verified_interview_question(self):
        row = {"label": _Q_EN, "question": _Q_HE, "questionStatus": "verified"}

        assert _Q_HE not in _collected(row)

    def test_should_translate_a_question_the_check_did_not_verify(self):
        row = {"label": "Short", "question": _Q_EN, "questionStatus": "translated"}

        assert _Q_EN in _collected(row)

    def test_should_translate_a_quiz_question(self):
        assert _Q_EN in _collected({"question": _Q_EN, "options": ["a", "b"]})

    def test_should_not_send_question_en(self):
        assert _Q_EN not in _collected({"question": _Q_HE, "questionEn": _Q_EN})

    def test_should_not_send_the_label_cut_from_a_verbatim_question(self):
        row = {"label": _Q_HE, "question": _Q_HE, "questionStatus": "verified"}

        assert _collected(row) == []

    def test_should_translate_the_label_when_the_question_english_titles_the_row(self):
        row = {"label": _Q_EN, "question": _Q_HE, "questionEn": _Q_EN, "questionStatus": "verified"}

        assert _collected(row) == [_Q_EN]


class TestSourceView:
    async def test_should_keep_the_verbatim_quote_unchanged_in_the_source_view(
        self, fake_translate
    ):
        result = await translate_to_source(AsyncMock(), _output(_spot(quoteEn=_EN)), "he")

        assert result["sourceLanguage"]["tabs"][0]["props"]["spots"][0]["quote"] == _HE

    async def test_should_leave_quote_en_out_of_the_source_language_tabs(self, fake_translate):
        result = await translate_to_source(AsyncMock(), _output(_spot(quoteEn=_EN)), "he")

        assert "quoteEn" not in result["sourceLanguage"]["tabs"][0]["props"]["spots"][0]

    async def test_should_keep_quote_en_on_the_english_tabs(self, fake_translate):
        result = await translate_to_source(AsyncMock(), _output(_spot(quoteEn=_EN)), "he")

        assert result["tabs"][0]["props"]["spots"][0]["quoteEn"] == _EN

    async def test_should_translate_a_translated_quote_in_the_source_view(self, fake_translate):
        spot = _spot(quote=_EN, quoteTranslated=True)

        result = await translate_to_source(AsyncMock(), _output(spot), "he")

        assert result["sourceLanguage"]["tabs"][0]["props"]["spots"][0]["quote"] == f"HE: {_EN}"

    async def test_should_leave_question_en_out_of_the_source_language_tabs(self, fake_translate):
        row = {"label": _Q_EN, "question": _Q_HE, "questionEn": _Q_EN,
               "questionStatus": "verified", "seconds": 12}  # fmt: skip
        output = {"tabs": [{"id": "qa", "label": "Questions", "props": {"items": [row]}}]}

        result = await translate_to_source(AsyncMock(), output, "he")

        assert "questionEn" not in result["sourceLanguage"]["tabs"][0]["props"]["items"][0]
