"""pC review, lane FA — the translation findings.

- [C] The EN→source walk sent a claim's ``status`` (and the other enum leaves the web
  matches in English) to translation: S15's source view got "שנוי במחלוקת", failed the
  claims schema and showed the tab as a raw display_section.
- [W] D12: a source-language line the quote check could not find is flagged
  ``quoteTranslated`` (English prose) — S12 shipped a bare Hebrew quote on the English
  page. A flagged line not in Latin script is the speaker's language: it keeps its
  words in the source view and gets its English beneath. So does an interview
  question kept as ``translated``.
- FB relay: one batch that raises no longer leaves its sibling batches running,
  unawaited; it costs only its own strings, kept as English.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from src.services.pipeline import translation
from src.services.pipeline.assembly.text_utils import in_latin_script
from src.services.pipeline.translation import (
    _collect_strings,
    translate_in_batches,
    translate_to_source,
)
from src.services.pipeline.translation_quotes import (
    apply_english,
    english_of_lines,
    verbatim_lines,
)

_FLAT = "src.services.pipeline.translation.translate_flat_list"
_D11 = frozenset({"stated", "supported", "contested"})
_HE_MISS = "באוקטובר 2022 פשוט החליט לשחרר את זה כקוד פתוח"
_HE_QUESTION = "למה בעצם צריך את LangGraph?"


def _claims_tab() -> dict[str, Any]:
    """S15's Satoshi Suspects, shaped as it ships: English claims, Hebrew quotes."""
    claims = [
        {"claim": "Nick Szabo designed bit gold", "source": "Researchers",
         "status": "contested", "quote": "ניק סאבו תכנן את ביט גולד", "timestamp": 2100},
        {"claim": "Dorian Nakamoto denied it", "source": "Dorian Nakamoto",
         "status": "stated", "quote": "הוא הכחיש את זה", "timestamp": 2400},
        {"claim": "Craig Wright never proved it", "source": "A court",
         "status": "supported", "quote": "הוא מעולם לא הוכיח", "timestamp": 2700},
    ]  # fmt: skip
    return {"id": "suspects", "label": "Satoshi Suspects", "component": "claims_tracker",
            "props": {"claims": claims}}  # fmt: skip


def _output(*tabs: dict[str, Any], meta: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"tabs": list(tabs), "meta": meta if meta is not None else {"tldr": "A history"}}


def _collected(obj: Any) -> list[tuple[str | int, ...]]:
    pairs: list[tuple[tuple[str | int, ...], str]] = []
    _collect_strings(obj, (), pairs)
    return [path for path, _ in pairs]


def _sent_keys(obj: Any) -> set[str | int]:
    return {path[-1] for path in _collected(obj)}


async def _fake_flat(_llm: Any, strings: list[str], source: str, target: str, **_k: Any):
    return [f"{target.upper()}: {s}" for s in strings]


@pytest.fixture
def fake_flat():
    with patch(_FLAT, side_effect=_fake_flat) as mock_flat:
        yield mock_flat


# ─── [C] enum leaves stay English ───


class TestEnumLeaves:
    def test_should_not_send_a_claim_status_when_the_claims_tab_is_walked(self) -> None:
        assert "status" not in _sent_keys(_output(_claims_tab()))

    @pytest.mark.parametrize(
        ("key", "value"),
        [
            ("badge", "recommended"),
            ("winner", "right"),
            ("slot", "bottom"),
            ("size", "banner"),
            ("style", "warning"),
            ("mode", "tag_cloud"),
            ("primaryTag", "food"),
            ("degradedFrom", "spot_explorer"),
            ("sceneType", "product_closeup"),
        ],
    )
    def test_should_not_send_an_enum_leaf_when_the_web_matches_it_in_english(
        self, key: str, value: str
    ) -> None:
        assert key not in _sent_keys({"holder": {key: value, "text": "Some prose here"}})

    def test_should_still_send_the_claim_prose_when_the_claims_tab_is_walked(self) -> None:
        assert "claim" in _sent_keys(_output(_claims_tab()))

    async def test_should_keep_every_claim_status_in_d11_words_when_the_tab_is_translated(
        self, fake_flat
    ) -> None:
        result = await translate_to_source(AsyncMock(), _output(_claims_tab()), "he")

        claims = result["sourceLanguage"]["tabs"][0]["props"]["claims"]
        assert {claim["status"] for claim in claims} <= _D11

    async def test_should_keep_the_primary_tag_when_the_meta_is_translated(self, fake_flat) -> None:
        output = _output(_claims_tab(), meta={"primaryTag": "learning", "tldr": "A history"})

        result = await translate_to_source(AsyncMock(), output, "he")

        assert result["sourceLanguage"]["meta"]["primaryTag"] == "learning"


# ─── [W] D12: a source-language line the check could not find ───


def _spot(quote: str, **extra: Any) -> dict[str, Any]:
    return {"name": "Open source", "description": "Why it matters", "quote": quote, **extra}


def _spots_tab(*spots: dict[str, Any]) -> dict[str, Any]:
    return {"id": "ideas", "component": "spot_explorer", "props": {"spots": list(spots)}}


def _qa_row(question: str, status: str) -> dict[str, Any]:
    return {"label": question, "question": question, "questionStatus": status, "seconds": 61}


def _qa_tab(*rows: dict[str, Any]) -> dict[str, Any]:
    return {"id": "qa", "component": "moment_track", "props": {"items": list(rows)}}


class TestLatinScript:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("Money is only an agreement", True),
            (_HE_MISS, False),
            ("Claude Code ו-LangGraph הם כלים שונים לגמרי", False),
            ("Claude Codeは便利です", False),
            ("He calls it 'סבבה', which means fine", True),
            ("2022", True),
        ],
    )
    def test_should_tell_a_latin_line_when_under_a_third_of_its_words_are_foreign(
        self, text: str, expected: bool
    ) -> None:
        assert in_latin_script(text) is expected


class TestUnfoundSourceLine:
    def test_should_collect_a_flagged_quote_for_its_english_when_it_is_not_in_latin_script(
        self,
    ) -> None:
        output = _output(_spots_tab(_spot(_HE_MISS, quoteTranslated=True)))

        assert [line.text for line in verbatim_lines(output)] == [_HE_MISS]

    def test_should_not_send_a_flagged_hebrew_quote_when_the_walk_collects(self) -> None:
        output = _output(_spots_tab(_spot(_HE_MISS, quoteTranslated=True)))

        assert ("tabs", 0, "props", "spots", 0, "quote") not in _collected(output)

    def test_should_still_send_a_flagged_english_quote_when_the_walk_collects(self) -> None:
        output = _output(_spots_tab(_spot("He open-sourced it", quoteTranslated=True)))

        assert ("tabs", 0, "props", "spots", 0, "quote") in _collected(output)

    async def test_should_write_english_beneath_a_flagged_hebrew_quote_when_the_pass_runs(
        self, fake_flat
    ) -> None:
        spot = _spot(_HE_MISS, quoteTranslated=True)
        lines = verbatim_lines(_output(_spots_tab(spot)))

        apply_english(lines, await english_of_lines(AsyncMock(), lines, "he"))

        assert spot["quoteEn"] == f"EN: {_HE_MISS}"

    async def test_should_keep_a_flagged_hebrew_quote_unchanged_in_the_source_view(
        self, fake_flat
    ) -> None:
        output = _output(_spots_tab(_spot(_HE_MISS, quoteTranslated=True)))

        result = await translate_to_source(AsyncMock(), output, "he")

        assert result["sourceLanguage"]["tabs"][0]["props"]["spots"][0]["quote"] == _HE_MISS

    async def test_should_title_a_translated_hebrew_question_in_english_when_the_pass_runs(
        self, fake_flat
    ) -> None:
        row = _qa_row(_HE_QUESTION, "translated")
        lines = verbatim_lines(_output(_qa_tab(row)))

        apply_english(lines, await english_of_lines(AsyncMock(), lines, "he"))

        assert row["questionEn"] == f"EN: {_HE_QUESTION}"

    def test_should_still_skip_a_translated_english_question_when_lines_are_collected(
        self,
    ) -> None:
        output = _output(_qa_tab(_qa_row("Why LangGraph at all?", "translated")))

        assert verbatim_lines(output) == []


# ─── FB relay: every batch is awaited ───


class _RaisingFlat:
    """A fake flat call: a batch holding "BOOM" raises at once; every other batch
    answers after a pause, and records that it finished."""

    def __init__(self) -> None:
        self.finished: list[str] = []

    async def __call__(self, _llm: Any, strings: list[str], *_a: Any, **_k: Any) -> list[str]:
        if any("BOOM" in s for s in strings):
            raise RuntimeError("provider exploded")
        await asyncio.sleep(0.05)
        self.finished.extend(strings)
        return [f"HE: {s}" for s in strings]


_STRINGS = ["The first sentence here", "BOOM goes this one", "The last sentence here"]


@pytest.fixture
def raising_flat():
    fake = _RaisingFlat()
    with (
        patch.object(translation, "translate_flat_list", new=fake),
        patch.object(translation, "_MAX_TRANSLATION_BATCH", 1),
    ):
        yield fake


class TestEveryBatchAwaited:
    async def test_should_finish_every_sibling_batch_when_one_batch_raises(
        self, raising_flat: _RaisingFlat
    ) -> None:
        await translate_in_batches(AsyncMock(), _STRINGS, "en", "he")

        assert raising_flat.finished == [_STRINGS[0], _STRINGS[2]]

    async def test_should_keep_only_the_raising_batch_as_english_when_one_batch_raises(
        self, raising_flat: _RaisingFlat
    ) -> None:
        result = await translate_in_batches(AsyncMock(), _STRINGS, "en", "he")

        assert result == (
            [f"HE: {_STRINGS[0]}", _STRINGS[1], f"HE: {_STRINGS[2]}"],
            [False, True, False],
        )

    async def test_should_keep_a_raising_half_as_english_when_the_retry_halves(self) -> None:
        async def flat(_llm: Any, strings: list[str], *_a: Any, **_k: Any) -> list[str] | None:
            if len(strings) > 1:
                return None  # the full batch fails: the half retry runs
            if "BOOM" in strings[0]:
                raise RuntimeError("provider exploded")
            return [f"HE: {s}" for s in strings]

        with patch(_FLAT, side_effect=flat):
            result = await translate_in_batches(AsyncMock(), _STRINGS[:2], "en", "he")

        assert result == ([f"HE: {_STRINGS[0]}", _STRINGS[1]], [False, True])
