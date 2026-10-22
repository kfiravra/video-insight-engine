"""Which quiz v2 questions ship, and where each key sits (output-quality p3.0, G2-2 / 4 / 5).

* G2-2 — gate 2 keyed option 0 on 22 of 25 questions: the shipped keys are dealt evenly
  over the four positions (no position more than ⌈k / 4⌉ of k), fixed by the questions'
  texts in every process, each answer still the keyed option (the hero check reads it);
* G2-4 — S03 shipped 8 questions in the first 22 of 57 minutes: the prompt names the
  video's thirds and code ships one question from each third first;
* G2-5 — the key was the longest option: a key over 1.5x the mean wrong option is
  flagged and ships only when nothing clean is left.

No LLM: the selection is pure; the stage tests fake the call at the retry wrapper.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from collections import Counter
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.models.pipeline_types import QuizQuestion
from src.services.pipeline.phases import enrichment as enrichment_phase
from src.services.pipeline.quiz_v2 import (
    QuizV2Request,
    enrich_quiz_v2,
    keyed_option,
    restates_the_hero,
)
from src.services.pipeline.quiz_v2_prompt import build_quiz_v2_prompts
from src.services.pipeline.quiz_v2_select import (
    length_tell,
    select_quiz,
    shuffle_quiz,
    stretch_bounds,
    stretch_of,
    stretch_quotas,
)
from src.services.pipeline.quote_verify import QuoteIndex

_PATCH_LLM = "src.services.pipeline.quiz_v2.call_llm_with_retry"
_SUMMARIZER = Path(__file__).resolve().parents[1]
# S03 (Stanford BIO 150, 57:14): its thirds and the stamps of the 8 questions gate 2 shipped.
_S03_S = 3434
_S03_SHIPPED_STAMPS = [382, 421, 659, 694, 1205, 906, 991, 1297]
_THIRDS = stretch_bounds(_S03_S)
_TEN_MIN = 600


def _question(n: int, timestamp: int | None = None, key: str = "", **fields: Any) -> QuizQuestion:
    item: dict[str, Any] = {
        "question": f"Why does the speaker say so (#{n})?",
        "options": [key or f"Answer {n:02d}", "Wrong one A", "Wrong one B", "Wrong one C"],
        "correctIndex": 0,
        "explanation": "The speaker gives this reason.",
        "timestamp": timestamp,
    }
    item.update(fields)
    return QuizQuestion.model_validate(item)


def _told(n: int, timestamp: int | None = None) -> QuizQuestion:
    """A question whose key gives itself away: three times the wrong options' length."""
    return _question(n, timestamp, key="An answer far longer than any of the wrong ones")


def _keys(questions: list[QuizQuestion]) -> list[int]:
    return [question.correct_index for question in questions]


def _numbers(questions: list[QuizQuestion]) -> list[int]:
    return [int(question.question.split("#")[1].rstrip(")?")) for question in questions]


# ─── G2-2: the shuffle ───


_FRESH_PROCESS_SCRIPT = """
import json, sys
from src.models.pipeline_types import QuizQuestion
from src.services.pipeline.quiz_v2_select import shuffle_quiz
questions = [QuizQuestion.model_validate(item) for item in json.loads(sys.stdin.read())]
print(json.dumps([question.options for question in shuffle_quiz(questions)]))
"""


def _eight() -> list[QuizQuestion]:
    return [_question(n) for n in range(8)]


def _options_in_a_fresh_process(hash_seed: str) -> list[list[str]]:
    """The shuffled options of :func:`_eight` as a new interpreter with ``hash_seed`` deals them."""
    done = subprocess.run(
        [sys.executable, "-c", _FRESH_PROCESS_SCRIPT],
        input=json.dumps([q.model_dump(by_alias=True) for q in _eight()]),
        capture_output=True,
        check=True,
        cwd=_SUMMARIZER,
        env={**os.environ, "PYTHONHASHSEED": hash_seed},
        text=True,
    )
    return json.loads(done.stdout)


class TestShuffle:
    def test_should_keep_every_questions_answer_when_the_options_are_shuffled(self) -> None:
        questions = [_question(n) for n in range(8)]

        shuffled = shuffle_quiz(questions)

        assert [q.options[q.correct_index] for q in shuffled] == [
            f"Answer {n:02d}" for n in range(8)
        ]

    def test_should_key_no_position_more_than_twice_when_eight_questions_are_keyed_first(
        self,
    ) -> None:
        shuffled = shuffle_quiz([_question(n) for n in range(8)])

        assert max(Counter(_keys(shuffled)).values()) == 2

    def test_should_key_three_different_positions_when_a_three_question_quiz_ships(self) -> None:
        shuffled = shuffle_quiz([_question(n) for n in range(3)])

        assert len(set(_keys(shuffled))) == 3

    def test_should_keep_the_question_order_when_the_options_are_shuffled(self) -> None:
        assert _numbers(shuffle_quiz([_question(n) for n in range(5)])) == list(range(5))

    def test_should_shuffle_the_same_way_when_the_same_questions_come_again(self) -> None:
        first = shuffle_quiz([_question(n) for n in range(8)])

        assert shuffle_quiz([_question(n) for n in range(8)]) == first

    def test_should_shuffle_the_same_way_when_another_process_has_another_hash_seed(
        self,
    ) -> None:
        runs = [_options_in_a_fresh_process(seed) for seed in ("1", "2")]

        assert runs[0] == runs[1] == [q.options for q in shuffle_quiz(_eight())]

    def test_should_shuffle_a_question_with_fewer_options_inside_its_own_options(self) -> None:
        two = _question(0, options=["Yes, by design", "No, by accident"])

        (shuffled,) = shuffle_quiz([two])

        assert shuffled.options[shuffled.correct_index] == "Yes, by design"


class TestShuffledAnswersStillRead:
    async def test_should_read_each_shipped_and_spare_answer_when_quiz_v2_shuffled_them(
        self,
    ) -> None:
        segments = [
            {"start": 30 * n, "text": f"the speaker says reason number {n} aloud"} for n in range(5)
        ]
        reply = [
            _question(n, 30 * n, quote=segments[n]["text"]).model_dump(by_alias=True)
            for n in range(5)
        ]
        request = QuizV2Request(
            primary_tag="learning",
            title="Reasons",
            duration_s=_TEN_MIN,
            transcript="[0:00] the speaker says the reasons aloud",
            extraction={},
        )
        with patch(_PATCH_LLM, new=AsyncMock(return_value=json.dumps({"quiz": reply}))):
            result = await enrich_quiz_v2(MagicMock(), request, QuoteIndex.build(segments))

        assert result is not None
        landed = [q.model_dump(by_alias=True) for q in [*result.quiz, *result.spares]]
        assert {keyed_option(q) for q in landed} == {f"Answer {n:02d}" for n in range(5)}

    def test_should_find_the_restated_takeaway_when_the_keyed_option_moved(self) -> None:
        answer = "Let users bring their own agent by shipping a CLI"
        (shuffled,) = shuffle_quiz([_question(0, key=answer)])
        takeaway = "Apps should ship a CLI so users bring their own agent. [45:23]"

        assert restates_the_hero(shuffled.model_dump(by_alias=True), [takeaway]) is True


# ─── G2-5: the length tell ───


class TestLengthTell:
    def test_should_flag_a_key_over_one_and_a_half_times_the_mean_wrong_option(self) -> None:
        assert length_tell(_told(0)) is True

    def test_should_not_flag_a_key_as_long_as_the_wrong_options(self) -> None:
        assert length_tell(_question(0)) is False

    def test_should_read_the_keyed_option_when_the_key_is_not_first(self) -> None:
        question = _question(
            0, options=["Wrong one A", "An answer far longer than any wrong one", "B", "C"]
        )

        assert length_tell(question.model_copy(update={"correct_index": 1})) is True

    def test_should_ship_a_clean_spare_instead_of_a_flagged_question(self) -> None:
        questions = [_told(0), _question(1), _question(2), _question(3)]

        shipped, _ = select_quiz(questions, 3, [])

        assert _numbers(shipped) == [1, 2, 3]

    def test_should_ship_a_flagged_question_when_nothing_clean_is_left(self) -> None:
        shipped, _ = select_quiz([_told(0), _question(1), _question(2)], 3, [])

        assert _numbers(shipped) == [0, 1, 2]

    def test_should_hand_a_flagged_question_on_after_the_clean_spares(self) -> None:
        questions = [_told(0), _question(1), _question(2), _question(3), _question(4)]

        _, spares = select_quiz(questions, 3, [])

        assert _numbers(spares) == [4, 0]


# ─── G2-4: the spread ───


class TestStretches:
    def test_should_cut_the_video_into_thirds_when_its_length_is_known(self) -> None:
        assert stretch_bounds(_S03_S) == [(0, 1145), (1145, 2290), (2290, 3434)]

    @pytest.mark.parametrize("duration_s", [None, 0, -5.0, float("nan"), float("inf")])
    def test_should_name_no_stretch_when_the_length_is_unknown(
        self, duration_s: float | None
    ) -> None:
        assert stretch_bounds(duration_s) == []

    def test_should_count_a_stamp_past_the_end_in_the_last_third(self) -> None:
        assert stretch_of(_S03_S + 8, _THIRDS) == 2

    def test_should_place_a_stretchs_first_second_in_that_stretch(self) -> None:
        assert [stretch_of(stamp, _THIRDS) for stamp in (1144, 1145, 2289, 2290)] == [0, 1, 1, 2]

    def test_should_show_s03s_gate_2_quiz_missing_its_last_third(self) -> None:
        assert {stretch_of(stamp, _THIRDS) for stamp in _S03_SHIPPED_STAMPS} == {0, 1}


class TestSpreadSelection:
    def test_should_ship_one_question_from_each_third_when_the_model_front_loads(self) -> None:
        stamps = [100, 200, 300, 400, 500, 600, 700, 800, 1500, 3000]
        questions = [_question(n, stamp) for n, stamp in enumerate(stamps)]

        shipped, _ = select_quiz(questions, 8, _THIRDS)

        assert {stretch_of(q.timestamp, _THIRDS) for q in shipped} == {0, 1, 2}

    def test_should_ship_in_the_models_order_when_a_later_question_covers_a_third(self) -> None:
        stamps = [100, 200, 300, 400, 3000]
        questions = [_question(n, stamp) for n, stamp in enumerate(stamps)]

        shipped, spares = select_quiz(questions, 3, _THIRDS)

        assert (_numbers(shipped), _numbers(spares)) == ([0, 1, 4], [2, 3])

    def test_should_take_the_more_important_question_when_two_cover_the_same_third(
        self,
    ) -> None:
        stamps = [100, 200, 300, 2500, 3000]
        questions = [_question(n, stamp) for n, stamp in enumerate(stamps)]

        shipped, _ = select_quiz(questions, 3, _THIRDS)

        assert _numbers(shipped) == [0, 1, 3]

    def test_should_fill_in_the_models_order_when_a_third_has_no_question(self) -> None:
        questions = [_question(n, stamp) for n, stamp in enumerate([100, 200, 300, 1500])]

        shipped, _ = select_quiz(questions, 3, _THIRDS)

        assert _numbers(shipped) == [0, 1, 3]

    def test_should_ship_the_first_questions_when_the_length_is_unknown(self) -> None:
        questions = [_question(n, stamp) for n, stamp in enumerate([100, 200, 300, 3000])]

        shipped, _ = select_quiz(questions, 3, [])

        assert _numbers(shipped) == [0, 1, 2]

    def test_should_cover_a_third_with_a_clean_question_only(self) -> None:
        questions = [_question(0, 100), _question(1, 200), _question(2, 300), _told(3, 3000)]

        shipped, _ = select_quiz(questions, 3, _THIRDS)

        assert _numbers(shipped) == [0, 1, 2]


# ─── The late refill keeps the spread (settle_late_quiz) ───

_CLI_ANSWER = "Let users bring their own agent by shipping a CLI"
_CLI_TAKEAWAY = "Apps should ship a CLI so users bring their own agent. [45:23]"


def _stored(question: QuizQuestion) -> dict[str, Any]:
    return question.model_dump(by_alias=True)


def _settled(spares: list[QuizQuestion], duration_s: float | None = _S03_S) -> list[int]:
    """The shipped numbers once question 1 (stamped in S03's last third) is dropped for
    restating the shipped takeaway."""
    quiz = [_question(0, 100), _question(1, 3000, key=_CLI_ANSWER), _question(2, 200)]
    record = {"quiz": [_stored(q) for q in quiz], "spares": [_stored(q) for q in spares]}
    ctx: Any = SimpleNamespace(
        enrichment_data=record,
        synthesis_dict={"keyTakeaways": [_CLI_TAKEAWAY]},
        video_summary_id="v",
        video_data=SimpleNamespace(duration=duration_s),
    )
    enrichment_phase.settle_late_quiz(ctx)
    return [int(q["question"].split("#")[1].rstrip(")?")) for q in ctx.enrichment_data["quiz"]]


class TestLateRefill:
    def test_should_refill_from_the_dropped_questions_third_when_a_spare_is_stamped_there(
        self,
    ) -> None:
        assert _settled([_question(3, 300), _question(4, 3100)]) == [0, 2, 4]

    def test_should_refill_with_the_next_spare_when_none_is_stamped_in_that_third(self) -> None:
        assert _settled([_question(3, 300), _question(4, 1500)]) == [0, 2, 3]

    def test_should_pass_over_a_flagged_spare_when_it_is_the_one_in_that_third(self) -> None:
        assert _settled([_question(3, 300), _told(4, 3100)]) == [0, 2, 3]

    def test_should_refill_with_the_next_spare_when_the_length_is_unknown(self) -> None:
        assert _settled([_question(3, 300), _question(4, 3100)], None) == [0, 2, 3]


# ─── The prompt ───


# S03's p3i1 key-idea stamps: 17 in its first third, 12 in the second, 3 in the last.
_S03_IDEA_STAMPS = [75, 144, 180, 280, 320, 341, 382, 421, 500, 516, 561, 640, 700, 720, 880,
                    921, 1018, 1183, 1220, 1300, 1303, 1342, 1500, 1582, 1681, 1800, 1895, 2196,
                    2278, 2381, 2421, 2880]  # fmt: skip


def _ideas(stamps: list[int]) -> dict[str, Any]:
    ideas = [{"claim": f"Claim at {s}", "why": "Because.", "timestamp": s} for s in stamps]
    return {"learning": {"keyIdeas": ideas}}


def _prompt(duration_s: float | None, extraction: dict[str, Any] | None = None) -> str:
    """The first call's prompt (one whole-video call without stamped key ideas)."""
    request = QuizV2Request(
        primary_tag="learning",
        title="Behavioral biology",
        duration_s=duration_s,
        transcript="[0:00] We start off with a scenario.",
        extraction=extraction or {},
    )
    first, *_ = build_quiz_v2_prompts(request)
    return first.text


class TestStretchQuotas:
    def test_should_ask_every_stretch_that_holds_a_key_idea_for_a_question(self) -> None:
        assert stretch_quotas(_S03_IDEA_STAMPS, _THIRDS, 10) == [5, 3, 2]

    def test_should_ask_for_the_whole_count_across_the_stretches(self) -> None:
        assert sum(stretch_quotas(_S03_IDEA_STAMPS, _THIRDS, 7)) == 7

    def test_should_ask_nothing_of_a_stretch_without_key_ideas(self) -> None:
        assert stretch_quotas([100, 200, 3000], _THIRDS, 5)[1] == 0

    def test_should_return_no_quotas_when_the_length_is_unknown(self) -> None:
        assert stretch_quotas(_S03_IDEA_STAMPS, stretch_bounds(None), 10) == []

    def test_should_return_no_quotas_when_no_key_idea_is_stamped(self) -> None:
        assert stretch_quotas([], _THIRDS, 10) == []


class TestPrompt:
    def test_should_ask_to_spread_the_questions_to_the_last_key_ideas(self) -> None:
        prompt = _prompt(_S03_S, _ideas(_S03_IDEA_STAMPS))

        assert "spread them across <key_ideas>, its last entries included" in prompt

    def test_should_ask_for_wrong_options_built_from_the_correct_one(self) -> None:
        assert "within 2 words of the correct option's length" in _prompt(_S03_S)

    def test_should_not_ask_the_model_to_vary_the_key_position(self) -> None:
        assert "vary that position" not in _prompt(_S03_S)
