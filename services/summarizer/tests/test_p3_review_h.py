"""Phase-3 review fixes, area B — merge_rank and the hero (output-quality 3.1 / 3.2 / 3.0).

merge_rank: a twin goes only while its keep is sure to ship — a keep that assembly's caps
could still sample out (a timeline over its §9 cap, a list left to the richness cut) holds
its group (B1); a list that ships whole is never rank-cut (B5); the key-idea sections are
code's, so a call that does not answer still sections them, and without an outline the
chunks' labels are joined to eight (B2). The hero: 5 K unless a chunked run's parts show
(B3), a part's window is no evidence (B6), the worthItFor example claims nothing about
watching (B4). A failed run cancels the started merge_rank call (B8). No LLM: the call is
faked at the retry wrapper.
"""

from __future__ import annotations

import asyncio
import copy
import json
import re
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

from src.models.memory_types import OutlineSection
from src.services.llm_model_rules import LLMRefusalError
from src.services.pipeline import merge_rank
from src.services.pipeline.extraction_chunks import ExtractionChunk
from src.services.pipeline.group_merge import ScalarPart
from src.services.pipeline.hero_compact import (
    HERO_EXTRACTION_MAX_TOKENS,
    HERO_EXTRACTION_PARTED_MAX_TOKENS,
    compact_for_hero,
    estimate_tokens,
)
from src.services.pipeline.hero_validate import HeroEvidence
from src.services.pipeline.merge_rank import read_answer
from src.services.pipeline.merge_rank_apply import KEEP_CUT, MergeRankAnswer, apply_merge_rank
from src.services.pipeline.merge_rank_render import MergeRankRequest, RenderInput, build_request
from src.services.pipeline.phases import metadata
from src.services.pipeline.prompt_builder import PROMPTS_DIR

_HOUR = 3600
# Twelve five-minute sections.
_OUTLINE = tuple(
    OutlineSection(start=300 * n, end=300 * (n + 1), title=f"part {n + 1}") for n in range(12)
)
_TABS = {
    "ideas": {"id": "ideas", "label": "Positions", "component": "spot_explorer",
              "dataSource": "learning.keyIdeas"},
    "banner": {"id": "banner", "label": "Numbers", "component": "stat_banner",
               "dataSource": "learning.keyIdeas"},
    "moments": {"id": "moments", "label": "Moments", "component": "moment_track",
                "dataSource": "learning.timestamps"},
    "steps": {"id": "steps", "label": "Steps", "component": "step_player",
              "dataSource": "learning.keyPoints"},
}  # fmt: skip


def _idea(n: int, seconds: int, section: str | None = None) -> dict[str, Any]:
    return {"claim": f"claim {n}", "why": f"reason {n}", "timestamp": seconds,
            "section": section or f"c{n}"}  # fmt: skip


def _moment(n: int, seconds: int) -> dict[str, Any]:
    return {"seconds": seconds, "label": f"moment {n}", "description": f"shows {n}"}


def _request(
    learning: dict[str, list[Any]],
    tabs: tuple[str, ...] = ("ideas",),
    duration: int = _HOUR,
    outline: tuple[OutlineSection, ...] = _OUTLINE,
) -> tuple[dict[str, Any], MergeRankRequest]:
    extraction = {"learning": learning}
    chunks = (
        ExtractionChunk(index=0, transcript="", end_s=duration // 2, video_end_s=duration),
        ExtractionChunk(index=1, transcript="", start_s=duration // 2, video_end_s=duration),
    )
    source = RenderInput(extraction, [_TABS[t] for t in tabs], outline, chunks, duration)
    request = build_request(source)
    assert request is not None
    return extraction, request


def _applied(
    extraction: dict[str, Any], request: MergeRankRequest, data: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    return apply_merge_rank(copy.deepcopy(extraction), read_answer(data, request))


def _claims(result: dict[str, Any]) -> list[str]:
    return [idea["claim"] for idea in result["learning"]["keyIdeas"]]


def _labels(result: dict[str, Any]) -> list[str]:
    return [moment["label"] for moment in result["learning"]["timestamps"]]


def _ideas(n: int, step: int = 60) -> list[dict[str, Any]]:
    return [_idea(k, step * k) for k in range(1, n + 1)]


def _moments(n: int, step: int = 150) -> list[dict[str, Any]]:
    return [_moment(k, step * k) for k in range(1, n + 1)]


# ─── B1: a twin goes only while its keep is sure to ship ───


class TestKeepShips:
    """60 minutes: a label-only timeline ships 12 moments, the key ideas 24."""

    def test_should_hold_a_twin_when_its_keep_sits_in_a_timeline_over_its_cap(self) -> None:
        learning = {"keyIdeas": _ideas(6), "timestamps": _moments(20)}
        extraction, request = _request(learning, tabs=("ideas", "moments"))

        result, _ = _applied(extraction, request, {"duplicates": [{"keep": "M1", "drop": ["K1"]}]})

        assert "claim 1" in _claims(result)

    def test_should_record_the_held_twin_under_the_keep_guard(self) -> None:
        learning = {"keyIdeas": _ideas(6), "timestamps": _moments(20)}
        extraction, request = _request(learning, tabs=("ideas", "moments"))

        _, record = _applied(extraction, request, {"duplicates": [{"keep": "M1", "drop": ["K1"]}]})

        assert record["held"] == [{"path": "learning.keyIdeas", "id": "K1", "guard": KEEP_CUT}]

    def test_should_drop_a_twin_when_its_keeps_timeline_fits_its_cap(self) -> None:
        learning = {"keyIdeas": _ideas(6), "timestamps": _moments(12)}
        extraction, request = _request(learning, tabs=("ideas", "moments"))

        result, _ = _applied(extraction, request, {"duplicates": [{"keep": "M1", "drop": ["K1"]}]})

        assert "claim 1" not in _claims(result)

    def test_should_hold_a_twin_when_its_keep_is_left_to_assemblys_richness_cut(self) -> None:
        learning = {"keyIdeas": _ideas(40, step=90), "timestamps": _moments(17)}
        extraction, request = _request(learning, tabs=("ideas", "moments"))
        answer = {"duplicates": [{"keep": "K40", "drop": ["M3"]}], "rank": []}

        result, _ = _applied(extraction, request, answer)

        assert "moment 3" in _labels(result)

    def test_should_drop_a_twin_when_its_keep_sits_in_a_list_cut_by_rank(self) -> None:
        learning = {"keyIdeas": _ideas(40, step=90), "timestamps": _moments(17)}
        extraction, request = _request(learning, tabs=("ideas", "moments"))
        answer = {"duplicates": [{"keep": "K40", "drop": ["M3"]}], "rank": ["K1", "K2", "K3"]}

        result, _ = _applied(extraction, request, answer)

        assert ("moment 3" in _labels(result), "claim 40" in _claims(result)) == (False, True)

    def test_should_hold_a_twin_when_its_ranked_list_has_no_room_for_one_more_keep(self) -> None:
        # Ten minutes: the key ideas ship 10, so the eleventh kept twin would be cut.
        learning = {"keyIdeas": _ideas(12, step=45), "timestamps": _moments(30, step=19)}
        extraction, request = _request(learning, tabs=("ideas", "moments"), duration=600)
        groups = [{"keep": f"K{n}", "drop": [f"M{n}"]} for n in range(1, 12)]
        answer = {"duplicates": groups, "rank": [f"K{n}" for n in range(1, 13)]}

        _, record = _applied(extraction, request, answer)

        assert [row["id"] for row in record["held"]] == ["M11"]

    def test_should_keep_a_key_idea_in_the_prompts_example_when_a_group_spans_lists(self) -> None:
        text = (PROMPTS_DIR / "merge_rank.txt").read_text(encoding="utf-8")
        example = json.loads(re.search(r'^\{"duplicates".*\}$', text, re.MULTILINE).group())  # type: ignore[union-attr]

        assert example["duplicates"][0]["keep"].startswith("K")


# ─── B5: a list assembly ships whole is never rank-cut ───


class TestShipsWhole:
    def test_should_leave_key_ideas_whole_when_their_tab_is_a_stat_banner(self) -> None:
        extraction, request = _request({"keyIdeas": _ideas(40, step=90)}, tabs=("banner",))

        result, _ = _applied(extraction, request, {"rank": [f"K{n}" for n in range(1, 41)]})

        assert len(_claims(result)) == 40

    def test_should_ask_for_no_rank_when_the_list_is_a_procedures_steps(self) -> None:
        points = [{"text": f"step {n}", "timestamp": 60 * n} for n in range(1, 31)]

        _, request = _request({"keyPoints": points}, tabs=("steps",))

        assert request.lists[0].cap is None


# ─── B2: the sections are code's, answered or not ───


class TestSectionsWithoutAnAnswer:
    def test_should_title_each_idea_with_its_outline_section_when_the_call_timed_out(
        self,
    ) -> None:
        extraction, request = _request({"keyIdeas": [_idea(1, 100), _idea(2, 700)]})

        result, _ = apply_merge_rank(extraction, MergeRankAnswer("timeout", request))

        assert [i["section"] for i in result["learning"]["keyIdeas"]] == ["Part 1", "Part 3"]

    def test_should_record_the_sections_when_the_call_was_refused(self) -> None:
        extraction, request = _request({"keyIdeas": [_idea(1, 100), _idea(2, 700)]})

        _, record = apply_merge_rank(extraction, MergeRankAnswer("refused", request, ms=40))

        assert record == {"status": "refused", "ms": 40, "sectioned": {"items": 2, "sections": 2}}

    async def test_should_section_the_key_ideas_when_the_call_times_out(self) -> None:
        extraction, request = _request({"keyIdeas": [_idea(1, 100), _idea(2, 700)]})
        llm = SimpleNamespace(fast_model="fast")

        with patch.object(merge_rank, "call_llm_with_retry", AsyncMock(side_effect=TimeoutError)):
            answer = await merge_rank.run_merge_rank(llm, request)  # type: ignore[arg-type]
        result, _ = apply_merge_rank(extraction, answer)

        assert [i["section"] for i in result["learning"]["keyIdeas"]] == ["Part 1", "Part 3"]

    async def test_should_section_the_key_ideas_when_the_call_is_refused(self) -> None:
        extraction, request = _request({"keyIdeas": [_idea(1, 100), _idea(2, 700)]})
        refusal = AsyncMock(side_effect=LLMRefusalError("haiku", "cyber"))

        with patch.object(merge_rank, "call_llm_with_retry", refusal):
            answer = await merge_rank.run_merge_rank(SimpleNamespace(fast_model="f"), request)  # type: ignore[arg-type]
        result, _ = apply_merge_rank(extraction, answer)

        assert result["learning"]["keyIdeas"][1]["section"] == "Part 3"


class TestSectionsWithoutAnOutline:
    def test_should_join_the_chunks_labels_to_eight_when_there_is_no_outline(self) -> None:
        extraction, request = _request({"keyIdeas": _ideas(24)}, outline=())

        result, _ = _applied(extraction, request, {})

        assert len({i["section"] for i in result["learning"]["keyIdeas"]}) == 8

    def test_should_keep_the_chunks_labels_when_eight_or_fewer_and_no_outline(self) -> None:
        ideas = [_idea(n, 60 * n, section="Opening" if n < 4 else "Close") for n in range(1, 7)]
        extraction, request = _request({"keyIdeas": ideas}, outline=())

        result, _ = _applied(extraction, request, {})

        assert [i["section"] for i in result["learning"]["keyIdeas"]] == [
            "Opening", "Opening", "Opening", "Close", "Close", "Close"
        ]  # fmt: skip

    def test_should_title_a_joined_label_after_its_largest_member_when_no_outline(self) -> None:
        # Nine labels in list order; "c1" (one idea) joins "big" (three), its smaller neighbour.
        names = ["c1", "big", "big", "big", *(f"c{n}" for n in range(2, 9)), "c8"]
        ideas = [_idea(k, 60 * k, section=name) for k, name in enumerate(names, start=1)]
        extraction, request = _request({"keyIdeas": ideas}, outline=())

        result, _ = _applied(extraction, request, {})

        assert result["learning"]["keyIdeas"][0]["section"] == "big"


# ─── B3: 5 K unless a chunked run's parts show ───

# 28 ideas whose full-detail compact is ≈ 5.4 K tokens: within 5.5 K, over 5 K.
_NEAR_5K = {
    "learning": {
        "summary": "The opening half.",
        "keyIdeas": [
            {"claim": f"Claim {n} " + "word " * 30, "why": f"reason {n} " + "because " * 30,
             "quote": f"said {n} " + "quoted " * 30, "timestamp": 40 * n}
            for n in range(1, 29)
        ],
    }
}  # fmt: skip
_PARTS = {
    "learning.summary": (
        ScalarPart("0:00–39:21", "The opening half."),
        ScalarPart("39:21–1:03:07", "The closing half."),
    )
}


class TestHeroBudget:
    def test_should_keep_the_5k_budget_when_there_are_no_parts(self) -> None:
        compact = compact_for_hero(_NEAR_5K, _HOUR)

        assert estimate_tokens(compact.text) <= HERO_EXTRACTION_MAX_TOKENS

    def test_should_keep_full_detail_within_5_5k_when_a_chunked_run_shows_parts(self) -> None:
        compact = compact_for_hero(_NEAR_5K, _HOUR, chunk_scalars=_PARTS)

        tokens = estimate_tokens(compact.text)
        assert HERO_EXTRACTION_MAX_TOKENS < tokens <= HERO_EXTRACTION_PARTED_MAX_TOKENS


# ─── B6: a part's window is no evidence ───


def _evidence(text: str) -> HeroEvidence:
    return HeroEvidence(text=text, stamps={}, duration=_HOUR, tab_labels=(), takeaway_count=5)


class TestPartWindows:
    def test_should_not_count_a_chunk_boundary_as_a_number_when_parts_show(self) -> None:
        compact = compact_for_hero({"learning": {"summary": "x"}}, _HOUR, chunk_scalars=_PARTS)

        assert {"39", "21"}.isdisjoint(_evidence(compact.evidence).numbers)

    def test_should_keep_only_the_parts_own_numbers_when_a_window_is_open_ended(self) -> None:
        parts = {"learning.summary": (ScalarPart("0:00–39:21", "a"), ScalarPart("39:21–end", "7"))}

        compact = compact_for_hero({"learning": {"summary": "a"}}, _HOUR, chunk_scalars=parts)

        assert _evidence(compact.evidence).numbers == {"7"}


# ─── B4: the worthItFor example ───


def test_should_claim_nothing_about_watching_in_the_worth_it_for_example() -> None:
    text = (PROMPTS_DIR / "hero_rewrite.txt").read_text(encoding="utf-8")
    line = next(row for row in text.splitlines() if row.startswith("- worthItFor:"))
    example = line.split("From another video:", 1)[1]

    assert re.search(r"camera|shown|watch|screen", example, re.IGNORECASE) is None


# ─── B8: a failed run stops the started call ───


async def test_should_cancel_the_split_paths_started_merge_rank_call() -> None:
    call = asyncio.create_task(asyncio.sleep(60))
    ctx = SimpleNamespace(
        caption_task=None, description_task=None, quiz_task=None, hero_task=None,
        merge_rank_task=call,
    )  # fmt: skip

    metadata.release_background_work(ctx)  # type: ignore[arg-type]
    await asyncio.gather(call, return_exceptions=True)

    assert call.cancelled()
