"""Quiz v2 is written chunk by chunk on a chunked run (output-quality 4.0 b, G3-1 / G3-3).

Gate 3 shipped S15's 8 questions inside 0:00–27:40 of 71:22 and S02's none past 66:17:
the quiz was cut into thirds, and S15's third-stretch call quoted the hero's takeaways
from other thirds. Now the split run's extraction chunks are the parts — one call per
chunk that holds a target idea, sized by the targets stamped in it, its hero without
another chunk's clocks — and a one-chunk run keeps today's thirds. The LLM is faked at
the retry wrapper or at the stage seam.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, patch

import pytest

from src.config import settings
from src.models.pipeline_types import QuizQuestion
from src.services.pipeline.extraction_chunks import ExtractionChunk
from src.services.pipeline.phases import enrichment as enrichment_phase
from src.services.pipeline.phases.group_emission import GroupLanding
from src.services.pipeline.quiz_v2_prompt import QuizV2Request, build_quiz_v2_prompts
from src.services.pipeline.quiz_v2_select import chunk_windows
from tests.test_group_emission import _CONCEPTS, _FIELDS, _ctx, _group, _outcome

# S15 (71:22, two chunks at 40:00) and S02 (82:58, three chunks): their windows (design §0.1).
_S15_S = 4282
_S15_WINDOWS = ((0, 2400), (2400, _S15_S))
_S02_S = 4978
_S02_WINDOWS = ((0, 2216), (2216, 4271), (4271, _S02_S))
_LAST_CHUNK = "inside this stretch of the video: 40:00–1:11:22 (the video runs 0:00–1:11:22)"


def _ideas(stamps: list[int], kind: str = "argument") -> list[dict[str, Any]]:
    return [
        {"claim": f"Idea at {s}", "why": "Because.", "timestamp": s, "kind": kind} for s in stamps
    ]


def _transcript(duration_s: int) -> str:
    return "\n".join(
        f"[{s // 60}:{s % 60:02d}] line at second {s}" for s in range(0, duration_s, 60)
    )


def _request(
    ideas: list[dict[str, Any]],
    windows: tuple[tuple[int, int], ...] = _S15_WINDOWS,
    duration_s: int = _S15_S,
    hero: str = "",
) -> QuizV2Request:
    return QuizV2Request(
        primary_tag="learning",
        title="The history of inflation",
        duration_s=duration_s,
        transcript=_transcript(duration_s),
        extraction={"learning": {"keyIdeas": ideas}},
        hero=hero,
        windows=windows,
    )


def _chunk(index: int, start: float | None, end: float | None, video_end: float) -> ExtractionChunk:
    return ExtractionChunk(index, "", start_s=start, end_s=end, video_end_s=video_end)


# ─── The parts ───


class TestChunkParts:
    def test_should_write_one_call_per_extraction_chunk_when_the_run_has_two_or_more_chunks(
        self,
    ) -> None:
        calls = build_quiz_v2_prompts(_request(_ideas([300, 1500, 2700, 3900])))

        assert [call.part for call in calls] == list(_S15_WINDOWS)

    def test_should_name_the_chunks_range_in_its_calls_task(self) -> None:
        last = build_quiz_v2_prompts(_request(_ideas([300, 2700])))[-1]

        assert _LAST_CHUNK in last.text

    def test_should_keep_one_call_per_third_when_the_run_has_one_chunk(self) -> None:
        windows = chunk_windows([_chunk(0, None, None, _S15_S)], _S15_S)

        calls = build_quiz_v2_prompts(_request(_ideas([300, 2000, 3900]), windows))

        assert [call.part for call in calls] == [(0, 1428), (1428, 2855), (2855, _S15_S)]

    def test_should_end_the_last_part_at_the_videos_duration_when_its_chunk_is_open(
        self,
    ) -> None:
        # S03: its chapters end at 50:31 (3031 s), the video runs to 57:14 (3434 s).
        chunks = [_chunk(0, None, 1030, 3031), _chunk(1, 1030, None, 3031)]

        assert chunk_windows(chunks, 3434) == ((0, 1030), (1030, 3434))

    def test_should_size_each_chunks_call_by_the_target_ideas_stamped_in_it(self) -> None:
        ideas = [*_ideas([100, 400, 800, 1200, 1600, 2000]), *_ideas([2600, 3000])]
        stories = _ideas([3200, 3400, 3600, 3800], kind="story")

        calls = build_quiz_v2_prompts(_request([*ideas, *stories]))

        assert [call.asked for call in calls] == [9, 3]

    def test_should_give_no_call_to_a_chunk_without_a_target_idea_and_move_its_count_to_the_others(
        self,
    ) -> None:
        ideas = [*_ideas([300, 1200, 2600, 3500]), *_ideas([4500, 4800], kind="story")]

        calls = build_quiz_v2_prompts(_request(ideas, _S02_WINDOWS, _S02_S))

        assert ([call.part for call in calls], sum(c.asked for c in calls)) == (
            list(_S02_WINDOWS[:2]),
            12,
        )


# ─── The hero a chunk's call reads (G3-3) ───

_S15_HERO = (
    "title: The history of inflation\n"
    "tldr: Governments print money in crises\n"
    "- Printing new money in a crisis is an easy fix that becomes a trap [1:20]\n"
    "- Inflation makes people buy now rather than save [27:20]\n"
    "- Bitcoin's supply is capped at 21 million [44:01]"
)


def _hero_block(prompt: str) -> str:
    return prompt.split("<hero>\n", 1)[1].split("\n</hero>", 1)[0]


class TestChunkHero:
    def test_should_hand_a_chunks_call_no_takeaway_stamped_in_another_chunk(self) -> None:
        last = build_quiz_v2_prompts(_request(_ideas([300, 2700]), hero=_S15_HERO))[-1]

        assert _hero_block(last.text).splitlines() == [
            "title: The history of inflation",
            "tldr: Governments print money in crises",
            "- Bitcoin's supply is capped at 21 million [44:01]",
        ]

    def test_should_keep_the_whole_hero_when_one_call_writes_the_quiz(self) -> None:
        (call,) = build_quiz_v2_prompts(_request([], windows=(), duration_s=600, hero=_S15_HERO))

        assert _hero_block(call.text) == _S15_HERO


# ─── The late refill ───


def _question(n: int, timestamp: int, key: str = "") -> dict[str, Any]:
    return QuizQuestion.model_validate(
        {
            "question": f"Why does the speaker say so (#{n})?",
            "options": [key or f"Answer {n:02d}", "Wrong one A", "Wrong one B", "Wrong one C"],
            "correctIndex": 0,
            "explanation": "The speaker gives this reason.",
            "timestamp": timestamp,
        }
    ).model_dump(by_alias=True)


_TAKEAWAY = "Bitcoin's supply is capped at 21 million coins so no state can print more [44:01]"


class TestLateRefill:
    def test_should_refill_a_dropped_question_from_its_own_chunk(self) -> None:
        # 2500 s shares a third with the 1500 s spare but a chunk with the 3000 s one.
        dropped = _question(1, 2500, key="Its supply is capped at 21 million coins")
        record = {
            "quiz": [_question(0, 100), dropped, _question(2, 200)],
            "spares": [_question(3, 1500), _question(4, 3000)],
            "parts": [list(window) for window in _S15_WINDOWS],
        }
        ctx: Any = SimpleNamespace(
            enrichment_data=record,
            synthesis_dict={"keyTakeaways": [_TAKEAWAY]},
            video_summary_id="v",
            video_data=SimpleNamespace(duration=_S15_S),
        )

        enrichment_phase.settle_late_quiz(ctx)

        assert [q["timestamp"] for q in ctx.enrichment_data["quiz"]] == [100, 200, 3000]


# ─── The split run hands its chunks to the quiz ───


@pytest.fixture
async def quiz_v2_call() -> AsyncIterator[AsyncMock]:
    """The quiz stage's LLM boundary; the quiz tasks a test started are gathered."""
    with patch.object(enrichment_phase, "enrich_quiz_v2", AsyncMock(return_value=None)) as mock:
        yield mock
        started = [t for t in asyncio.all_tasks() if t.get_name().startswith("quiz_")]
        await asyncio.gather(*started, return_exceptions=True)


class TestSplitRunWindows:
    async def test_should_pass_the_split_runs_chunk_windows_to_the_quiz_task(
        self, quiz_v2_call: AsyncMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(settings, "HERO_REWRITE_ENABLED", False)
        monkeypatch.setattr(settings, "MERGE_RANK_ENABLED", False)
        ctx = _ctx()
        text1 = _group("text1", *_FIELDS)
        # The run's prompt: the quiz calls read its cached blocks (4.6e).
        run = SimpleNamespace(
            prompt=object(),
            chunks=(_chunk(0, None, 300, 600), _chunk(1, 300, None, 600)),
            text_groups=(text1,),
        )
        landing = GroupLanding.for_run(ctx, [text1], 2, cast(Any, run))
        outcomes = [_outcome(text1, chunk=0), _outcome(text1, chunk=1)]
        for landed in range(1, 3):
            landing.events(outcomes[landed - 1], outcomes[:landed], (_CONCEPTS,))
        assert ctx.quiz_task is not None

        await ctx.quiz_task

        (_, request, _), _ = cast(Any, quiz_v2_call.await_args)
        assert request.windows == ((0, 300), (300, 600))
