"""merge_rank's answer read and applied (output-quality 3.1) — pure code, no LLM.

Reading: an unknown id goes, a keep never drops itself, an id two groups claim stays
with the first, a section number outside the outline falls back to the stamp's section,
the rank keeps the ranked lists' ids. Applying: drops by id while the item is still
where the call saw it, under five guards (40 % per list, the density-gate minimum, a
timeline's band minimum, no 10-minute window emptied); a ranked list over its cap is cut
by rank tier with every window kept; key ideas take ≤ 8 outline titles.
"""

from __future__ import annotations

import copy
from collections import Counter
from typing import Any

from src.models.memory_types import OutlineSection
from src.services.pipeline.extraction_chunks import ExtractionChunk
from src.services.pipeline.merge_rank import (
    read_answer,
    read_duplicates,
    read_rank,
    read_sections,
)
from src.services.pipeline.merge_rank_apply import (
    MergeRankAnswer,
    apply_merge_rank,
    refill_windows,
    section_titles,
    window_of,
)
from src.services.pipeline.merge_rank_render import MergeRankRequest, RenderInput, build_request

_DURATION = 3600
_CHUNKS = (
    ExtractionChunk(index=0, transcript="", end_s=1800, video_end_s=_DURATION),
    ExtractionChunk(index=1, transcript="", start_s=1800, video_end_s=_DURATION),
)
# Twelve five-minute sections.
_OUTLINE = tuple(
    OutlineSection(start=300 * n, end=300 * (n + 1), title=f"part {n + 1}") for n in range(12)
)
_TABS = {
    "ideas": {"id": "ideas", "label": "Positions", "component": "spot_explorer",
              "dataSource": "learning.keyIdeas"},
    "moments": {"id": "moments", "label": "Moments", "component": "moment_track",
                "dataSource": "learning.timestamps"},
    "asks": {"id": "asks", "label": "Asks", "component": "checklist",
             "dataSource": "learning.takeaways"},
}  # fmt: skip


def _idea(n: int, seconds: int) -> dict[str, Any]:
    return {"claim": f"claim {n}", "why": f"reason {n}", "timestamp": seconds, "section": f"c{n}"}


def _moment(n: int, seconds: int) -> dict[str, Any]:
    return {"seconds": seconds, "label": f"moment {n}", "description": f"shows {n}"}


def _request(
    learning: dict[str, list[Any]], tabs: tuple[str, ...] = ("ideas",)
) -> tuple[dict[str, Any], MergeRankRequest]:
    extraction = {"learning": learning}
    source = RenderInput(extraction, [_TABS[t] for t in tabs], _OUTLINE, _CHUNKS, _DURATION)
    request = build_request(source)
    assert request is not None
    return extraction, request


def _applied(
    extraction: dict[str, Any], request: MergeRankRequest, data: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    return apply_merge_rank(copy.deepcopy(extraction), read_answer(data, request))


def _ideas(n: int, step: int = 60, start: int = 0) -> list[dict[str, Any]]:
    return [_idea(k, start + step * k) for k in range(1, n + 1)]


def _claims(result: dict[str, Any], field: str = "keyIdeas") -> list[str]:
    return [item["claim"] if isinstance(item, dict) else item for item in result["learning"][field]]


# ─── Reading ───


class TestReadDuplicates:
    def test_should_drop_unknown_ids_when_the_answer_names_them(self) -> None:
        _, request = _request({"keyIdeas": _ideas(4)})

        groups = read_duplicates([{"keep": "K1", "drop": ["K9", "X1", "K2"]}], request.items)

        assert [g.drop for g in groups] == [("K2",)]

    def test_should_never_let_a_keep_drop_itself(self) -> None:
        _, request = _request({"keyIdeas": _ideas(4)})

        groups = read_duplicates([{"keep": "K1", "drop": ["K1"]}], request.items)

        assert groups == ()

    def test_should_leave_an_id_with_the_first_group_when_two_claim_it(self) -> None:
        _, request = _request({"keyIdeas": _ideas(4)})
        answer = [{"keep": "K1", "drop": ["K2"]}, {"keep": "K3", "drop": ["K2", "K4"]}]

        groups = read_duplicates(answer, request.items)

        assert [(g.keep, g.drop) for g in groups] == [("K1", ("K2",)), ("K3", ("K4",))]

    def test_should_skip_a_group_whose_keep_an_earlier_group_dropped(self) -> None:
        _, request = _request({"keyIdeas": _ideas(4)})
        answer = [{"keep": "K1", "drop": ["K2"]}, {"keep": "K2", "drop": ["K3"]}]

        groups = read_duplicates(answer, request.items)

        assert [g.keep for g in groups] == ["K1"]

    def test_should_not_let_a_later_group_drop_an_earlier_keep(self) -> None:
        _, request = _request({"keyIdeas": _ideas(4)})
        answer = [{"keep": "K1", "drop": ["K2"]}, {"keep": "K3", "drop": ["K1"]}]

        groups = read_duplicates(answer, request.items)

        assert [g.keep for g in groups] == ["K1"]


class TestReadSections:
    def test_should_take_the_answered_section_when_it_is_in_the_outline(self) -> None:
        _, request = _request({"keyIdeas": _ideas(2)})

        assert read_sections({"7": ["K1"]}, request)["K1"] == 6

    def test_should_fall_back_to_the_stamps_section_when_the_number_is_outside(self) -> None:
        _, request = _request({"keyIdeas": [_idea(1, 1000)]})

        assert read_sections({"40": ["K1"]}, request)["K1"] == 3

    def test_should_place_an_idea_the_answer_left_out_by_its_stamp(self) -> None:
        _, request = _request({"keyIdeas": [_idea(1, 3599)]})

        assert read_sections({}, request) == {"K1": 11}

    def test_should_place_no_idea_of_an_unsectioned_list(self) -> None:
        _, request = _request({"timestamps": [_moment(1, 60)]}, tabs=("moments",))

        assert read_sections({"1": ["M1"]}, request) == {}


class TestReadRank:
    def test_should_keep_only_the_ranked_lists_ids_once_each(self) -> None:
        learning = {"keyIdeas": _ideas(30), "timestamps": [_moment(1, 60)]}
        _, request = _request(learning, tabs=("ideas", "moments"))

        rank = read_rank(["K3", "M1", "K3", "K99", "K1"], request)

        assert rank == ("K3", "K1")


# ─── Duplicates ───


class TestDrops:
    def test_should_drop_the_named_twin_and_keep_its_keep(self) -> None:
        extraction, request = _request({"keyIdeas": _ideas(6)})

        result, _ = _applied(extraction, request, {"duplicates": [{"keep": "K1", "drop": ["K2"]}]})

        assert _claims(result) == ["claim 1", "claim 3", "claim 4", "claim 5", "claim 6"]

    def test_should_record_each_drop_with_its_head_and_keep(self) -> None:
        extraction, request = _request({"keyIdeas": _ideas(6)})

        _, record = _applied(extraction, request, {"duplicates": [{"keep": "K1", "drop": ["K2"]}]})

        assert record["dropped"] == [
            {"path": "learning.keyIdeas", "id": "K2", "head": "claim 2", "keep": "K1"}
        ]

    def test_should_hold_a_drop_past_forty_percent_of_its_list(self) -> None:
        extraction, request = _request({"keyIdeas": _ideas(5)})
        answer = {"duplicates": [{"keep": "K1", "drop": ["K2", "K3", "K4"]}]}

        _, record = _applied(extraction, request, answer)

        assert [h["guard"] for h in record["held"]] == ["share"]

    def test_should_hold_a_drop_below_the_density_gate_minimum(self) -> None:
        takeaways = ["ship small", "ship small, often", "measure first"]
        extraction, request = _request({"takeaways": takeaways}, tabs=("asks",))

        _, record = _applied(extraction, request, {"duplicates": [{"keep": "T1", "drop": ["T2"]}]})

        assert record["held"] == [{"path": "learning.takeaways", "id": "T2", "guard": "minimum"}]

    def test_should_hold_a_timeline_drop_below_the_band_minimum(self) -> None:
        moments = [_moment(n, 200 * n) for n in range(1, 14)]  # 13; 60 min → minimum 12
        extraction, request = _request({"timestamps": moments}, tabs=("moments",))
        answer = {"duplicates": [{"keep": "M1", "drop": ["M2", "M4"]}]}

        _, record = _applied(extraction, request, answer)

        assert [h["guard"] for h in record["held"]] == ["minimum"]

    def test_should_hold_a_drop_that_empties_the_last_ten_minutes(self) -> None:
        ideas = [*_ideas(4), _idea(5, 3300)]  # the last idea alone in the last 10 minutes
        extraction, request = _request({"keyIdeas": ideas})

        result, record = _applied(
            extraction, request, {"duplicates": [{"keep": "K1", "drop": ["K5"]}]}
        )

        assert (record["held"][0]["guard"], "claim 5" in _claims(result)) == ("window", True)

    def test_should_skip_an_id_whose_item_moved_since_the_call_saw_it(self) -> None:
        extraction, request = _request({"keyIdeas": _ideas(6)})
        moved = copy.deepcopy(extraction)
        moved["learning"]["keyIdeas"].insert(0, _idea(0, 5))

        result, record = apply_merge_rank(
            moved, read_answer({"duplicates": [{"keep": "K1", "drop": ["K2"]}]}, request)
        )

        assert (len(result["learning"]["keyIdeas"]), record["moved"]) == (7, 6)

    def test_should_drop_across_lists_when_a_moment_restates_an_idea(self) -> None:
        moments = [_moment(n, 200 * n) for n in range(1, 18)]
        learning = {"keyIdeas": _ideas(6), "timestamps": moments}
        extraction, request = _request(learning, tabs=("ideas", "moments"))

        result, _ = _applied(extraction, request, {"duplicates": [{"keep": "K1", "drop": ["M3"]}]})

        assert len(result["learning"]["timestamps"]) == 16


# ─── The answer left alone ───


class TestNoAnswer:
    def test_should_return_the_extraction_itself_when_the_call_failed(self) -> None:
        extraction, request = _request({"keyIdeas": _ideas(6)})

        result, record = apply_merge_rank(extraction, MergeRankAnswer("timeout", request, ms=25))

        assert (result is extraction, record) == (True, {"status": "timeout", "ms": 25})


# ─── Rank ───


class TestRankCut:
    """60 minutes → the key-ideas cap is 24."""

    def test_should_cut_a_ranked_list_to_its_cap(self) -> None:
        extraction, request = _request({"keyIdeas": _ideas(40, step=90)})
        rank = [f"K{n}" for n in range(1, 41)]

        result, record = _applied(extraction, request, {"rank": rank})

        assert (len(_claims(result)), record["ranked"]) == (
            24,
            [{"path": "learning.keyIdeas", "from": 40, "to": 24}],
        )

    def test_should_keep_the_top_third_whole(self) -> None:
        extraction, request = _request({"keyIdeas": _ideas(40, step=90)})
        rank = [f"K{n}" for n in range(40, 0, -1)]

        result, _ = _applied(extraction, request, {"rank": rank})

        assert {f"claim {n}" for n in range(27, 41)} <= set(_claims(result))

    def test_should_keep_an_item_of_the_last_ten_minutes_when_the_rank_leaves_them_out(
        self,
    ) -> None:
        extraction, request = _request({"keyIdeas": _ideas(40, step=90)})
        rank = [f"K{n}" for n in range(1, 31)]  # the last ten (2790 s on) unranked

        result, _ = _applied(extraction, request, {"rank": rank})

        stamps = [i["timestamp"] for i in result["learning"]["keyIdeas"]]
        assert any(s > _DURATION - 600 for s in stamps)

    def test_should_keep_a_twin_that_absorbed_a_drop_whatever_its_rank(self) -> None:
        extraction, request = _request({"keyIdeas": _ideas(40, step=90)})
        answer = {"duplicates": [{"keep": "K40", "drop": ["K39"]}], "rank": ["K1", "K2", "K3"]}

        result, _ = _applied(extraction, request, answer)

        assert "claim 40" in _claims(result)

    def test_should_leave_a_list_the_call_did_not_rank_to_assembly(self) -> None:
        extraction, request = _request({"keyIdeas": _ideas(40, step=90)})

        result, record = _applied(extraction, request, {"rank": []})

        assert (len(_claims(result)), record["ranked"]) == (40, [])

    def test_should_bring_back_an_emptied_windows_best_item_for_a_doubled_ones_worst(
        self,
    ) -> None:
        def window(index: int) -> int:
            return [0, 0, 1][index]

        kept = refill_windows([0, 1, 2], [0, 1], {0: 3, 1: 1, 2: 0}, window)

        assert kept == [0, 2]


# ─── Sections ───


class TestSections:
    def test_should_title_each_idea_with_its_outline_section(self) -> None:
        extraction, request = _request({"keyIdeas": [_idea(1, 100), _idea(2, 700)]})

        result, _ = _applied(extraction, request, {})

        assert [i["section"] for i in result["learning"]["keyIdeas"]] == ["Part 1", "Part 3"]

    def test_should_ship_at_most_eight_sections_when_ideas_span_twelve(self) -> None:
        ideas = [_idea(n, 300 * n + 10) for n in range(12)]
        extraction, request = _request({"keyIdeas": ideas})

        result, record = _applied(extraction, request, {})

        sections = {i["section"] for i in result["learning"]["keyIdeas"]}
        assert (len(sections), record["sectioned"]["sections"]) == (8, 8)

    def test_should_title_a_joined_section_after_its_largest_member(self) -> None:
        counts = Counter({n: 5 for n in range(9)} | {0: 3, 1: 1})

        titles = section_titles(counts, _OUTLINE)

        assert titles[1] == titles[0] == "Part 1"

    def test_should_join_the_smallest_section_to_its_smaller_neighbour(self) -> None:
        counts = Counter({n: 5 for n in range(9)} | {4: 1, 3: 2, 5: 4})

        titles = section_titles(counts, _OUTLINE)

        assert titles[4] == titles[3] == "Part 4"

    def test_should_drop_the_chunks_own_label_from_an_idea_with_no_place(self) -> None:
        unstamped = {"claim": "no stamp", "section": "c9"}
        extraction, request = _request({"keyIdeas": [_idea(1, 100), _idea(2, 700), unstamped]})

        result, _ = _applied(extraction, request, {})

        assert "section" not in result["learning"]["keyIdeas"][2]


def test_should_count_windows_back_from_the_videos_end() -> None:
    assert (window_of(3500, _DURATION), window_of(2999, _DURATION)) == (0, 1)
