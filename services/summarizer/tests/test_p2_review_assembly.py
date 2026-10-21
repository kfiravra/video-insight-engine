"""p2 review fixes, lane 3 — assembly and the quote check (review-p2-A/C/D.md).

A1 the dropped-tab scrub never empties a hero field, drops a takeaway with its claim
(never its bare ``[m:ss]``) and gives multi-word labels the exact-case rule; A2 a
"…"-cut quote's fragments sit in order and close together; A3 an untiered tier list
ships as a spot list; A4 a degraded tab's goal no longer promises a ranking; A6 the quiz
normalizer's extras; C2 a promoted / folded grid spot's quote is only its item's checked
quote; D11 the re-assembly tool counts only verdicts without a stamp or quote.
No LLM, no I/O beyond the gate tool's own module.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from src.services.pipeline.assembly import assemble_info_grid, assemble_response, promote_component
from src.services.pipeline.assembly.info_grid_fold import apply_folds
from src.services.pipeline.assembly.normalizers import _normalize_to_spot, _quiz_extras
from src.services.pipeline.assembly.promotion import INFO_GRID_LONG_VALUE, demote_component
from src.services.pipeline.assembly.tab_references import (
    auto_tab_candidates,
    goal_without_lost_structure,
    registry_goal,
    scrub_tab_references,
)
from src.services.pipeline.quote_verify import UNVERIFIED, VERIFIED, QuoteIndex, verify_quote
from src.shared_config.assembly_policy import injected_tab

# ─── A1 the dropped-tab scrub ───

_HERO_LINES = ("tldr", "brief", "worthItFor", "skipIf")
# The frames gallery ("Visual Moments") is an auto-tab candidate that ships here not at all.
_SHIPPED = [{"id": "itinerary", "label": "Day by Day"}]


def _scrubbed(meta: dict[str, Any]) -> dict[str, Any]:
    scrub_tab_references(meta, [dict(tab) for tab in _SHIPPED], auto_tab_candidates())
    return meta


class TestScrubNeverEmpties:
    @pytest.mark.parametrize("key", _HERO_LINES)
    def test_should_keep_a_line_that_uses_a_dropped_label_as_prose_when_it_is_all_there_is(
        self, key: str
    ) -> None:
        line = "The vlog strings together visual moments from Hanoi."

        assert _scrubbed({key: line})[key] == line

    @pytest.mark.parametrize("key", _HERO_LINES)
    def test_should_keep_a_one_sentence_line_with_no_clean_cut_when_it_names_a_dropped_tab(
        self, key: str
    ) -> None:
        line = "Open the Visual Moments tab for every stage."

        assert _scrubbed({key: line})[key] == line

    @pytest.mark.parametrize("key", _HERO_LINES)
    def test_should_drop_only_the_naming_clause_when_every_sentence_names_a_dropped_tab(
        self, key: str
    ) -> None:
        line = "A ten-day route through Vietnam, with the Visual Moments tab showing every stop."

        assert _scrubbed({key: line})[key] == "A ten-day route through Vietnam."

    def test_should_still_drop_a_naming_sentence_when_another_sentence_remains(self) -> None:
        brief = "A ten-day route through Vietnam. The Visual Moments tab shows every stop."

        assert _scrubbed({"brief": brief})["brief"] == "A ten-day route through Vietnam."

    def test_should_never_empty_a_goal(self) -> None:
        tabs = [{"id": "itinerary", "label": "Day by Day", "goal": "Then open Visual Moments."}]

        scrub_tab_references({}, tabs, auto_tab_candidates())

        assert tabs[0]["goal"] == "Then open Visual Moments."


class TestTakeawayMarker:
    def test_should_drop_the_whole_takeaway_when_its_claim_names_a_dropped_tab(self) -> None:
        meta = {"keyTakeaways": ["Visual Moments shows each reveal. [3:20]", "Book early. [1:05]"]}

        assert _scrubbed(meta)["keyTakeaways"] == ["Book early. [1:05]"]

    def test_should_keep_the_marker_on_the_claim_that_survives(self) -> None:
        meta = {"keyTakeaways": ["He pulls Loki. The Visual Moments tab shows it. [3:20]"]}

        assert _scrubbed(meta)["keyTakeaways"] == ["He pulls Loki. [3:20]"]

    def test_should_read_an_hour_marker_as_the_closing_stamp(self) -> None:
        meta = {"keyTakeaways": ["Visual Moments has it all. [1:02:03]"]}

        assert _scrubbed(meta)["keyTakeaways"] == []


class TestMultiWordLabelCase:
    def test_should_keep_a_lowercase_phrase_outside_a_tab_sentence(self) -> None:
        tldr = "Keep. The visual moments are lovely."

        assert _scrubbed({"tldr": tldr})["tldr"] == tldr

    def test_should_drop_the_label_in_its_own_case_even_as_the_first_words(self) -> None:
        assert _scrubbed({"tldr": "Keep. Visual Moments shows every stage."})["tldr"] == "Keep."

    def test_should_drop_any_case_in_a_sentence_about_tabs(self) -> None:
        assert _scrubbed({"tldr": "Keep. The visual moments tab is gone."})["tldr"] == "Keep."


# ─── A2 a "…"-cut quote ───

_STITCH = [
    {"start": 10, "text": "AI is everywhere in this talk."},
    {"start": 900, "text": "And AI will not replace the people who use it well."},
    {"start": 1800, "text": "Hire programmers who read code."},
]
_SAID = [
    {"start": 20, "text": "So the real problem is not what the model decided."},
    {"start": 41, "text": "It's who let it decide alone, who gave it the keys?"},
    {"start": 63, "text": "Pencils down. Writing code by hand is now the exception."},
    {"start": 300, "text": "Pencils down. I said it before and I'll say it again."},
    {"start": 400, "text": "Loki is S tier for me, no question about it."},
]


def _gap_index(gap_words: int) -> QuoteIndex:
    filler = " ".join(f"filler{i}" for i in range(gap_words))
    segments = [
        {"start": 0, "text": "alpha beta gamma delta"},
        {"start": 5, "text": filler},
        {"start": 20, "text": "omega psi chi"},
    ]
    return QuoteIndex.build(segments)


class TestCutQuoteFragments:
    def test_should_refuse_fragments_stitched_from_lines_far_apart(self) -> None:
        verdict = verify_quote("AI … replace … programmers", QuoteIndex.build(_STITCH))

        assert verdict.status == UNVERIFIED

    def test_should_refuse_fragments_out_of_order(self) -> None:
        quote = "who gave it the keys … the real problem is not what the model decided"

        assert verify_quote(quote, QuoteIndex.build(_SAID)).status == UNVERIFIED

    def test_should_refuse_a_long_fragment_said_minutes_later(self) -> None:
        quote = "the real problem is not what the model decided … Loki is S tier for me"

        assert verify_quote(quote, QuoteIndex.build(_SAID)).status == UNVERIFIED

    def test_should_refuse_a_short_fragment_said_only_outside_the_bound(self) -> None:
        quote = "the real problem is not what the model decided … question"

        assert verify_quote(quote, QuoteIndex.build(_SAID)).status == UNVERIFIED

    def test_should_verify_a_short_fragment_inside_the_bound(self) -> None:
        quote = "the real problem is not what the model decided … alone"

        assert verify_quote(quote, QuoteIndex.build(_SAID)).status == VERIFIED

    def test_should_verify_an_earlier_short_fragment_right_before_the_longest(self) -> None:
        verdict = verify_quote(
            "Pencils down … writing code by hand is now the exception", QuoteIndex.build(_SAID)
        )

        assert (verdict.status, verdict.seconds) == (VERIFIED, 63)

    def test_should_refuse_an_earlier_fragment_more_than_a_minute_before(self) -> None:
        # "Pencils down" at 300 is 9 words but 100 s before "Loki is S tier" at 400.
        quote = "Pencils down … Loki is S tier for me"

        assert verify_quote(quote, QuoteIndex.build(_SAID)).status == UNVERIFIED

    def test_should_verify_fragments_thirty_words_apart(self) -> None:
        assert verify_quote("alpha beta gamma … omega psi chi", _gap_index(30)).status == VERIFIED

    def test_should_refuse_fragments_fifty_words_apart(self) -> None:
        verdict = verify_quote("alpha beta gamma … omega psi chi", _gap_index(50))

        assert verdict.status == UNVERIFIED


# ─── A3 an untiered tier list ───

_RANKINGS = [
    {"item": "Loki (secret rare)", "tier": "S", "reason": "The card he hunts.", "timestamp": 643},
    {"item": "Luffy alt art SR", "tier": "S", "reason": "The SR he needs.", "timestamp": 380},
    {"item": "Pandaman", "tier": "A", "reason": "For the binder.", "timestamp": 650},
]


def _gaming(rankings: list[dict], goal: str = "") -> dict:
    tab = {"id": "pulls", "label": "The Pulls", "component": "tier_list"}
    tab |= {"dataSource": "gaming.rankings", "goal": goal}
    triage = {"primaryTag": "gaming", "contentTags": ["gaming"], "tabs": [tab]}
    result = assemble_response(triage, {"gaming": {"rankings": rankings}}, None, None)
    return next(t for t in result["tabs"] if t["id"] == "pulls")


class TestUntieredTierList:
    def test_should_ship_an_unquoted_tier_list_as_a_spot_list(self) -> None:
        tab = _gaming(_RANKINGS)

        assert (tab["component"], tab.get("degradedFrom")) == ("spot_explorer", "tier_list")

    def test_should_keep_each_picks_name_reason_and_stamp_without_its_tier(self) -> None:
        spot = _gaming(_RANKINGS)["props"]["spots"][0]

        assert spot == {
            "name": "Loki (secret rare)",
            "description": "The card he hunts.",
            "timestamp": 643,
        }

    def test_should_read_a_tier_row_as_a_spot(self) -> None:
        assert _normalize_to_spot({"item": "Loki", "reason": "chase"}) == {
            "name": "Loki",
            "description": "chase",
        }

    def test_should_fall_back_to_a_grid_only_when_the_spot_rung_is_ruled_out(self) -> None:
        demoted = demote_component(
            "tier_list", {}, _RANKINGS, {}, None, frozenset({"spot_explorer"})
        )

        assert demoted is not None and demoted[0] == "info_grid"


# ─── A4 a degraded tab's goal ───

_DEFAULT_TIER_GOAL = (
    "After this you know how the creator ranks each pick, and you can rank your own."
)
_RANK_WORDS = ("rank", "tier", "rate", "best", "highest")


class TestDegradedGoal:
    def test_should_replace_the_registry_default_goal_that_promises_a_ranking(self) -> None:
        goal = goal_without_lost_structure(
            _DEFAULT_TIER_GOAL, "tier_list", "spot_explorer", "gaming"
        )

        assert not any(word in goal.lower() for word in _RANK_WORDS)

    def test_should_give_a_gaming_spot_list_the_registrys_list_goal(self) -> None:
        goal = goal_without_lost_structure(
            _DEFAULT_TIER_GOAL, "tier_list", "spot_explorer", "gaming"
        )

        assert goal == injected_tab("keyInfo")["goal"]

    def test_should_replace_a_goal_that_opens_on_what_he_rates_highest(self) -> None:
        goal = "After this you know which pulls he rates highest and why."

        assert goal_without_lost_structure(goal, "tier_list") == injected_tab("keyInfo")["goal"]

    def test_should_take_the_shipped_rungs_goal_from_its_domains_registry(self) -> None:
        goal = goal_without_lost_structure(
            _DEFAULT_TIER_GOAL, "tier_list", "spot_explorer", "travel"
        )

        assert goal == registry_goal("travel", "spot_explorer")

    def test_should_cut_at_the_first_naming_clause_keeping_the_goals_own_words(self) -> None:
        goal = "After this you know each card, its price, and the tier he gives it."

        assert goal_without_lost_structure(goal, "tier_list") == (
            "After this you know each card, its price."
        )

    def test_should_ship_the_default_goal_degraded_end_to_end_without_a_ranking(self) -> None:
        goal = _gaming(_RANKINGS, _DEFAULT_TIER_GOAL)["goal"]

        assert not any(word in goal.lower() for word in _RANK_WORDS)


# ─── A6 the quiz normalizer's extras ───


class TestQuizExtras:
    def test_should_carry_a_translated_evidence_line_as_prose(self) -> None:
        item = {
            "explanation": "Because.",
            "timestamp": 75,
            "quote": " Words. ",
            "quoteTranslated": True,
        }

        assert _quiz_extras(item, 600) == {
            "explanation": "Because.",
            "timestamp": 75,
            "quote": "Words.",
            "quoteTranslated": True,
        }

    def test_should_leave_out_an_unset_quote_and_stamp(self) -> None:
        assert _quiz_extras({"explanation": "Because."}, None) == {"explanation": "Because."}


# ─── C2 a promoted / folded grid spot's quote ───

_LONG = "x" * (INFO_GRID_LONG_VALUE + 30)


def _terms() -> list[dict]:
    return [
        {"term": "Agent", "definition": _LONG, "example": "a refund bot", "timestamp": 20},
        {"term": "Tool", "definition": _LONG, "quote": "who gave it the keys", "timestamp": 41},
    ]


def _promoted(data: list[dict]) -> list[dict]:
    grid = assemble_info_grid({}, data, {}, None)
    assert grid is not None
    component, props = promote_component("info_grid", grid, data, {}, "learning")
    assert component == "spot_explorer"
    return props["spots"]


class TestPromotedSpotQuote:
    def test_should_ship_an_example_as_prose_not_as_the_speakers_words(self) -> None:
        spot = _promoted(_terms())[0]

        assert ("quote" in spot, spot["description"].endswith("— a refund bot")) == (False, True)

    def test_should_ship_the_items_checked_quote_as_the_spot_quote(self) -> None:
        assert _promoted(_terms())[1]["quote"] == "who gave it the keys"

    def test_should_flag_a_translated_quote_on_the_spot(self) -> None:
        data = _terms()
        data[1]["quoteTranslated"] = True

        assert _promoted(data)[1].get("quoteTranslated") is True


class TestFoldedSpotQuote:
    def _folded(self, data: list[dict]) -> list[dict]:
        host: dict[str, Any] = {"id": "ideas", "label": "Key Ideas", "component": "spot_explorer"}
        host["props"] = {"spots": [{"name": "Idea", "description": "d"}]}
        fold = {"id": "terms", "label": "Terms", "dataSource": "learning.terms"}

        apply_folds(host, [fold], {}, None, lambda source, extraction, enrichment: data)

        return host["props"]["spots"][1:]

    def test_should_ship_a_folded_rows_example_as_prose(self) -> None:
        spot = self._folded(_terms())[0]

        assert ("quote" in spot, spot["description"].endswith("— a refund bot")) == (False, True)

    def test_should_ship_a_folded_rows_checked_quote_with_its_translated_flag(self) -> None:
        data = _terms()
        data[1]["quoteTranslated"] = True
        spot = self._folded(data)[1]

        assert (spot["quote"], spot.get("quoteTranslated")) == ("who gave it the keys", True)


# ─── D11 the re-assembly tool's verdict count ───

_TOOLS = Path(__file__).resolve().parents[3] / "dev" / "active" / "output-quality" / "tools"


@pytest.fixture(scope="module")
def reassemble() -> ModuleType:
    if not (_TOOLS / "reassemble.py").is_file():
        pytest.skip("the output-quality gate tools are archived")
    with pytest.MonkeyPatch.context() as patch:
        patch.chdir(Path.cwd())  # the tool chdirs to the summarizer on import
        patch.syspath_prepend(str(_TOOLS))
        module = importlib.import_module("reassemble")
    sys.modules.pop("reassemble", None)
    return module


_TIER_TAB = {"component": "tier_list", "props": {"items": [{"item": "Loki", "tier": "S"}]}}


class TestUnbackedVerdicts:
    def test_should_count_a_verdict_without_a_stamp(self, reassemble: ModuleType) -> None:
        assert reassemble.unbacked_verdicts([_TIER_TAB], {643}) == 1

    def test_should_not_count_a_stamped_verdict_whose_words_the_extraction_holds(
        self, reassemble: ModuleType
    ) -> None:
        tab = {"props": {"items": [{"item": "Loki", "tier": "S", "timestamp": 643}]}}
        extraction = {"gaming": {"rankings": [{"item": "Loki", "timestamp": 643, "quote": "S"}]}}

        assert reassemble.unbacked_verdicts([tab], reassemble.quoted_seconds(extraction)) == 0

    def test_should_count_a_stamped_verdict_whose_quote_the_check_cleared(
        self, reassemble: ModuleType
    ) -> None:
        tab = {"props": {"claims": [{"claim": "c", "status": "supported", "timestamp": 90}]}}
        cleared = {
            "news": {"claims": [{"timestamp": 90, "quote": None, "quoteStatus": UNVERIFIED}]}
        }

        assert reassemble.unbacked_verdicts([tab], reassemble.quoted_seconds(cleared)) == 1

    def test_should_not_count_a_verdict_carrying_its_own_quote(
        self, reassemble: ModuleType
    ) -> None:
        tab = {"props": {"spots": [{"name": "n", "rating": 4, "timestamp": 5, "quote": "four"}]}}

        assert reassemble.unbacked_verdicts([tab], set()) == 0
