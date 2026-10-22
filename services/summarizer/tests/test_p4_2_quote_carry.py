"""Quotes everywhere, with their English beneath (output-quality 4.2, R10, D12).

A non-English run writes ``quoteEn`` (the English rendering) beside each verbatim
``quote``, and ``questionEn`` beside an interview's verbatim ``question``. Assembly
carries each only beside the line it renders: a key-idea card, a quote card, a spot (a
promoted grid row's too), a moment, an exchange row and a claim. A cleared quote takes
its English with it. Claims now ship their own quote (S14 shipped 0 of 20) with the
translated flag (prose, 2.0 d); a ``{quote, speaker, timestamp}`` item becomes a flash
card instead of a card the FE schema refuses. Quiz quotes stay out (the task row's scope:
spots, moments, claims).
"""

from __future__ import annotations

from typing import Any

from src.services.pipeline.assembly import assemble_flash_deck, assemble_response
from src.services.pipeline.assembly.assemblers_secondary import _normalize_claim
from src.services.pipeline.assembly.key_ideas import key_idea_props
from src.services.pipeline.assembly.moment_normalizers import normalize_moment_items
from src.services.pipeline.assembly.normalizers import _normalize_to_spot, _to_flash_card
from src.services.pipeline.assembly.promotion import spot_evidence
from src.services.pipeline.assembly.quote_spots import quote_spots
from src.services.pipeline.quote_verify import QuoteIndex

_HE = "הכסף הוא רק הסכמה"
_EN = "Money is only an agreement"
_Q_HE = "מה זה בעצם כסף?"
_Q_EN = "What is money, really?"


def _idea(**extra: Any) -> dict[str, Any]:
    return {"claim": "Money is trust", "why": "w", "quote": _HE, "quoteEn": _EN,
            "timestamp": 60, **extra}  # fmt: skip


class TestKeyIdeaCards:
    def test_should_keep_quote_en_on_a_key_idea_card(self):
        spot = key_idea_props([_idea(), _idea(claim="Two")])["spots"][0]

        assert (spot["quote"], spot["quoteEn"]) == (_HE, _EN)

    def test_should_drop_quote_en_when_the_quote_is_cleared(self):
        spot = key_idea_props([_idea(quote=None), _idea(claim="Two")])["spots"][0]

        assert "quoteEn" not in spot

    def test_should_ship_no_quote_en_when_the_run_wrote_none(self):
        spot = key_idea_props([_idea(quoteEn=None), _idea(claim="Two")])["spots"][0]

        assert "quoteEn" not in spot


class TestQuoteAndSpotCards:
    def test_should_keep_quote_en_on_a_quote_card(self):
        item = {"quote": _HE, "quoteEn": _EN, "speaker": "Ran Levi", "timestamp": 30}

        assert quote_spots([item])[0]["quoteEn"] == _EN

    def test_should_keep_quote_en_on_a_spot_with_a_quote(self):
        spot = _normalize_to_spot({"name": "Inflation", "quote": _HE, "quoteEn": _EN})

        assert spot is not None and spot["quoteEn"] == _EN

    def test_should_not_ship_quote_en_on_a_spot_without_a_quote(self):
        spot = _normalize_to_spot({"name": "Inflation", "description": "d", "quoteEn": _EN})

        assert spot is not None and "quoteEn" not in spot


class TestMoments:
    def test_should_keep_quote_en_on_a_segment_moment(self):
        segment = {"title": "Gold", "summary": "s", "quote": _HE, "quoteEn": _EN, "timestamp": 90}

        assert normalize_moment_items([segment])[0]["quoteEn"] == _EN

    def test_should_drop_quote_en_from_a_moment_whose_quote_is_cleared(self):
        segment = {"title": "Gold", "summary": "s", "quote": None, "quoteEn": _EN, "timestamp": 90}

        assert "quoteEn" not in normalize_moment_items([segment])[0]

    def test_should_keep_question_en_on_an_exchange_row(self):
        exchange = {"question": _Q_HE, "questionEn": _Q_EN, "answer": "a", "timestamp": 12}

        assert normalize_moment_items([exchange])[0]["questionEn"] == _Q_EN

    def test_should_title_an_exchange_row_with_the_question_in_english(self):
        exchange = {"question": _Q_HE, "questionEn": _Q_EN, "answer": "a", "timestamp": 12}

        assert normalize_moment_items([exchange])[0]["label"] == _Q_EN

    def test_should_keep_the_verbatim_question_beside_its_english_title(self):
        exchange = {"question": _Q_HE, "questionEn": _Q_EN, "answer": "a", "timestamp": 12}

        assert normalize_moment_items([exchange])[0]["question"] == _Q_HE


def _claim(item: dict[str, Any]) -> dict[str, Any]:
    claim = _normalize_claim(item)
    assert claim is not None
    return claim


class TestClaims:
    def test_should_ship_the_claim_quote_when_the_claim_is_anchored_and_quoted(self):
        claim = {"claim": "c", "quote": "We doubled it.", "timestamp": 40}

        assert _claim(claim)["quote"] == "We doubled it."

    def test_should_ship_the_claim_quote_when_the_claim_is_unanchored(self):
        assert _claim({"claim": "c", "quote": "We doubled it."})["quote"] == ("We doubled it.")

    def test_should_mark_a_translated_claim_quote_as_prose(self):
        claim = {"claim": "c", "quote": "We doubled it.", "quoteTranslated": True, "timestamp": 4}

        assert _claim(claim)["quoteTranslated"] is True

    def test_should_keep_quote_en_on_a_claim(self):
        claim = {"claim": "c", "quote": _HE, "quoteEn": _EN, "timestamp": 40}

        assert _claim(claim)["quoteEn"] == _EN

    def test_should_ship_no_quote_when_the_quote_pass_cleared_it(self):
        claim = {"claim": "c", "quote": None, "quoteEn": _EN, "timestamp": 40}

        assert {"quote", "quoteEn"} & _claim(claim).keys() == set()


class TestClearedByTheQuotePass:
    def test_should_drop_quote_en_when_the_english_quote_pass_clears_the_quote(self):
        index = QuoteIndex.build([{"text": "money is only an agreement", "start": 60}])
        ideas = [_idea(quote="never said", quoteEn="never said"), _idea(claim="Two")]
        tabs = [{"id": "ideas", "component": "spot_explorer", "dataSource": "learning.keyIdeas"}]
        triage = {"primaryTag": "learning", "contentTags": ["learning"], "tabs": tabs}

        result = assemble_response(triage, {"learning": {"keyIdeas": ideas}}, None, None,
                                   quote_index=index)  # fmt: skip

        spots = next(t for t in result["tabs"] if t["id"] == "ideas")["props"]["spots"]
        assert "quoteEn" not in spots[0]


class TestPromotedGridEvidence:
    def test_should_keep_quote_en_when_a_grid_rows_evidence_is_its_items_quote(self):
        quotes = {_HE: {"quote": _HE, "quoteEn": _EN}}

        spot = spot_evidence({"name": "Money"}, {"evidence": _HE}, quotes)

        assert (spot["quote"], spot["quoteEn"]) == (_HE, _EN)

    def test_should_ship_no_quote_en_when_the_evidence_is_prose(self):
        spot = spot_evidence({"name": "Money"}, {"evidence": "An example"}, {})

        assert {"quote", "quoteEn"} & spot.keys() == set()


class TestFlashCardFromAQuote:
    def test_should_put_the_line_in_front(self):
        card = _to_flash_card({"quote": "Stay hungry.", "speaker": "Jobs", "timestamp": 75})

        assert card["front"] == "Stay hungry."

    def test_should_put_the_speaker_and_time_behind(self):
        card = _to_flash_card({"quote": "Stay hungry.", "speaker": "Jobs", "timestamp": 75})

        assert card["back"] == "— Jobs · 1:15"

    def test_should_lead_the_back_with_the_english_of_a_verbatim_quote(self):
        card = _to_flash_card({"quote": _HE, "quoteEn": _EN, "speaker": "Ran", "timestamp": 5})

        assert card["back"] == f"{_EN} — Ran · 0:05"

    def test_should_fall_back_to_the_context_without_speaker_or_time(self):
        card = _to_flash_card({"text": "Four score.", "speaker": "", "context": "Gettysburg"})

        assert (card["front"], card["back"]) == ("Four score.", "Gettysburg")

    def test_should_ship_a_deck_whose_every_card_has_front_and_back(self):
        quotes = [
            {"quote": "Stay hungry.", "speaker": "Jobs", "timestamp": 75},
            {"quote": "Stay foolish.", "speaker": "Jobs", "timestamp": 80},
        ]

        props = assemble_flash_deck({}, quotes, {}, None)
        assert props is not None
        cards = props["cards"]

        assert all(isinstance(c.get("front"), str) and isinstance(c.get("back"), str)
                   for c in cards)  # fmt: skip
