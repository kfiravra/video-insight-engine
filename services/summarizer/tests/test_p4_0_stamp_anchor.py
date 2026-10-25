"""4.0 (G3-3) — a chunk call's stamps anchored to their verified quote and their list's order.

S15 (1XSmEfCe9-s, 71:22, auto ``iw-orig``) is the evidence: chunk 2 (window 40:02–1:11:22)
stamped key idea "New bitcoins are created by the network…" at 56:20 (3380) while its own
Hebrew quote is said at 43:16 (2596), and concepts "Bitcoin mining" (3200) and "Fixed
supply" (3821) — both explained at 43:01–44:01 — in their right list places. The window
check passed all three (they lie inside the window). ``anchor_stamps`` moves a stamp to
where its VERIFIED quote is said inside the window (a range only when the quote falls
outside its span) and pulls an unquoted stamp more than 300 s out of its chronological
list's order back to its earlier in-order neighbour, or clears it when the neighbours are
more than 300 s apart. A one-chunk run (open window) is returned untouched. The cues
below are S15's own (``data/S15/segments.json``, 43:00–44:05), the list shapes chunk 2's
calls returned (``data/S15/trace.json``).
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from llm_common.context import llm_feature_var

from src.config import settings
from src.models.pipeline_types import PlanResult
from src.services.pipeline import extraction_groups
from src.services.pipeline.context import PipelineContext
from src.services.pipeline.extraction_chunks import ExtractionChunk, StampReport
from src.services.pipeline.extraction_group_prompt import GroupJob
from src.services.pipeline.extraction_groups import (
    GroupCall,
    GroupOutcome,
    SplitRun,
    run_group_call,
)
from src.services.pipeline.field_groups import ExtractionGroup
from src.services.pipeline.field_requests import FieldRequest
from src.services.pipeline.phases import group_emission
from src.services.pipeline.phases.group_emission import GroupLanding
from src.services.pipeline.pipeline_helpers import PipelineTimer
from src.services.pipeline.quote_verify import QuoteIndex
from src.services.pipeline.stamp_anchor import anchor_stamps

# ─── S15 chunk 2 (data/S15) ───

_CHUNK_2 = ExtractionChunk(1, "t", start_s=2402.0, end_s=None, video_end_s=4282.0)
_CHUNK_1 = ExtractionChunk(0, "t", start_s=None, end_s=2402.0, video_end_s=4282.0)
_OPEN = ExtractionChunk(0, "whole video")

_HEBREW_CUES = [
    (2581.920, "עם זאת אין גוף מרכזי ששולט על הכסף כי עם"),
    (2585.680, "רשת של המוני מחשבים שכל אחד מהם פועל"),
    (2588.839, "בנפרד התשובה היא שביטוי חדשים נוצרים"),
    (2592.680, "באופן אקראי וספונטני לחלוטין ברחבי הרשת"),
    (2596.760, "אתם יכולים לקרוא לזה כסף שנופל מהשמיים"),
    (2600.319, "כאמור כל המחשבים החברים ברשת מבצעים כל"),
    (2641.119, "הכולל של ביטקוין ברשת יהיה 21 מיליון ולא"),
    (2645.240, "ביטקוין אחד יותר זו הסיבה לכך שביטוי"),
    (2649.800, "חסין מפני אינפלציה מספר המטבעות מוגבל אף"),
]
_SKY = "אתם יכולים לקרוא לזה כסף שנופל מהשמיים"  # said at 2596
_CAPPED = "זו הסיבה לכך שביטוי חסין מפני אינפלציה מספר המטבעות מוגבל"  # said at 2645

_ENGLISH_CUES = [
    (1000.0, "we started the whole project with a single spreadsheet"),
    (2700.0, "the market always rewards patience over speed"),
    (2740.0, "and that is why we kept the team small"),
]
_PATIENCE = "the market always rewards patience over speed"  # said at 2700
_SPREADSHEET = "we started the whole project with a single spreadsheet"  # said at 1000

# text2_c1 concepts as the call stamped them (none carries a quote).
_S15_CONCEPTS = [2402, 2440, 2501, 3200, 3821, 2680, 2701, 2701, 2804, 3323, 3220, 3423]


def _index(cues: list[tuple[float, str]], language: str) -> QuoteIndex:
    return QuoteIndex.build([{"text": t, "startMs": int(s * 1000)} for s, t in cues], language)


@pytest.fixture(scope="module")
def hebrew() -> QuoteIndex:
    return _index(_HEBREW_CUES, "he")


@pytest.fixture(scope="module")
def english() -> QuoteIndex:
    return _index(_ENGLISH_CUES, "en")


def _idea(stamp: int, quote: str | None = None) -> dict[str, Any]:
    return {"claim": f"idea at {stamp}", "why": "because", "quote": quote, "timestamp": stamp}


def _ideas(*ideas: dict[str, Any]) -> dict[str, Any]:
    return {"learning": {"keyIdeas": list(ideas)}}


def _stamps(data: dict[str, Any], domain: str, field: str, key: str = "timestamp") -> list:
    return [item.get(key) for item in data[domain][field]]


def _anchored(
    data: dict[str, Any], index: QuoteIndex | None, chunk: ExtractionChunk = _CHUNK_2
) -> dict[str, Any]:
    return anchor_stamps(data, chunk, index, StampReport())


# ─── 1. The quote anchor ───


class TestQuoteAnchor:
    def test_should_move_a_key_idea_to_its_verified_quote_when_it_is_said_inside_the_window(
        self, english: QuoteIndex
    ) -> None:
        anchored = _anchored(_ideas(_idea(3300, _PATIENCE)), english)

        assert _stamps(anchored, "learning", "keyIdeas") == [2700]

    def test_should_move_a_hebrew_key_idea_to_its_hebrew_quote_when_the_transcript_is_hebrew(
        self, hebrew: QuoteIndex
    ) -> None:
        data = _ideas(_idea(2522), _idea(3380, _SKY), _idea(2641, _CAPPED))

        anchored = _anchored(data, hebrew)

        assert _stamps(anchored, "learning", "keyIdeas") == [2522, 2596, 2641]

    def test_should_keep_the_stamp_when_the_quote_is_translated(self, hebrew: QuoteIndex) -> None:
        data = _ideas(_idea(3380, "You can call it money falling from the sky"))

        assert _anchored(data, hebrew) is data

    def test_should_keep_the_stamp_when_the_verified_quote_is_within_30_seconds_of_it(
        self, hebrew: QuoteIndex
    ) -> None:
        data = _ideas(_idea(2581, _SKY))

        assert _anchored(data, hebrew) is data

    def test_should_keep_the_stamp_when_the_verified_quote_lies_outside_the_chunk_window(
        self, english: QuoteIndex
    ) -> None:
        data = _ideas(_idea(3300, _SPREADSHEET))

        assert _anchored(data, english) is data

    def test_should_anchor_a_quoted_point_reached_as_a_value_when_it_is_said_elsewhere(
        self, english: QuoteIndex
    ) -> None:
        data = {"review": {"verdict": {"bottomLine": "buy", "quote": _PATIENCE, "timestamp": 3900}}}

        anchored = _anchored(data, english)

        assert anchored["review"]["verdict"]["timestamp"] == 2700


class TestRanges:
    def test_should_keep_a_moments_stamp_when_its_quote_is_said_inside_its_span(
        self, hebrew: QuoteIndex
    ) -> None:
        moments = [
            {"time": "43:01", "seconds": 2581, "label": "a", "description": "a", "quote": _SKY},
            {"time": "43:40", "seconds": 2620, "label": "b", "description": "b"},
        ]
        data = {"learning": {"timestamps": moments}}

        assert _anchored(data, hebrew) is data

    def test_should_move_a_moment_to_its_quote_and_rewrite_its_time_when_said_outside_its_span(
        self, hebrew: QuoteIndex
    ) -> None:
        moments = [
            {"time": "56:20", "seconds": 3380, "label": "a", "description": "a", "quote": _SKY},
            {"time": "58:20", "seconds": 3500, "label": "b", "description": "b"},
        ]

        anchored = _anchored({"learning": {"timestamps": moments}}, hebrew)

        moved = anchored["learning"]["timestamps"][0]
        assert (moved["seconds"], moved["time"]) == (2596, "43:16")

    def test_should_clear_a_segments_end_before_its_new_start_when_its_quote_moves_it(
        self, hebrew: QuoteIndex
    ) -> None:
        segment = {"title": "coins", "summary": "s", "timestamp": 2420, "endSeconds": 2500}
        data = {"podcast": {"segments": [{**segment, "quote": _SKY}]}}

        anchored = _anchored(data, hebrew)

        assert anchored["podcast"]["segments"][0] == {
            **segment,
            "quote": _SKY,
            "timestamp": 2596,
            "endSeconds": None,
        }


# ─── 2. The order guard ───


class TestOrderGuard:
    def test_should_pull_a_concept_back_to_its_earlier_neighbour_when_300_s_past_its_order(
        self,
    ) -> None:
        concepts = [{"name": f"c{n}", "timestamp": s} for n, s in enumerate(_S15_CONCEPTS)]

        anchored = _anchored({"learning": {"concepts": concepts}}, None)

        expected = [2501 if s in (3200, 3821) else s for s in _S15_CONCEPTS]
        assert _stamps(anchored, "learning", "concepts") == expected

    def test_should_clear_an_out_of_order_stamp_when_its_neighbours_are_over_300_s_apart(
        self,
    ) -> None:
        concepts = [{"name": f"c{s}", "timestamp": s} for s in (2500, 4200, 2900, 3200)]

        anchored = _anchored({"learning": {"concepts": concepts}}, None)

        assert _stamps(anchored, "learning", "concepts") == [2500, None, 2900, 3200]

    def test_should_drop_a_moment_whose_out_of_order_stamp_is_cleared(self) -> None:
        moments = [
            {"seconds": s, "label": f"m{s}", "description": "d"} for s in (2500, 4200, 2900, 3200)
        ]

        anchored = _anchored({"learning": {"timestamps": moments}}, None)

        assert _stamps(anchored, "learning", "timestamps", "seconds") == [2500, 2900, 3200]

    def test_should_leave_a_rankings_list_alone_when_its_stamps_are_out_of_order(self) -> None:
        rankings = [
            {"name": f"r{s}", "tier": "S", "timestamp": s} for s in (3900, 2500, 2600, 2700)
        ]
        data = {"gaming": {"rankings": rankings}}

        assert _anchored(data, None) is data

    def test_should_return_the_same_object_when_the_chunk_window_is_open(
        self, english: QuoteIndex
    ) -> None:
        concepts = [{"name": f"c{n}", "timestamp": s} for n, s in enumerate(_S15_CONCEPTS)]
        data = {"learning": {"keyIdeas": [_idea(3300, _PATIENCE)], "concepts": concepts}}

        assert _anchored(data, english, _OPEN) is data

    def test_should_pull_a_first_item_to_the_windows_start_when_no_stamp_precedes_it(self) -> None:
        concepts = [{"name": f"c{s}", "timestamp": s} for s in (1900, 100, 200, 300)]

        anchored = _anchored({"learning": {"concepts": concepts}}, None, _CHUNK_1)

        assert _stamps(anchored, "learning", "concepts") == [0, 100, 200, 300]


# ─── Through the chunk's call → outcome → the readers ───

_JOB = GroupJob([], ["learning"], "learning")
_IDEAS = FieldRequest("learning", "keyIdeas", "list", "planned", ("ideas",), 30, 5, 40, False)
_CONCEPTS = FieldRequest("learning", "concepts", "list", "planned", ("ideas",), 30, 5, 40, False)
_TEXT1 = ExtractionGroup("text1", (_IDEAS, _CONCEPTS), False, 200, ("ideas",))
_TABS = [
    {"id": "ideas", "label": "Key Ideas", "component": "spot_explorer",
     "dataSource": "learning.keyIdeas"},
]  # fmt: skip
_CHUNK_2_ANSWER = {
    "learning": {
        "keyIdeas": [_idea(2522), _idea(3380, _SKY), _idea(2641, _CAPPED)],
        "concepts": [{"name": f"c{n}", "timestamp": s} for n, s in enumerate(_S15_CONCEPTS)],
    }
}


def _run(index: QuoteIndex) -> SplitRun:
    return SplitRun(
        llm_service=MagicMock(),
        prompt=MagicMock(),
        job=_JOB,
        chunks=(_CHUNK_1, _CHUNK_2),
        text_groups=(_TEXT1,),
        content_tags=("learning",),
        modifiers=(),
        quote_index=index,
    )


@pytest.fixture
def llm() -> Iterator[AsyncMock]:
    async def answer(*_args: Any, **_kwargs: Any) -> str:
        llm_feature_var.get()
        return json.dumps(_CHUNK_2_ANSWER)

    blocks = [{"type": "text", "text": block} for block in ("rules", "head", "job")]
    mock = AsyncMock(side_effect=answer)
    with (
        patch.object(extraction_groups, "call_llm_with_retry", mock),
        patch.object(extraction_groups, "build_group_messages", return_value=blocks),
    ):
        yield mock


async def _chunk_2_outcome(index: QuoteIndex) -> GroupOutcome:
    call = GroupCall(_TEXT1, _CHUNK_2, _JOB)
    return await asyncio.create_task(run_group_call(call, _run(index)))


class TestThroughTheCall:
    async def test_should_count_anchored_and_reordered_stamps_per_field_path_in_the_report(
        self, llm: AsyncMock, hebrew: QuoteIndex
    ) -> None:
        outcome = await _chunk_2_outcome(hebrew)

        dropped = outcome.validation.get("dropped") or {}
        moves = (
            dropped["learning.keyIdeas"]["anchored"],
            dropped["learning.concepts"]["reordered"],
        )
        assert moves == (1, 2)

    async def test_should_start_quiz_hero_and_merge_rank_over_anchored_stamps_when_a_call_lands(
        self, llm: AsyncMock, hebrew: QuoteIndex, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(settings, "HERO_REWRITE_ENABLED", True)
        monkeypatch.setattr(settings, "MERGE_RANK_ENABLED", True)
        first = GroupOutcome(_TEXT1, 0, {"learning": {"keyIdeas": [_idea(600)]}}, {}, "ok", 1, 0)
        landing = _landing(hebrew)
        readers = {name: MagicMock() for name in _READERS}

        with (
            patch.multiple(group_emission, **readers),
            patch.object(group_emission, "_safe_ready_tab_events", return_value=[]),
        ):
            outcomes = [first, await _chunk_2_outcome(hebrew)]
            for n in range(len(outcomes)):
                landing.events(outcomes[n], outcomes[: n + 1], (_IDEAS, _CONCEPTS))

        merged = {name: mock.call_args.args[1] for name, mock in readers.items()}
        assert {name: _stamps(m, "learning", "keyIdeas") for name, m in merged.items()} == {
            name: [600, 2522, 2596, 2641] for name in _READERS
        }


_READERS = ("start_quiz_task", "start_hero_task", "start_merge_rank_task")


def _landing(index: QuoteIndex) -> GroupLanding:
    ctx = PipelineContext("vsid", "yt", {}, MagicMock(), MagicMock(), PipelineTimer())
    ctx.plan_result = PlanResult.model_validate(
        {"contentTags": ["learning"], "primaryTag": "learning", "tabs": _TABS, "confidence": 0.9}
    )
    ctx.video_data = SimpleNamespace(title="t", duration=4282)  # type: ignore[assignment]
    ctx.frames_done.set()
    run = _run(index)
    return GroupLanding.for_run(ctx, run.text_groups, len(run.chunks), run)
