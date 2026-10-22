"""Quiz v2 writes one call per stretch of the video (output-quality p3.0, G2-4 round 2).

Told "at least one per stretch" (p3i1), then given a count per stretch beside grouped
key ideas (p3i2), Haiku still wrote nothing past 37:30 of S03's 57:14. With a known
length and stamped key ideas the quiz is now one call per third that holds a key idea,
in parallel under one deadline: call k asks for its quota, names its range, and reads
only its third's key ideas and transcript lines (± 30 s). A call that fails or runs out
of time loses only its stretch; the replies merge by rank. Without quotas: one call over
the whole video. The LLM is faked at the retry wrapper.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.models.pipeline_types import QuizQuestion
from src.services.pipeline import quiz_v2
from src.services.pipeline.quiz_v2 import QuizV2Request, enrich_quiz_v2, merge_by_rank
from src.services.pipeline.quiz_v2_prompt import build_quiz_v2_prompts, stretch_transcript
from src.services.pipeline.quiz_v2_select import stretch_bounds, stretch_of, stretch_quotas
from src.services.pipeline.quiz_v2_targets import Candidate
from src.services.pipeline.quote_verify import QuoteIndex
from tests.replay.quiz_synth import quiz_v2_answer, transcript_lines

_PATCH_LLM = "src.services.pipeline.quiz_v2.call_llm_with_retry"
# S03 (57:14): thirds 0:00–19:05 · 19:05–38:10 · 38:10–57:14; key ideas in all three.
_S03_S = 3434
_THIRDS = stretch_bounds(_S03_S)
_IDEA_STAMPS = [75, 382, 921, 1220, 1500, 1800, 2381, 2880]
_LAST_THIRD = "inside this stretch of the video: 38:10–57:14 (the video runs 0:00–57:14)"
_SECOND_THIRD = "inside this stretch of the video: 19:05–38:10"
_SEGMENTS = [
    {"start": s, "text": f"at second {s} the speaker explains idea number {s} with care"}
    for s in range(0, _S03_S, 60)
]
_TRANSCRIPT = "\n".join(
    f"[{s['start'] // 60}:{s['start'] % 60:02d}] {s['text']}" for s in _SEGMENTS
)


def _request(duration_s: float | None = _S03_S, stamps: list[int] | None = None) -> QuizV2Request:
    ideas = [
        {"claim": f"Idea at {s}", "why": "Because.", "timestamp": s}
        for s in (_IDEA_STAMPS if stamps is None else stamps)
    ]
    return QuizV2Request(
        primary_tag="learning",
        title="Behavioral biology",
        duration_s=duration_s,
        transcript=_TRANSCRIPT,
        extraction={"learning": {"keyIdeas": ideas}},
    )


def _question(second: int) -> dict[str, Any]:
    return {
        "question": f"What does the speaker explain at second {second}?",
        "options": [f"Idea {second}", "Idea A", "Idea B", "Idea C"],
        "correctIndex": 0,
        "explanation": "The speaker says so.",
        "quote": f"at second {second} the speaker explains idea number {second} with care",
        "timestamp": second,
    }


def _reply(*seconds: int) -> str:
    return json.dumps({"quiz": [_question(s) for s in seconds]})


# Each stretch's fake answer: three questions quoting its own lines.
_ANSWERS = {0: _reply(60, 120, 180), 1: _reply(1200, 1260, 1320), 2: _reply(2400, 2460, 2520)}


def _stretch_in(prompt: str) -> int:
    return 2 if _LAST_THIRD in prompt else 1 if _SECOND_THIRD in prompt else 0


async def _answer_by_stretch(_service: object, prompt: str, **_kwargs: Any) -> str:
    return _ANSWERS[_stretch_in(prompt)]


async def _quiz(llm: AsyncMock, request: QuizV2Request | None = None) -> Any:
    with patch(_PATCH_LLM, new=llm):
        return await enrich_quiz_v2(MagicMock(), request or _request(), QuoteIndex.build(_SEGMENTS))


def _stretches(result: Any) -> set[int | None]:
    return {stretch_of(question.timestamp, _THIRDS) for question in result.quiz}


# ─── The calls ───


class TestStretchCalls:
    async def test_should_make_one_call_per_stretch_when_key_ideas_span_the_thirds(self) -> None:
        llm = AsyncMock(side_effect=_answer_by_stretch)

        await _quiz(llm)

        assert llm.await_count == 3

    def test_should_ask_each_stretch_for_its_quota(self) -> None:
        calls = build_quiz_v2_prompts(_request())

        assert [call.asked for call in calls] == stretch_quotas(_IDEA_STAMPS, _THIRDS, 12)

    def test_should_name_the_stretch_and_its_count_in_the_task_when_a_stretch_call_is_built(
        self,
    ) -> None:
        last = build_quiz_v2_prompts(_request())[2]

        assert f"Questions to write: {last.asked}\nEvery timestamp lies {_LAST_THIRD}." in last.text

    def test_should_give_a_stretch_call_only_the_key_ideas_stamped_in_it(self) -> None:
        last = build_quiz_v2_prompts(_request())[2].text
        ideas = last.split("<key_ideas>\n", 1)[1].split("\n</key_ideas>", 1)[0]

        assert ideas.splitlines() == [
            "- K7 [39:41] Idea at 2381 — Because.",
            "- K8 [48:00] Idea at 2880 — Because.",
        ]

    def test_should_give_a_stretch_call_only_its_lines_within_half_a_minute(self) -> None:
        second = build_quiz_v2_prompts(_request())[1].text
        lines = transcript_lines(second)

        assert (lines[0][0], lines[-1][0]) == (1140, 2280)

    def test_should_make_no_call_for_a_stretch_without_key_ideas(self) -> None:
        calls = build_quiz_v2_prompts(_request(stamps=[100, 3000]))

        assert [_stretch_in(call.text) for call in calls] == [0, 2]

    async def test_should_ship_questions_from_every_third_when_each_call_answers(self) -> None:
        result = await _quiz(AsyncMock(side_effect=_answer_by_stretch))

        assert _stretches(result) == {0, 1, 2}


class TestOneCallLosesOnlyItsStretch:
    async def test_should_keep_the_other_stretches_when_one_call_fails(self) -> None:
        async def answer(_service: object, prompt: str, **_kwargs: Any) -> str | None:
            return None if _stretch_in(prompt) == 1 else _ANSWERS[_stretch_in(prompt)]

        result = await _quiz(AsyncMock(side_effect=answer))

        assert _stretches(result) == {0, 2}

    async def test_should_keep_the_other_stretches_when_one_call_runs_past_the_deadline(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(quiz_v2, "QUIZ_V2_TOTAL_TIMEOUT_S", 0.2)

        async def answer(_service: object, prompt: str, **_kwargs: Any) -> str:
            if _stretch_in(prompt) == 2:
                await asyncio.sleep(5)
            return _ANSWERS[_stretch_in(prompt)]

        result = await _quiz(AsyncMock(side_effect=answer))

        assert _stretches(result) == {0, 1}

    async def test_should_ask_a_stretch_once_more_when_its_reply_holds_no_question(self) -> None:
        asked: list[int] = []

        async def answer(_service: object, prompt: str, **_kwargs: Any) -> str:
            stretch = _stretch_in(prompt)
            asked.append(stretch)
            first_of_the_second_third = stretch == 1 and asked.count(1) == 1
            return "no quiz" if first_of_the_second_third else _ANSWERS[stretch]

        await _quiz(AsyncMock(side_effect=answer))

        assert sorted(asked) == [0, 1, 1, 2]


# ─── The merge ───


def _q(name: str) -> Candidate:
    return Candidate(QuizQuestion.model_validate({**_question(0), "question": name}))


class TestMergeByRank:
    def test_should_take_every_calls_first_question_before_any_second(self) -> None:
        replies = [[_q("a1"), _q("a2")], [_q("b1")], [_q("c1"), _q("c2"), _q("c3")]]

        merged = merge_by_rank(replies)

        assert [c.question.question for c in merged] == ["a1", "b1", "c1", "a2", "c2", "c3"]

    def test_should_keep_a_question_once_when_two_calls_write_it(self) -> None:
        merged = merge_by_rank([[_q("Same?"), _q("a2")], [_q("same?")]])

        assert [c.question.question for c in merged] == ["Same?", "a2"]


# ─── No quotas: one call over the whole video ───


class TestWholeVideoCall:
    @pytest.mark.parametrize(
        ("duration_s", "stamps", "asked"), [(None, _IDEA_STAMPS, 9), (_S03_S, [], 12)]
    )
    def test_should_make_one_call_over_the_whole_transcript_when_there_are_no_quotas(
        self, duration_s: float | None, stamps: list[int], asked: int
    ) -> None:
        (call,) = build_quiz_v2_prompts(_request(duration_s, stamps))

        assert (call.asked, _TRANSCRIPT in call.text) == (asked, True)

    def test_should_state_the_whole_video_range_when_one_call_writes_the_quiz(self) -> None:
        (call,) = build_quiz_v2_prompts(_request(_S03_S, []))

        assert "Every timestamp lies inside the video (0:00–57:14)." in call.text


# ─── Helpers ───


class TestStretchTranscript:
    def test_should_keep_an_unmarked_line_with_the_line_before_it(self) -> None:
        transcript = "[0:00] first\n[1:00] second\n  more of the second\n[9:00] far"

        assert stretch_transcript(transcript, (60, 120)) == "[1:00] second\n  more of the second"


class TestReplayAnswersStretchCalls:
    def test_should_answer_only_the_recorded_questions_placed_in_its_stretch(self) -> None:
        recorded = {"quiz": [{**_question(s), "timestamp": s} for s in (100, 1500, 3000)]}
        lines = [(s["start"], s["text"]) for s in _SEGMENTS]

        answered = quiz_v2_answer(recorded, lines, (1145, 2290, _S03_S))["quiz"]

        assert [item["timestamp"] for item in answered] == [1500 - 1500 % 60]
