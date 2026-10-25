"""Phase-4 review fixes, lane FC — assembly + translation (review-p4-C, review-p4-D W8).

C-C1: a review's verdict rides ONE comparison tab — the Pros & cons tab when it is
planned and ships the reviewer's pros / cons, else the first comparison tab — so the
hero that lifts it is its only place (the 4.3 playbook plans Pros & cons AND vs
competitors, and both carried it). C-W1 / D-W8: a verbatim source-language line never
goes EN→source under another key either — an asks row's “note”, a quote flash card's
``front`` — and its English (``quoteEn``) ships as its own key on the English page and
leaves the source copy. C-W2: only a talk's Takeaways checklist is the asks view; a
lecture's keeps its takeaways.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from src.services.pipeline.assembly import assemble_comparison, assemble_response
from src.services.pipeline.assembly.assemblers_learning import assemble_flash_deck
from src.services.pipeline.assembly.assemblers_primary import assemble_checklist
from src.services.pipeline.assembly.dedupe import _dedupe_tabs
from src.services.pipeline.assembly.talk_asks import asks_data
from src.services.pipeline.translation import _collect_strings, translate_to_source

# ─── C-C1: the verdict on one tab ────────────────────────────────────────────

_VERDICT = {"badge": "recommended", "bottomLine": "Really, really good.", "bestFor": ["Commuters"]}
_ROWS = [
    {"feature": "ANC", "thisProduct": "same", "competitor": "same", "competitorName": "Sony"},
    {"feature": "Case", "thisProduct": "better", "competitor": "worse", "competitorName": "Bose"},
]


def _review(pros: list[str] | None = None, cons: list[str] | None = None) -> dict[str, Any]:
    return {
        "review": {
            "product": "AirPods Pro 2",
            "pros": ["Best sound yet"] if pros is None else pros,
            "cons": ["No lossless"] if cons is None else cons,
            "comparisons": [dict(row) for row in _ROWS],
            "verdict": dict(_VERDICT),
        }
    }


def _comparison_tab(tab_id: str, source: str, component: str = "comparison") -> dict[str, Any]:
    return {"id": tab_id, "label": tab_id, "component": component, "dataSource": source}


_PROS_CONS = _comparison_tab("pros_cons", "review.pros")
_VS = _comparison_tab("vs", "review.comparisons")


def _verdict_tabs(tabs: list[dict[str, Any]], extraction: dict[str, Any] | None = None) -> list:
    triage = {"primaryTag": "review", "contentTags": ["review"], "tabs": tabs}
    result = assemble_response(triage, extraction or _review(), None, None)
    return [tab["id"] for tab in result["tabs"] if "verdict" in (tab.get("props") or {})]


class TestTheVerdictRidesOneTab:
    def test_should_carry_the_verdict_on_the_pros_and_cons_tab_only_when_a_review_plans_both(
        self,
    ) -> None:
        assert _verdict_tabs([_PROS_CONS, _VS]) == ["pros_cons"]

    def test_should_carry_the_verdict_on_the_pros_and_cons_tab_when_the_rows_tab_comes_first(
        self,
    ) -> None:
        assert _verdict_tabs([_VS, _comparison_tab("pros_cons", "review")]) == ["pros_cons"]

    def test_should_carry_the_verdict_on_the_pros_and_cons_tab_when_the_rivals_ship_as_a_radar(
        self,
    ) -> None:
        radar = _comparison_tab("vs", "review.comparisons", "comparison_radar")

        assert _verdict_tabs([_PROS_CONS, radar]) == ["pros_cons"]

    def test_should_carry_the_verdict_on_the_rows_tab_when_the_review_states_no_pros_or_cons(
        self,
    ) -> None:
        assert _verdict_tabs([_PROS_CONS, _VS], _review(pros=[], cons=[])) == ["vs"]

    def test_should_carry_the_verdict_on_the_first_tab_when_no_pros_and_cons_tab_is_planned(
        self,
    ) -> None:
        extraction = {**_review(), "tech": {"comparisons": [dict(row) for row in _ROWS]}}
        first = _comparison_tab("specs_vs", "tech.comparisons")

        assert _verdict_tabs([first, _VS], extraction) == ["specs_vs"]

    def test_should_keep_the_verdict_on_a_lone_comparison_tab(self) -> None:
        assert _verdict_tabs([_VS]) == ["vs"]

    def test_should_keep_the_verdict_on_a_comparison_assembled_outside_a_plan(self) -> None:
        props = assemble_comparison(_VS, _review()["review"]["comparisons"], _review(), None)

        assert props is not None and props["verdict"]["bottomLine"] == _VERDICT["bottomLine"]


# ─── C-W1 / D-W8: verbatim lines under other keys ────────────────────────────

_HE = "אם זהב היה גדל על העצים לא היה טעם להשתמש בו"
_EN = "If gold grew on trees there would be no point using it"


def _sent(obj: Any) -> list[str]:
    pairs: list[tuple[tuple[str | int, ...], str]] = []
    _collect_strings(obj, (), pairs)
    return [text for _, text in pairs]


def _sends_hebrew_line(obj: Any) -> bool:
    return any(_HE in text for text in _sent(obj))


def _ask(n: int, **extra: Any) -> dict[str, Any]:
    return {"claim": f"Ask {n}", "why": "w", "kind": "ask", "quote": _HE, "quoteEn": _EN,
            "timestamp": 60 * n, **extra}  # fmt: skip


def _asks_rows(**extra: Any) -> list[dict[str, Any]]:
    extraction = {"learning": {"keyIdeas": [_ask(n, **extra) for n in (1, 2, 3)]}}
    data = asks_data("checklist", "learning.takeaways", extraction, [], "talk")
    props = assemble_checklist({}, data, extraction, None)
    assert props is not None
    return props["items"]


def _quote_cards(**extra: Any) -> list[dict[str, Any]]:
    quotes = [{"quote": _HE, "quoteEn": _EN, "speaker": "Ran Levi", "timestamp": 75, **extra}] * 2
    props = assemble_flash_deck({}, quotes, {}, None)
    assert props is not None
    return props["cards"]


def _output(component: str, key: str, items: list[dict[str, Any]]) -> dict[str, Any]:
    tab = {"id": "t", "label": "Tab", "component": component, "props": {key: items}}
    return {"tabs": [tab], "meta": {"tldr": "A history of money", "language": "en"}}


@pytest.fixture
def fake_translate():
    async def fake(_llm, strings, *_a, **_k):
        return [f"HE: {s}" for s in strings]

    with patch("src.services.pipeline.translation.translate_flat_list") as mock_flat:
        mock_flat.side_effect = fake
        yield mock_flat


class TestAsksRowVerbatimLine:
    def test_should_not_send_the_verbatim_note_of_a_hebrew_asks_row(self) -> None:
        assert _sends_hebrew_line({"items": _asks_rows()}) is False

    def test_should_keep_the_quotes_english_on_an_asks_row(self) -> None:
        assert _asks_rows()[0]["quoteEn"] == _EN

    def test_should_ship_the_verbatim_line_as_the_rows_quote(self) -> None:
        assert _asks_rows()[0]["quote"] == _HE

    def test_should_still_translate_the_asks_label(self) -> None:
        assert "Ask 1" in _sent({"items": _asks_rows()})

    def test_should_translate_the_note_of_an_ask_whose_quote_is_a_translation(self) -> None:
        rows = _asks_rows(quote=_EN, quoteEn=None, quoteTranslated=True)

        assert _EN in _sent({"items": rows})

    async def test_should_leave_the_asks_rows_english_out_of_the_source_copy(
        self, fake_translate
    ) -> None:
        output = _output("checklist", "items", _asks_rows())

        result = await translate_to_source(AsyncMock(), output, "he")

        assert "quoteEn" not in result["sourceLanguage"]["tabs"][0]["props"]["items"][0]

    async def test_should_keep_the_verbatim_note_in_the_source_copy(self, fake_translate) -> None:
        output = _output("checklist", "items", _asks_rows())

        result = await translate_to_source(AsyncMock(), output, "he")

        assert result["sourceLanguage"]["tabs"][0]["props"]["items"][0]["note"] == f"“{_HE}”"


class TestQuoteFlashCardVerbatimLine:
    def test_should_not_send_the_verbatim_front_of_a_hebrew_quote_card(self) -> None:
        assert _sends_hebrew_line({"cards": _quote_cards()}) is False

    def test_should_ship_the_quotes_english_as_its_own_key(self) -> None:
        assert _quote_cards()[0]["quoteEn"] == _EN

    def test_should_keep_the_english_out_of_the_cards_back(self) -> None:
        assert _quote_cards()[0]["back"] == "— Ran Levi · 1:15"

    def test_should_translate_who_said_it_and_when(self) -> None:
        assert _sent({"cards": _quote_cards()}) == ["— Ran Levi · 1:15"] * 2

    def test_should_translate_the_front_of_a_card_whose_quote_is_a_translation(self) -> None:
        cards = _quote_cards(quote=_EN, quoteEn=None, quoteTranslated=True)

        assert _EN in _sent({"cards": cards})

    def test_should_carry_the_translated_flag_onto_the_card(self) -> None:
        cards = _quote_cards(quote=_EN, quoteEn=None, quoteTranslated=True)

        assert cards[0]["quoteTranslated"] is True

    async def test_should_leave_the_cards_english_out_of_the_source_copy(
        self, fake_translate
    ) -> None:
        output = _output("flash_deck", "cards", _quote_cards())

        result = await translate_to_source(AsyncMock(), output, "he")

        assert "quoteEn" not in result["sourceLanguage"]["tabs"][0]["props"]["cards"][0]

    async def test_should_keep_the_verbatim_front_in_the_source_copy(self, fake_translate) -> None:
        output = _output("flash_deck", "cards", _quote_cards())

        result = await translate_to_source(AsyncMock(), output, "he")

        assert result["sourceLanguage"]["tabs"][0]["props"]["cards"][0]["front"] == _HE


class TestRestatedQuoteRule:
    def test_should_send_a_leaf_that_only_shares_words_with_the_quote(self) -> None:
        card = {"front": f"{_HE} — עוד", "back": "— Ran", "quote": _HE}

        assert f"{_HE} — עוד" in _sent(card)

    def test_should_send_a_leaf_beside_a_cleared_quote(self) -> None:
        assert _sent({"front": _HE, "back": "— Ran", "quote": None}) == [_HE, "— Ran"]


# ─── C-W2: only a talk's Takeaways checklist is the asks view ────────────────

_TAKEAWAYS = ["Sleep before the exam", "Teach it to a friend", "Space your reviews"]
_KEY_IDEAS = [
    {"claim": "Spacing beats cramming", "why": "w", "kind": "argument", "timestamp": 60},
    {"claim": "Testing is learning", "why": "w", "kind": "thesis", "timestamp": 120},
]


def _learning_tabs(content_format: str) -> list[dict[str, Any]]:
    tabs = [
        {"id": "key_ideas", "label": "Key ideas", "component": "spot_explorer",
         "dataSource": "learning.keyIdeas"},
        {"id": "takeaways", "label": "Takeaways", "component": "checklist",
         "dataSource": "learning.takeaways"},
    ]  # fmt: skip
    triage = {"primaryTag": "learning", "contentTags": ["learning"],
              "contentFormat": content_format, "tabs": tabs}  # fmt: skip
    extraction = {"learning": {"keyIdeas": [dict(i) for i in _KEY_IDEAS], "takeaways": _TAKEAWAYS}}
    return assemble_response(triage, extraction, None, None)["tabs"]


class TestTheAsksSwapIsATalksOnly:
    def test_should_keep_a_lectures_takeaways_checklist_on_a_run_with_key_ideas(self) -> None:
        tab = next(t for t in _learning_tabs("lecture") if t["id"] == "takeaways")

        assert [item["label"] for item in tab["props"]["items"]] == _TAKEAWAYS

    def test_should_keep_the_takeaways_when_the_run_is_not_a_talk(self) -> None:
        extraction = {"learning": {"keyIdeas": [_ask(1)], "takeaways": _TAKEAWAYS}}

        data = asks_data("checklist", "learning.takeaways", extraction, _TAKEAWAYS, "lecture")

        assert data is _TAKEAWAYS

    def test_should_keep_the_takeaways_when_the_run_has_no_format(self) -> None:
        extraction = {"learning": {"keyIdeas": [_ask(1)], "takeaways": _TAKEAWAYS}}

        assert asks_data("checklist", "learning.takeaways", extraction, _TAKEAWAYS) is _TAKEAWAYS

    def test_should_swap_a_talks_takeaways_checklist_for_its_asks(self) -> None:
        extraction = {"learning": {"keyIdeas": [_ask(n) for n in (1, 2, 3)]}}

        data = asks_data("checklist", "learning.takeaways", extraction, _TAKEAWAYS, "talk")

        assert [row["label"] for row in data] == ["Ask 1", "Ask 2", "Ask 3"]


def _settled_tabs(takeaways: list[str]) -> list[str]:
    """The ids that stay after the dedupe: a two-card key-ideas tab (its minimum) and a
    Takeaways checklist of plain takeaways, one equal to a key-idea claim."""
    spots = [{"name": idea["claim"], "description": "d"} for idea in _KEY_IDEAS]
    tabs = [
        {"id": "key_ideas", "component": "spot_explorer", "props": {"spots": spots}},
        {"id": "takeaways", "component": "checklist",
         "props": {"items": [{"label": text} for text in takeaways]}},
    ]  # fmt: skip
    sources = {"key_ideas": "learning.keyIdeas", "takeaways": "learning.takeaways"}
    _dedupe_tabs(tabs, [], (), sources)
    return [tab["id"] for tab in tabs]


class TestTheDedupeKnowsTheAsksView:
    def test_should_keep_a_takeaways_checklist_that_is_no_asks_view(self) -> None:
        takeaways = [_KEY_IDEAS[0]["claim"], *_TAKEAWAYS]

        assert _settled_tabs(takeaways) == ["key_ideas", "takeaways"]
