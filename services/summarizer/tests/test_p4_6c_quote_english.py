"""4.6c (D12, G4-1): the translation phase writes the English beneath a verbatim line.

On a non-English run every verbatim source-language line the English page shows — a
verified ``quote`` on a card, a verified interview ``question`` — gets its English
(``quoteEn`` / ``questionEn``) from one source→English pass of the flat translation
call, never from the extraction model. The English tabs are saved with it; the
source-language copy never carries it. An English run makes no call.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.services.pipeline.translation_quotes import (
    apply_english,
    english_of_lines,
    verbatim_lines,
)

_HE = "הכסף הוא רק הסכמה בין אנשים"
_HE_2 = "הזהב לא היה נדיר מספיק"
_Q_HE = "מה זה בעצם כסף?"
_EN = "Money is only an agreement between people"
_FLAT = "src.services.pipeline.translation.translate_flat_list"


def _spot(quote: str = _HE, **extra: Any) -> dict[str, Any]:
    return {"name": "Money is trust", "description": "Why it holds", "quote": quote, **extra}


def _row(**extra: Any) -> dict[str, Any]:
    row = {"label": _Q_HE, "question": _Q_HE, "questionStatus": "verified", "seconds": 61}
    return {**row, **extra}


def _tab(component: str, key: str, items: list[dict[str, Any]]) -> dict[str, Any]:
    return {"id": component, "component": component, "props": {key: items}}


def _output(*tabs: dict[str, Any], meta: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"tabs": list(tabs), "meta": meta if meta is not None else {"tldr": "A history"}}


def _texts(output: dict[str, Any]) -> list[str]:
    return [line.text for line in verbatim_lines(output)]


async def _fake_flat(_llm: Any, strings: list[str], source: str, target: str, **_k: Any):
    """Each direction marked, so a test can tell which pass wrote a string."""
    return [f"{target.upper()}: {s}" for s in strings]


@pytest.fixture
def fake_flat():
    with patch(_FLAT, side_effect=_fake_flat) as mock_flat:
        yield mock_flat


# ─── Which lines ───


class TestVerbatimLines:
    def test_should_collect_a_verified_quote_when_a_card_ships_one(self) -> None:
        assert _texts(_output(_tab("spot_explorer", "spots", [_spot()]))) == [_HE]

    def test_should_skip_a_quote_when_it_is_flagged_quote_translated(self) -> None:
        spot = _spot(_EN, quoteTranslated=True)

        assert _texts(_output(_tab("spot_explorer", "spots", [spot]))) == []

    def test_should_collect_a_question_when_the_question_check_verified_it(self) -> None:
        assert _texts(_output(_tab("moment_track", "items", [_row()]))) == [_Q_HE]

    def test_should_skip_a_question_when_the_check_kept_it_as_translated(self) -> None:
        row = _row(question="What is money?", questionStatus="translated")

        assert _texts(_output(_tab("moment_track", "items", [row]))) == []

    def test_should_skip_a_quiz_quote_when_the_quiz_card_has_no_english_line(self) -> None:
        question = {"question": "Why?", "options": ["a", "b"], "quote": _HE}

        assert _texts(_output(_tab("quiz_arena", "questions", [question]))) == []

    def test_should_collect_the_verdict_quote_when_it_is_nested_in_a_tab(self) -> None:
        tab = {"id": "vs", "component": "comparison", "props": {"verdict": {"quote": _HE}}}

        assert _texts(_output(tab)) == [_HE]

    def test_should_collect_a_quote_when_the_meta_carries_one(self) -> None:
        assert _texts(_output(meta={"verdict": {"bottomLine": "b", "quote": _HE}})) == [_HE]

    def test_should_skip_a_blank_quote(self) -> None:
        assert _texts(_output(_tab("spot_explorer", "spots", [_spot("  ")]))) == []


# ─── The source→English pass ───


class TestEnglishOfLines:
    async def test_should_translate_from_the_source_language_into_english(
        self, fake_flat: MagicMock
    ) -> None:
        lines = verbatim_lines(_output(_tab("spot_explorer", "spots", [_spot()])))

        await english_of_lines(MagicMock(), lines, "he")

        assert fake_flat.call_args.args[2:4] == ("he", "en")

    async def test_should_send_a_line_once_when_two_cards_quote_it(
        self, fake_flat: MagicMock
    ) -> None:
        output = _output(
            _tab("spot_explorer", "spots", [_spot(), _spot(_HE_2)]),
            _tab("moment_track", "items", [{"label": "m", "seconds": 5, "quote": _HE}]),
        )

        await english_of_lines(MagicMock(), verbatim_lines(output), "he")

        assert fake_flat.call_args.args[1] == [_HE, _HE_2]

    async def test_should_make_no_call_when_no_line_is_verbatim(self, fake_flat: MagicMock) -> None:
        english = await english_of_lines(MagicMock(), [], "he")

        assert (english, fake_flat.call_count) == ({}, 0)

    async def test_should_give_no_english_when_the_call_fails(self) -> None:
        lines = verbatim_lines(_output(_tab("spot_explorer", "spots", [_spot()])))

        with patch(_FLAT, AsyncMock(return_value=None)):
            assert await english_of_lines(MagicMock(), lines, "he") == {}

    async def test_should_give_no_english_when_the_model_echoes_the_line(self) -> None:
        lines = verbatim_lines(_output(_tab("spot_explorer", "spots", [_spot()])))

        async def echo(_llm: Any, strings: list[str], *_a: Any, **_k: Any) -> list[str]:
            return list(strings)

        with patch(_FLAT, side_effect=echo):
            assert await english_of_lines(MagicMock(), lines, "he") == {}


# ─── Writing it beside the line ───


class TestApplyEnglish:
    def test_should_write_quote_en_beside_a_verified_quote(self) -> None:
        spot = _spot()

        apply_english(verbatim_lines(_output(_tab("spot_explorer", "spots", [spot]))), {_HE: _EN})

        assert spot["quoteEn"] == _EN

    def test_should_replace_a_quote_en_when_the_extraction_wrote_one(self) -> None:
        spot = _spot(quoteEn="An older rendering")

        apply_english(verbatim_lines(_output(_tab("spot_explorer", "spots", [spot]))), {_HE: _EN})

        assert spot["quoteEn"] == _EN

    def test_should_write_question_en_and_retitle_the_row_when_its_label_is_the_question(
        self,
    ) -> None:
        row = _row()

        apply_english(
            verbatim_lines(_output(_tab("moment_track", "items", [row]))), {_Q_HE: "Why?"}
        )

        assert (row["questionEn"], row["label"]) == ("Why?", "Why?")

    def test_should_keep_the_row_label_when_it_was_not_cut_from_the_question(self) -> None:
        row = _row(label="Money as trust")

        apply_english(
            verbatim_lines(_output(_tab("moment_track", "items", [row]))), {_Q_HE: "Why?"}
        )

        assert row["label"] == "Money as trust"

    def test_should_count_only_the_lines_that_got_english(self) -> None:
        output = _output(_tab("spot_explorer", "spots", [_spot(), _spot(_HE_2)]))

        assert apply_english(verbatim_lines(output), {_HE: _EN}) == 1


# ─── The phase ───


def _ctx(tabs: list[dict[str, Any]], source: str | None = "he") -> SimpleNamespace:
    video = SimpleNamespace(title="כותרת", channel="c", duration=600, thumbnail_url=None)
    return SimpleNamespace(
        source_language_code=source,
        source_language=None,
        llm_service=MagicMock(),
        assembled_tabs=tabs,
        assembled_meta={"tldr": "A history of money"},
        video_data=video,
        youtube_id="ytid",
    )


async def _run(ctx: SimpleNamespace, repo: MagicMock) -> None:
    from src.services.pipeline.phases import translation as phase

    with (
        patch.object(phase, "translate_text", AsyncMock(return_value="Title")),
        patch.object(phase, "settings", SimpleNamespace(REDIS_ENABLED=False)),
        patch.object(phase, "send_video_status_background", MagicMock()),
    ):
        async for _ in phase.run_phase_translation(ctx, repo, "vsid"):  # type: ignore[arg-type]
            pass


def _ideas() -> list[dict[str, Any]]:
    spots = [_spot(), _spot(_HE_2, description="Gold was never scarce enough")]
    return [_tab("spot_explorer", "spots", spots), _tab("moment_track", "items", [_row()])]


class TestPhase:
    async def test_should_save_the_english_tabs_with_quote_en_when_the_run_is_hebrew(
        self, fake_flat: MagicMock
    ) -> None:
        repo = MagicMock()

        await _run(_ctx(_ideas()), repo)

        spots = repo.save_structured_result.call_args.args[1]["tabs"][0]["props"]["spots"]
        assert [spot["quoteEn"] for spot in spots] == [f"EN: {_HE}", f"EN: {_HE_2}"]

    async def test_should_keep_quote_en_out_of_the_source_language_copy(
        self, fake_flat: MagicMock
    ) -> None:
        repo = MagicMock()

        await _run(_ctx(_ideas()), repo)

        source = repo.save_structured_result.call_args.args[1]["sourceLanguage"]
        assert "quoteEn" not in source["tabs"][0]["props"]["spots"][0]

    async def test_should_keep_the_verbatim_question_as_the_source_view_title(
        self, fake_flat: MagicMock
    ) -> None:
        repo = MagicMock()

        await _run(_ctx(_ideas()), repo)

        saved = repo.save_structured_result.call_args.args[1]
        english_row = saved["tabs"][1]["props"]["items"][0]
        source_row = saved["sourceLanguage"]["tabs"][1]["props"]["items"][0]
        assert (english_row["label"], source_row["label"]) == (f"EN: {_Q_HE}", _Q_HE)

    async def test_should_save_the_english_tabs_when_the_source_copy_fails(self) -> None:
        repo = MagicMock()

        async def english_only(_llm: Any, strings: list[str], source: str, target: str, **_k: Any):
            return [f"EN: {s}" for s in strings] if target == "en" else None

        with patch(_FLAT, side_effect=english_only):
            await _run(_ctx(_ideas()), repo)

        saved = repo.save_structured_result.call_args.args[1]
        assert (saved["status"], saved["tabs"][0]["props"]["spots"][0]["quoteEn"]) == (
            "completed",
            f"EN: {_HE}",
        )

    async def test_should_make_no_call_when_the_run_is_english(self, fake_flat: MagicMock) -> None:
        await _run(_ctx(_ideas(), source=None), MagicMock())

        assert fake_flat.call_count == 0
