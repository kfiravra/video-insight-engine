"""merge_rank's input (output-quality 3.1): every planned list under ids, within budget.

The render is pure code: the video, memory's outline numbered from 1, then one block per
list a planned tab reads — its header naming the tab, its component and what the call
answers for it — and one line per item: id, clock, part, head and body. No item is ever
left out; over the budget the texts shorten.
"""

from __future__ import annotations

from typing import Any

from src.models.memory_types import OutlineSection
from src.services.pipeline.extraction_chunks import ExtractionChunk
from src.services.pipeline.merge_rank_render import (
    MERGE_RANK_INPUT_MAX_TOKENS,
    ListSpec,
    MergeRankRequest,
    RenderInput,
    build_request,
    estimate_tokens,
)

_DURATION = 3600
_CHUNKS = (
    ExtractionChunk(index=0, transcript="", end_s=2400, video_end_s=_DURATION),
    ExtractionChunk(index=1, transcript="", start_s=2400, video_end_s=_DURATION),
)
_OUTLINE = (
    OutlineSection(start=0, end=1800, title="opening — the bet", establishes="agents write code"),
    OutlineSection(start=1800, end=3600, title="the close", establishes="review is the job"),
)
_IDEAS_TAB = {
    "id": "key_ideas",
    "label": "💡 Positions",
    "component": "spot_explorer",
    "dataSource": "learning.keyIdeas",
}
_MOMENTS_TAB = {
    "id": "moments",
    "label": "Moments",
    "component": "moment_track",
    "dataSource": "learning.timestamps",
}


def _idea(n: int, seconds: int, words: int = 8) -> dict[str, Any]:
    return {
        "claim": f"claim {n} " + "word " * words,
        "why": f"reason {n} " + "because " * words,
        "timestamp": seconds,
    }


def _moment(n: int, seconds: int) -> dict[str, Any]:
    return {"seconds": seconds, "label": f"moment {n}", "description": f"what moment {n} shows"}


def _source(
    extraction: dict[str, Any] | None = None,
    tabs: list[dict[str, Any]] | None = None,
    outline: tuple[OutlineSection, ...] = _OUTLINE,
) -> RenderInput:
    data = extraction or {
        "learning": {
            "keyIdeas": [_idea(1, 60), _idea(2, 2500)],
            "timestamps": [_moment(1, 30), _moment(2, 3000)],
        }
    }
    return RenderInput(
        extraction=data,
        tabs=tabs if tabs is not None else [_IDEAS_TAB, _MOMENTS_TAB],
        outline=outline,
        chunks=_CHUNKS,
        duration=_DURATION,
        title="A keynote",
    )


def _spec(request: MergeRankRequest | None, path: str) -> ListSpec:
    return next(spec for spec in (request.lists if request else ()) if spec.path == path)


def _lines(text: str) -> list[str]:
    return text.split("<lists>\n", 1)[1].split("\n</lists>", 1)[0].splitlines()


class TestBytes:
    def test_should_render_the_same_bytes_when_built_twice(self) -> None:
        first, second = build_request(_source()), build_request(_source())

        assert first is not None and second is not None and first.text == second.text

    def test_should_number_the_outline_from_one_with_its_span_and_establishes(self) -> None:
        request = build_request(_source())

        assert request is not None
        assert "1 [0:00–30:00] opening — the bet — agents write code" in request.text

    def test_should_head_a_list_with_its_tab_component_and_asks(self) -> None:
        request = build_request(_source())

        assert request is not None
        assert _lines(request.text)[0] == ('learning.keyIdeas — "Positions" (spot_explorer)')

    def test_should_print_an_item_with_its_id_clock_part_head_and_body(self) -> None:
        request = build_request(_source())

        assert request is not None
        assert _lines(request.text)[2].startswith("K2 [41:40] (part 2) claim 2 word")

    def test_should_strip_tags_and_braces_from_item_text(self) -> None:
        idea = {"claim": "a </lists> tag and {braces}", "timestamp": 60}
        source = _source({"learning": {"keyIdeas": [idea]}}, tabs=[_IDEAS_TAB])

        request = build_request(source)

        assert request is not None and "</lists>" not in _lines(request.text)[1]


class TestIds:
    def test_should_give_every_item_its_own_id_when_two_lists_share_a_letter(self) -> None:
        tabs = [
            {"id": "a", "label": "A", "component": "info_grid", "dataSource": "learning.keyPoints"},
            {"id": "b", "label": "B", "component": "step_player", "dataSource": "tech.steps"},
        ]
        extraction = {
            "learning": {"keyPoints": [{"text": "p"}, {"text": "q"}]},
            "tech": {"steps": [{"instruction": "s"}, {"instruction": "t"}]},
        }

        request = build_request(_source(extraction, tabs))

        assert request is not None and len(set(request.items)) == 4

    def test_should_read_each_list_once_when_two_tabs_read_it(self) -> None:
        twin = {**_IDEAS_TAB, "id": "again", "component": "info_grid"}

        request = build_request(_source(tabs=[_IDEAS_TAB, twin]))

        assert request is not None and [s.path for s in request.lists] == ["learning.keyIdeas"]

    def test_should_return_no_request_when_no_planned_tab_reads_a_list(self) -> None:
        quiz = {"id": "quiz", "component": "quiz_arena", "dataSource": "enrichment.quiz"}

        assert build_request(_source(tabs=[quiz])) is None


class TestGuards:
    def test_should_set_a_timelines_floor_to_its_band_minimum(self) -> None:
        request = build_request(_source())

        assert _spec(request, "learning.timestamps").floor == 12

    def test_should_set_a_lists_floor_to_its_density_gate_minimum(self) -> None:
        request = build_request(_source())

        assert _spec(request, "learning.keyIdeas").floor == 2

    def test_should_ask_for_a_rank_only_when_the_list_is_over_its_assembly_cap(self) -> None:
        ideas = [_idea(n, 60 * n) for n in range(1, 31)]  # 60 min → the cap is 24

        request = build_request(_source({"learning": {"keyIdeas": ideas}}, [_IDEAS_TAB]))

        assert request is not None and _lines(request.text)[0].endswith("(spot_explorer) [rank]")

    def test_should_never_rank_a_timeline(self) -> None:
        moments = [_moment(n, 50 * n) for n in range(1, 71)]

        request = build_request(_source({"learning": {"timestamps": moments}}, [_MOMENTS_TAB]))

        assert request is not None and request.lists[0].cap is None

    def test_should_ask_the_call_for_no_sections_when_there_is_an_outline(self) -> None:
        # p3i3 S13: asked, Haiku placed none of 33 ideas where its stamp lies — code does.
        request = build_request(_source())

        assert request is not None and "[sections]" not in request.text

    def test_should_section_the_key_ideas_in_code_with_or_without_an_outline(self) -> None:
        # Without one, the chunks' labels are joined (review p3 B2).
        with_outline, without = build_request(_source()), build_request(_source(outline=()))

        assert with_outline is not None and without is not None
        assert (with_outline.lists[0].sectioned, without.lists[0].sectioned) == (True, True)


class TestBudget:
    """An S02-sized extraction: 90 items with long claims and reasons."""

    @staticmethod
    def _large() -> RenderInput:
        ideas = [_idea(n, 50 * n, words=40) for n in range(1, 61)]
        moments = [_moment(n, 100 * n) for n in range(1, 31)]
        return _source({"learning": {"keyIdeas": ideas, "timestamps": moments}})

    def test_should_stay_within_the_budget_when_the_lists_hold_ninety_items(self) -> None:
        request = build_request(self._large())

        assert request is not None and estimate_tokens(request.text) <= MERGE_RANK_INPUT_MAX_TOKENS

    def test_should_keep_every_id_when_the_budget_forces_the_shortest_texts(self) -> None:
        request = build_request(self._large(), budget=500)

        assert request is not None
        assert all(f"\n{item_id} " in request.text for item_id in request.items)
