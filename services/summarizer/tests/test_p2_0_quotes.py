"""p2.0 (c) + (d) — quote verification and D13 extended (gate-1 G1-4, G1-6).

(d) Every quote is checked against the transcript the pipeline read
(``pipeline/quote_verify.py``): normalized (case, quote marks, punctuation, the
prompt render's fillers), exact first, else fuzzy ≥ 0.9; a quote cut with "…" passes
only when every fragment does. An English source clears a quote it cannot find
(``quoteStatus: "unverified"``); any other source keeps it as prose (``translated``,
D12) and the card says so (``quoteTranslated``). The check runs on a copy at the top
of ``assemble_response``. (c) A tier, score or status ships only with a timestamp AND
that quote; a refused tier list walks the demote ladder to an untiered list (S09).
"""

from __future__ import annotations

import copy
from types import SimpleNamespace
from typing import Any

import pytest

from src.models.domain_types import GamingRanking, ReviewRating, ReviewVerdict
from src.models.idea_types import Claim
from src.services.pipeline.assembly import assemble_response
from src.services.pipeline.assembly.assemblers_learning import (
    assemble_overview,
    assemble_verdict,
)
from src.services.pipeline.assembly.assemblers_secondary import assemble_claims_tracker
from src.services.pipeline.assembly.derived_guard import (
    stated_block_scalars,
    stated_number,
    stated_scalars,
)
from src.services.pipeline.quote_verify import (
    TRANSLATED,
    UNVERIFIED,
    VERIFIED,
    QuoteIndex,
    run_quote_index,
    verify_extraction_quotes,
    verify_quote,
)
from src.services.pipeline.schema_fields import slice_skeleton

_SEGMENTS = [
    {"start": 0.0, "duration": 5.0, "text": "Welcome back, everyone. Today: agents."},
    {"start": 20.4, "text": "Um, so the real problem is not what the model decided."},
    {"start": 41, "text": "It's who let it decide alone — basically, who gave it the keys?"},
    {"start": 63, "text": "Pencils down. Writing code by hand is now the exception."},
    {"start": 90, "text": "Paying off a card at 24% beats an index fund at 7%."},
    {"start": 300, "text": "Pencils down. I said it before and I'll say it again."},
    {"start": 400, "text": "Loki is S tier for me, no question about it."},
]
_HEBREW = [{"start": 0, "text": "הסוכן שלנו בדיוק החזיר ללקוח שמונה אלף שקל במקום שמונים."}]
_PARAPHRASE = "Hand-written code is over for good."


def _index(language: str | None = None) -> QuoteIndex:
    return QuoteIndex.build(_SEGMENTS, language)


# ─── verify_quote ───


class TestVerifyQuote:
    def test_should_verify_an_exact_line_at_the_second_it_is_said(self) -> None:
        verdict = verify_quote("Paying off a card at 24% beats an index fund at 7%.", _index())

        assert (verdict.status, verdict.seconds, verdict.score) == (VERIFIED, 90, 1.0)

    def test_should_pick_the_occurrence_nearest_the_item_when_a_line_recurs(self) -> None:
        assert verify_quote("Pencils down.", _index(), near_seconds=310).seconds == 300

    def test_should_ignore_case_quote_marks_and_apostrophes_when_comparing(self) -> None:
        verdict = verify_quote("“IT’S WHO LET IT DECIDE ALONE”", _index())

        assert verdict.status == VERIFIED

    def test_should_ignore_the_fillers_the_prompt_render_drops_when_comparing(self) -> None:
        # "Um" and "basically" never reach the prompt transcript the model quoted.
        quote = "So the real problem is not what the model decided. It's who let it decide alone"

        assert verify_quote(quote, _index()).status == VERIFIED

    def test_should_pass_a_near_verbatim_quote_above_the_threshold(self) -> None:
        verdict = verify_quote("Writing code by hand is now the rare exception.", _index())

        assert (verdict.status, verdict.score < 1.0) == (VERIFIED, True)

    def test_should_refuse_a_paraphrase_when_the_source_is_english(self) -> None:
        assert verify_quote(_PARAPHRASE, _index()).status == UNVERIFIED

    def test_should_call_a_missing_quote_translated_when_the_source_is_not_english(self) -> None:
        assert verify_quote(_PARAPHRASE, _index("he")).status == TRANSLATED

    def test_should_verify_a_source_language_quote_when_the_source_is_not_english(self) -> None:
        index = QuoteIndex.build(_HEBREW, "he")

        assert verify_quote("החזיר ללקוח שמונה אלף שקל", index).status == VERIFIED

    def test_should_verify_a_cut_quote_when_every_fragment_is_said(self) -> None:
        quote = "the real problem is not what the model decided … who gave it the keys"

        assert verify_quote(quote, _index()).status == VERIFIED

    def test_should_refuse_a_cut_quote_when_one_fragment_is_not_said(self) -> None:
        quote = (
            "the real problem is not what the model decided... who stole the house keys at night"
        )

        assert verify_quote(quote, _index()).status == UNVERIFIED

    def test_should_refuse_a_quote_without_words(self) -> None:
        assert verify_quote(" … ", _index()).score == 0.0


# ─── the extraction pre-pass ───


def _checked(extraction: dict, index: QuoteIndex) -> dict[str, Any]:
    checked = verify_extraction_quotes(extraction, None, index).extraction
    assert checked is not None
    return checked


def _learning(quote: str | None, **item: Any) -> dict:
    return {"learning": {"keyIdeas": [{"claim": "c", "why": "w", "quote": quote, **item}]}}


class TestExtractionPrePass:
    def test_should_clear_a_quote_the_transcript_does_not_hold(self) -> None:
        checked = _checked(_learning(_PARAPHRASE), _index())

        assert checked["learning"]["keyIdeas"][0]["quote"] is None

    def test_should_flag_the_cleared_item_unverified(self) -> None:
        checked = _checked(_learning(_PARAPHRASE), _index())

        assert checked["learning"]["keyIdeas"][0]["quoteStatus"] == UNVERIFIED

    def test_should_keep_a_verified_quote(self) -> None:
        checked = _checked(_learning("Pencils down."), _index())

        assert checked["learning"]["keyIdeas"][0]["quote"] == "Pencils down."

    def test_should_never_mutate_the_persisted_extraction(self) -> None:
        extraction = _learning(_PARAPHRASE, timestamp=63)
        before = copy.deepcopy(extraction)

        verify_extraction_quotes(extraction, None, _index())

        assert extraction == before

    def test_should_keep_a_translated_quote_as_prose_when_the_source_is_not_english(self) -> None:
        checked = _checked(_learning(_PARAPHRASE), _index("he"))
        idea = checked["learning"]["keyIdeas"][0]

        assert (idea["quote"], idea["quoteTranslated"]) == (_PARAPHRASE, True)

    def test_should_drop_an_unverified_line_from_a_quote_list(self) -> None:
        quotes = [{"quote": "Pencils down.", "speaker": "DHH"}, {"quote": _PARAPHRASE}]
        extraction = {"podcast": {"quotes": quotes}}

        checked = _checked(extraction, _index())

        assert [q["quote"] for q in checked["podcast"]["quotes"]] == ["Pencils down."]

    def test_should_check_a_narrative_quotes_text(self) -> None:
        extraction = {"narrative": {"quotes": [{"text": _PARAPHRASE, "speaker": "Host"}]}}

        checked = _checked(extraction, _index("he"))

        assert checked["narrative"]["quotes"][0]["quoteTranslated"] is True

    def test_should_check_quotes_nested_in_an_itinerary(self) -> None:
        spot = {"name": "Ichiran", "quote": _PARAPHRASE}
        extraction = {"travel": {"itinerary": [{"day": 1, "spots": [spot]}]}}

        checked = _checked(extraction, _index())

        assert checked["travel"]["itinerary"][0]["spots"][0]["quote"] is None

    def test_should_check_a_quiz_questions_quote(self) -> None:
        enrichment = {"quiz": [{"question": "q", "options": ["a", "b"], "quote": _PARAPHRASE}]}

        checked = verify_extraction_quotes(None, enrichment, _index()).enrichment

        assert checked is not None and checked["quiz"][0]["quote"] is None

    def test_should_count_every_status_in_the_tally(self) -> None:
        extraction = {
            "learning": {"keyIdeas": [{"quote": "Pencils down."}, {"quote": _PARAPHRASE}]}
        }

        tally = verify_extraction_quotes(extraction, None, _index()).tally

        assert (tally.checked, tally.verified, tally.unverified, tally.unverified_share) == (
            2,
            1,
            1,
            0.5,
        )


# ─── the run's index ───


def _ctx(
    segments: list[dict], prompt: list[dict] | None = None, language: str | None = None
) -> Any:
    """A context stand-in carrying only what the index reads."""
    transcript = SimpleNamespace(segments=segments)
    return SimpleNamespace(
        transcript_data=transcript, prompt_segments=prompt or [], source_language_code=language
    )


class TestRunQuoteIndex:
    def test_should_build_the_index_once_per_run(self) -> None:
        ctx = _ctx(_SEGMENTS)

        assert run_quote_index(ctx) is run_quote_index(ctx)

    def test_should_read_the_prompt_segments_when_the_fetch_kept_none(self) -> None:
        ctx = _ctx([], prompt=_SEGMENTS)

        index = run_quote_index(ctx)

        assert index is not None and verify_quote("Pencils down.", index).status == VERIFIED

    def test_should_check_nothing_when_the_run_has_no_timed_transcript(self) -> None:
        assert run_quote_index(_ctx([])) is None

    def test_should_read_the_runs_source_language(self) -> None:
        index = run_quote_index(_ctx(_SEGMENTS, language="he"))

        assert index is not None and verify_quote(_PARAPHRASE, index).status == TRANSLATED


# ─── assembly ───


def _tab(tab_id: str, component: str, source: str) -> dict:
    return {"id": tab_id, "label": tab_id, "component": component, "dataSource": source}


def _assemble(tabs: list[dict], extraction: dict, index: QuoteIndex, tag: str = "learning"):
    triage = {"primaryTag": tag, "contentTags": [tag], "tabs": tabs}
    return assemble_response(triage, extraction, None, None, quote_index=index)


def _shipped(result: dict, tab_id: str) -> dict:
    return next(t for t in result["tabs"] if t["id"] == tab_id)


_IDEAS = [
    {"claim": "Code by hand is the exception", "why": "w", "timestamp": 63},
    {"claim": "Pay the card first", "why": "w", "timestamp": 90},
]


class TestAssemblyQuotes:
    def _key_ideas(self, quotes: list[str], index: QuoteIndex) -> list[dict]:
        ideas = [{**idea, "quote": q} for idea, q in zip(_IDEAS, quotes, strict=True)]
        tabs = [_tab("ideas", "spot_explorer", "learning.keyIdeas")]
        result = _assemble(tabs, {"learning": {"keyIdeas": ideas}}, index)
        return _shipped(result, "ideas")["props"]["spots"]

    def test_should_ship_no_quote_on_a_card_whose_quote_is_unverified(self) -> None:
        spots = self._key_ideas(["Pencils down.", _PARAPHRASE], _index())

        assert [s.get("quote") for s in spots] == ["Pencils down.", None]

    def test_should_flag_a_translated_quote_on_its_card(self) -> None:
        spots = self._key_ideas([_PARAPHRASE, _PARAPHRASE], _index("he"))

        assert [s.get("quoteTranslated") for s in spots] == [True, True]

    def test_should_flag_a_translated_quote_on_its_moment(self) -> None:
        moments = [
            {"seconds": 63, "label": "Pencils down", "description": "d", "quote": _PARAPHRASE},
            {"seconds": 90, "label": "Debt first", "description": "d", "quote": _PARAPHRASE},
        ]
        tabs = [_tab("moments", "moment_track", "learning.timestamps")]

        result = _assemble(tabs, {"learning": {"timestamps": moments}}, _index("he"))
        items = _shipped(result, "moments")["props"]["items"]

        assert all(item.get("quoteTranslated") is True for item in items)

    def test_should_leave_the_callers_extraction_untouched(self) -> None:
        extraction = {"learning": {"keyIdeas": [{**i, "quote": _PARAPHRASE} for i in _IDEAS]}}
        before = copy.deepcopy(extraction)

        _assemble([_tab("ideas", "spot_explorer", "learning.keyIdeas")], extraction, _index())

        assert extraction == before


# ─── D13 extended (2.0 c) ───


def _rankings(quote: str | None) -> list[dict]:
    names = ("Loki", "Nami", "Zoro")
    return [
        {"item": n, "tier": "S", "reason": f"{n} matters", "timestamp": 400, "quote": quote}
        for n in names
    ]


class TestTiers:
    def _pulls(self, quote: str | None, index: QuoteIndex) -> dict:
        extraction = {"gaming": {"rankings": _rankings(quote)}}
        result = _assemble(
            [_tab("pulls", "tier_list", "gaming.rankings")], extraction, index, "gaming"
        )
        return _shipped(result, "pulls")

    def test_should_ship_a_tier_the_creator_states_in_words(self) -> None:
        tab = self._pulls("Loki is S tier for me", _index())

        assert [i["tier"] for i in tab["props"]["items"]] == ["S", "S", "S"]

    def test_should_ship_an_untiered_list_when_no_rank_is_quoted(self) -> None:
        # S09 "The Pulls": a rarity bracket is no rank — the items stay, the tiers go.
        tab = self._pulls(None, _index())

        assert (tab["component"], tab.get("degradedFrom")) == ("info_grid", "tier_list")

    def test_should_keep_each_pulls_reason_and_stamp_when_the_tiers_go(self) -> None:
        items = self._pulls(None, _index())["props"]["items"]

        assert [(i["key"], i["value"], i["timestamp"]) for i in items][0] == (
            "Loki",
            "Loki matters",
            400,
        )

    def test_should_drop_the_tiers_when_their_quote_is_not_in_the_transcript(self) -> None:
        tab = self._pulls("Loki is the best card ever printed, a total S", _index())

        assert tab["component"] == "info_grid"

    def test_should_ship_a_tier_whose_quote_is_a_translation(self) -> None:
        tab = self._pulls(_PARAPHRASE, _index("he"))

        assert tab["component"] == "tier_list"


class TestClaimStatus:
    def _claims(self, quote: str | None) -> list[dict]:
        data = [
            {"claim": "c1", "status": "supported", "timestamp": 90, "quote": quote},
            {"claim": "c2", "status": "contested", "timestamp": 120, "quote": quote},
        ]
        props = assemble_claims_tracker({}, data, {}, None)
        assert props is not None
        return props["claims"]

    def test_should_ship_no_status_without_the_words_that_make_the_claim(self) -> None:
        assert [c.get("status") for c in self._claims(None)] == [None, None]

    def test_should_keep_the_seek_point_when_the_status_goes(self) -> None:
        assert [c.get("timestamp") for c in self._claims(None)] == [90, 120]

    def test_should_ship_the_status_with_its_stamp_and_quote(self) -> None:
        quoted = self._claims("Paying off a card at 24% beats an index fund.")

        assert [c["status"] for c in quoted] == ["supported", "contested"]


class TestScoresAndBadges:
    def test_should_ship_no_score_without_the_reviewers_words(self) -> None:
        assert stated_number({"score": 8.5, "timestamp": 612}, "score") is None

    def test_should_ship_a_score_stated_with_its_stamp_and_words(self) -> None:
        rating = {"score": 8.5, "timestamp": 612, "quote": "An eight and a half."}

        assert stated_number(rating, "score") == 8.5

    def test_should_spread_no_badge_without_the_reviewers_words(self) -> None:
        verdict = {"badge": "recommended", "bottomLine": "Buy it.", "timestamp": 5}

        assert stated_block_scalars(verdict, "verdict") == {"bottomLine": "Buy it.", "timestamp": 5}

    def test_should_render_no_verdict_badge_when_the_reviewers_words_are_missing(self) -> None:
        verdict = {"badge": "recommended", "bottomLine": "Buy it.", "timestamp": 5}

        props = assemble_verdict({}, verdict, {}, None)

        assert props is not None and "badge" not in props["verdict"]

    def test_should_render_the_verdict_badge_when_stamped_and_quoted(self) -> None:
        verdict = {
            "badge": "recommended",
            "bottomLine": "Buy it.",
            "timestamp": 5,
            "quote": "I recommend it.",
        }

        props = assemble_verdict({}, verdict, {}, None)

        assert props is not None and props["verdict"]["badge"] == "recommended"

    def test_should_still_state_a_duration_with_its_stamp_alone(self) -> None:
        assert stated_scalars({"duration": "10 min", "timestamp": 5}, ("duration",)) == {
            "duration": "10 min"
        }

    def test_should_not_spread_a_translated_quote_or_the_check_on_the_overview(self) -> None:
        verdict = {
            "bottomLine": "Buy it.",
            "quote": _PARAPHRASE,
            "quoteStatus": TRANSLATED,
            "quoteTranslated": True,
        }
        tab = {"_primary_tag": "review", "_video_meta": {"title": "T"}}

        props = assemble_overview(tab, None, {"review": {"verdict": verdict}}, None)

        assert props is not None
        assert {"quote", "quoteStatus", "quoteTranslated"} & set(props["data"]) == set()


# ─── schemas + models (live runs can earn a verdict) ───


class TestVerdictQuoteFields:
    @pytest.mark.parametrize(
        ("domain", "field"),
        [("gaming", "rankings"), ("news", "claims"), ("learning", "claims"), ("tech", "claims")],
    )
    def test_should_ask_for_the_words_that_state_the_verdict(self, domain: str, field: str) -> None:
        assert "quote" in slice_skeleton(domain, [field])[field][0]

    def test_should_ask_the_reviewer_for_the_words_of_the_score_and_the_verdict(self) -> None:
        review = slice_skeleton("review", ["rating", "verdict"])

        assert ("quote" in review["rating"], "quote" in review["verdict"]) == (True, True)

    @pytest.mark.parametrize("model", [GamingRanking, Claim])
    def test_should_keep_the_quote_through_validation(self, model: Any) -> None:
        base = {"item": "Loki"} if model is GamingRanking else {"claim": "c"}

        assert model.model_validate({**base, "quote": " S tier. "}).quote == "S tier."

    @pytest.mark.parametrize("model", [ReviewRating, ReviewVerdict])
    def test_should_clear_a_blank_quote_through_validation(self, model: Any) -> None:
        assert model.model_validate({"quote": "  "}).quote is None
