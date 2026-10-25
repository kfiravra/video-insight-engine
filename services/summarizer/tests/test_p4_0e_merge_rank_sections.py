"""Task 4.0e: merge_rank's sections place by the stamp's window first and the words second,
title a joined section after the member its ideas name best, ship whole-phrase titles and
read in video order; a Q&A question is never dropped for a twin in another list.

The S03 regression uses the real outline gate 3 stored (trace.json, merge_rank input).
"""

from __future__ import annotations

import copy
from collections import Counter
from typing import Any

from src.models.memory_types import OutlineSection
from src.services.pipeline.extraction_chunks import ExtractionChunk
from src.services.pipeline.merge_rank import read_answer
from src.services.pipeline.merge_rank_apply import MergeRankAnswer, apply_merge_rank
from src.services.pipeline.merge_rank_render import MergeRankRequest, RenderInput, build_request
from src.services.pipeline.merge_rank_sections import (
    EDGE_S,
    _joined_titles,
    idea_words,
    place_stamp,
    section_names,
)

_DURATION = 3600
_PODCAST_S = 5400  # a timeline of 16 clears its band minimum (12) and fits its cap (18)
_CHUNKS = (ExtractionChunk(index=0, transcript="", end_s=_DURATION, video_end_s=_DURATION),)
_IDEAS_TAB = {"id": "ideas", "label": "Positions", "component": "spot_explorer",
              "dataSource": "learning.keyIdeas"}  # fmt: skip
_QA_TAB = {"id": "qa", "label": "Questions Asked", "component": "moment_track",
           "dataSource": "podcast.qa"}  # fmt: skip


def _section(start: int, end: int, title: str, establishes: str = "") -> OutlineSection:
    return OutlineSection(start=start, end=end, title=title, establishes=establishes)


# Two neighbours: the first is about pricing, the second about hiring.
_PAIR = (
    _section(0, 600, "Pricing the subscription tiers", "annual plans beat monthly plans"),
    _section(600, 1200, "Hiring the first engineers", "recruiting senior engineers early"),
)
_S03_OUTLINE = (
    _section(0, 130, "opening scenario and the audience's beliefs about biology",
             "sudden personality change may trace to a single gene"),
    _section(130, 440, "hormones and the body-brain link, two course principles",
             "body states shape brain states, and brain states shape body states"),
    _section(1950, 2220, "three intellectual challenges: simple, mismatched, and…",
             "humans share boring physiology but use it in unprecedented ways"),
    _section(2220, 2580, "course structure, halves, and prerequisite catch-up sections",
             "the first half surveys buckets; the second applies them to behaviors"),
)  # fmt: skip


def _idea(claim: str, seconds: int | None, label: str = "") -> dict[str, Any]:
    idea: dict[str, Any] = {"claim": claim, "why": "because", "section": label}
    if seconds is not None:
        idea["timestamp"] = seconds
    return idea


def _sectioned(
    ideas: list[dict[str, Any]], outline: tuple[OutlineSection, ...]
) -> list[dict[str, Any]]:
    extraction = {"learning": {"keyIdeas": ideas}}
    request = build_request(RenderInput(extraction, [_IDEAS_TAB], outline, _CHUNKS, _DURATION))
    assert request is not None
    result, _ = apply_merge_rank(extraction, MergeRankAnswer("failed", request))
    return result["learning"]["keyIdeas"]


def _sections_of(shipped: list[dict[str, Any]]) -> list[str]:
    return [idea["section"] for idea in shipped]


class TestPlaceStamp:
    def test_should_move_an_idea_to_the_neighbour_section_when_its_words_match_by_two_more(
        self,
    ) -> None:
        words = idea_words(_idea("Hiring senior engineers early", 590))

        assert place_stamp(590, words, _PAIR) == 1

    def test_should_keep_the_stamps_section_when_the_words_tie(self) -> None:
        words = idea_words(_idea("Growth needs patience", 590))

        assert place_stamp(590, words, _PAIR) == 0

    def test_should_keep_the_stamps_section_when_the_margin_is_under_two(self) -> None:
        words = idea_words(_idea("Hiring plans", 590))

        assert place_stamp(590, words, _PAIR) == 0

    def test_should_keep_the_stamps_section_when_the_stamp_is_far_from_the_edge(self) -> None:
        words = idea_words(_idea("Hiring senior engineers early", 600 - EDGE_S - 1))

        assert place_stamp(600 - EDGE_S - 1, words, _PAIR) == 0

    def test_should_place_nothing_without_a_stamp(self) -> None:
        assert place_stamp(None, frozenset({"hiring"}), _PAIR) is None


class TestTitles:
    def test_should_title_a_joined_section_with_the_member_its_ideas_name_best(self) -> None:
        counts = Counter({0: 3, 1: 1} | {n: 5 for n in range(2, 9)})
        names = [f"part {n}" for n in range(9)]

        titles = _joined_titles(counts, names, lambda group, member: 4 if member == 1 else 0)

        assert titles[0] == titles[1] == "part 1"

    def test_should_fall_back_to_the_largest_member_when_no_words_fit(self) -> None:
        counts = Counter({n: 5 for n in range(9)} | {0: 3, 1: 1})

        names = section_names([_section(n, n + 1, f"part {n + 1}") for n in range(9)])

        titles = _joined_titles(counts, names, lambda group, member: 0)

        assert titles[1] == titles[0] == "Part 1"

    def test_should_cut_a_truncated_outline_title_back_to_a_whole_phrase(self) -> None:
        cut = [
            "three intellectual challenges: simple, mismatched, and…",
            "defining longevity, lifespan vs healthspan, and the four…",
            "phone-number and number-series demonstrations of…",
        ]

        shipped = section_names([_section(n, n + 1, t) for n, t in enumerate(cut)])

        assert shipped == [
            "Three intellectual challenges",
            "Defining longevity, lifespan vs healthspan",
            "Phone-number and number-series demonstrations",
        ]

    def test_should_leave_a_title_that_was_not_cut_as_it_is(self) -> None:
        shipped = section_names([_section(0, 1, "macOS config pain")])

        assert shipped[0] == "macOS config pain"


class TestOrder:
    def test_should_order_the_sectioned_ideas_by_section_and_stamp(self) -> None:
        ideas = [
            _idea("Hiring senior engineers early", 590),
            _idea("Annual plans beat monthly plans", 100),
            _idea("Pricing tiers need an annual plan", 590 - 1),
        ]

        shipped = _sectioned(ideas, _PAIR)

        assert [i["claim"] for i in shipped] == [
            "Annual plans beat monthly plans",
            "Pricing tiers need an annual plan",
            "Hiring senior engineers early",
        ]

    def test_should_keep_an_idea_without_a_stamp_after_the_stamped_ones(self) -> None:
        shipped = _sectioned([_idea("No stamp", None), _idea("Pricing tiers", 50)], _PAIR)

        assert [i["claim"] for i in shipped] == ["Pricing tiers", "No stamp"]


class TestS03Regression:
    def test_should_place_s03s_second_challenge_under_three_intellectual_challenges(self) -> None:
        second = _idea(
            "Second challenge: in some cases we have the same boring physiology as other "
            "animals but use it in ways no other animal could",
            2280,
            "Three intellectual challenges",
        )
        opening = _idea(
            "Many people hold beliefs about biology and behavior that this course will change",
            144,
            "Opening scenario",
        )

        shipped = _sectioned([opening, second], _S03_OUTLINE)

        assert _sections_of(shipped) == [
            "Opening scenario and the audience's beliefs about biology",
            "Three intellectual challenges",
        ]


# ─── A Q&A question and a position that say the same ───


def _qa_request(
    positions: list[dict[str, Any]], questions: list[dict[str, Any]]
) -> tuple[dict[str, Any], MergeRankRequest]:
    extraction = {"learning": {"keyIdeas": positions}, "podcast": {"qa": questions}}
    tabs = [_IDEAS_TAB, _QA_TAB]
    request = build_request(RenderInput(extraction, tabs, (), _CHUNKS, _PODCAST_S))
    assert request is not None
    return extraction, request


def _spread_positions(n: int) -> list[dict[str, Any]]:
    return [_idea(f"position {k}", 450 * k, f"c{k}") for k in range(n)]


def _spread_questions(n: int) -> list[dict[str, Any]]:
    return [
        {"question": f"question {k}", "answer": f"answer {k}", "timestamp": 300 * k + 50}
        for k in range(n)
    ]


class TestQaTwins:
    def test_should_keep_a_podcast_qa_item_when_its_twin_is_a_position(self) -> None:
        extraction, request = _qa_request(_spread_positions(10), _spread_questions(16))
        data = {"duplicates": [{"keep": "K3", "drop": ["Q3"]}]}

        result, record = apply_merge_rank(copy.deepcopy(extraction), read_answer(data, request))

        assert (len(result["podcast"]["qa"]), [h["guard"] for h in record["held"]]) == (
            16,
            ["qa_twin"],
        )

    def test_should_drop_a_podcast_qa_item_when_its_twin_is_another_podcast_qa_item(self) -> None:
        extraction, request = _qa_request(_spread_positions(10), _spread_questions(16))
        data = {"duplicates": [{"keep": "Q3", "drop": ["Q4"]}]}

        result, record = apply_merge_rank(copy.deepcopy(extraction), read_answer(data, request))

        assert (len(result["podcast"]["qa"]), [d["id"] for d in record["dropped"]]) == (15, ["Q4"])

    def test_should_still_drop_a_position_when_its_twin_is_a_podcast_qa_item(self) -> None:
        extraction, request = _qa_request(_spread_positions(10), _spread_questions(16))
        data = {"duplicates": [{"keep": "Q3", "drop": ["K4"]}]}

        result, _ = apply_merge_rank(copy.deepcopy(extraction), read_answer(data, request))

        assert len(result["learning"]["keyIdeas"]) == 9
