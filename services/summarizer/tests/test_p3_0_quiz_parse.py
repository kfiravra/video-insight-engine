"""Quiz v2 reads a broken reply item by item and asks once more when nothing parses
(output-quality p3.0, G2-1).

Gate 2 lost S02's quiz: Haiku closed ten valid questions with ``"57:40"}}]}`` — one
brace too many — the envelope parse found nothing ("kept 0 of 0") and the quiz went.
The salvage (``quiz_v2_parse``) decodes the ``quiz`` list one question at a time;
a reply in which no question parses is asked for once more, never a third time. The
LLM is faked at the retry wrapper.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from src.services.pipeline.enrichment import parse_quiz
from src.services.pipeline.quiz_v2 import QuizV2Request, enrich_quiz_v2
from src.services.pipeline.quiz_v2_parse import quiz_items, salvage_quiz_items
from src.services.pipeline.quote_verify import QuoteIndex

_PATCH_LLM = "src.services.pipeline.quiz_v2.call_llm_with_retry"
# S02 (Fridman / Tegmark, 83 min): the gate-2 quiz_v2 reply's last three questions as
# Haiku wrote them (``data/S02/trace.json``), its broken ending kept: "}}]}".
_S02_REPLY_TAIL = (
    '{"quiz": [{"question": "What does Tegmark identify as the two parts of the value '
    'alignment problem?", "options": ["A technical problem of making machines adopt our '
    'goals, and a philosophical problem of whose values", "Making machines faster and '
    'making them cheaper", "Choosing a programming language and choosing a hardware '
    'platform", "Solving consciousness and solving intelligence"], "correctIndex": 0, '
    '"explanation": "Tegmark separates the engineering challenge of getting machines to '
    "understand and retain goals from the harder question of which human values to "
    'encode.", "quote": "there\'s the technical value alignment problem of figuring out '
    'just how to make machines understand our goals adopt them and retain them", '
    '"timestamp": "45:20"}, {"question": "Why does Tegmark say we should start with '
    'kindergarten ethics instead of waiting for full value consensus?", "options": '
    '["Refusing to act until consensus exists guarantees failure, and perfect should not '
    'be the enemy of good", "Kindergarten ethics are the only values that are '
    'philosophically correct", "Full consensus is already reached among all humans", '
    '"Machines can only learn very simple rules"], "correctIndex": 0, "explanation": '
    '"Tegmark argues that waiting for complete agreement is a bad strategy, so we should '
    "hardwire widely shared basic values like airplanes not flying into buildings and "
    'grow the list over time.", "quote": "we shouldn\'t let perfect be the enemy of good '
    'instead we should start with the kindergarten ethics", "timestamp": "47:20"}, '
    '{"question": "Why does Tegmark say trust in advanced AI systems should come from '
    'verifiable understanding rather than from training data alone?", "options": '
    "[\"Understanding and proofs give assurance about behavior, while 'trained on tons of "
    'data\' gives none", "Training data is always corrupted and therefore useless", '
    '"Machines cannot be trusted at all, only humans can", "Proofs are cheaper to '
    'produce than training"], "correctIndex": 0, "explanation": "Tegmark contrasts a '
    "self-driving car that merely never crashed with a computer-verified proof that it "
    'will never swerve into traffic, arguing the proof builds real trust.", "quote": "it '
    "says that under no circumstances is this car just gonna swerve into oncoming "
    'traffic and that kind of information helps will build trust", "timestamp": '
    '"57:40"}}]}'
)
_S02_DURATION_S = 4978
_S02_SEGMENTS = [
    {
        "start": 2720,
        "text": "there's the technical value alignment problem of figuring out just how to "
        "make machines understand our goals adopt them and retain them",
    },
    {
        "start": 2840,
        "text": "we shouldn't let perfect be the enemy of good instead we should start with "
        "the kindergarten ethics",
    },
    {
        "start": 3460,
        "text": "it says that under no circumstances is this car just gonna swerve into "
        "oncoming traffic and that kind of information helps will build trust",
    },
]


def _item(n: int) -> dict[str, Any]:
    return {
        "question": f"Why does the speaker say so (#{n})?",
        "options": ["Because of A", "Because of B", "Because of C", "Because of D"],
        "correctIndex": 0,
        "explanation": "The speaker gives this reason.",
        "quote": _S02_SEGMENTS[n % 3]["text"],
        "timestamp": "45:20",
    }


def _items_json(count: int) -> list[str]:
    return [json.dumps(_item(n)) for n in range(count)]


def _s02_request() -> QuizV2Request:
    return QuizV2Request(
        primary_tag="learning",
        title="Max Tegmark: Life 3.0",
        duration_s=_S02_DURATION_S,
        transcript="\n".join(
            f"[{s['start'] // 60}:{s['start'] % 60:02d}] {s['text']}" for s in _S02_SEGMENTS
        ),
        extraction={"learning": {"keyIdeas": [{"claim": "Alignment", "timestamp": 2720}]}},
    )


async def _quiz_v2(llm: AsyncMock) -> Any:
    with patch(_PATCH_LLM, new=llm):
        return await enrich_quiz_v2(MagicMock(), _s02_request(), QuoteIndex.build(_S02_SEGMENTS))


# ─── The salvage ───


class TestSalvage:
    def test_should_read_every_question_when_the_reply_closes_with_a_stray_brace(self) -> None:
        assert len(quiz_items(_S02_REPLY_TAIL)) == 3

    def test_should_keep_the_questions_before_a_cut_off_one_when_the_reply_is_truncated(
        self,
    ) -> None:
        reply = '{"quiz": [' + ", ".join(_items_json(3)) + ', {"question": "Why does the'

        assert len(parse_quiz(quiz_items(reply), 10)) == 3

    def test_should_skip_a_broken_question_alone_when_its_neighbours_decode(self) -> None:
        first, broken, last = _items_json(3)
        reply = (
            '{"quiz": ['
            + ", ".join([first, broken.replace('", "options"', ' "options"'), last])
            + "]}"
        )

        assert quiz_items(reply) == [_item(0), _item(2)]

    def test_should_read_a_bare_list_when_the_reply_has_no_envelope(self) -> None:
        assert len(quiz_items("[" + ", ".join(_items_json(2)) + "]")) == 2

    def test_should_read_markdown_fenced_questions_when_the_envelope_is_broken(self) -> None:
        reply = '```json\n{"quiz": [' + ", ".join(_items_json(2)) + "}]}\n```"

        assert len(quiz_items(reply)) == 2

    def test_should_stop_at_the_end_of_the_list_when_more_json_follows(self) -> None:
        reply = '{"quiz": [' + _items_json(1)[0] + '], "notes": {"question": "not one"}}'

        assert salvage_quiz_items(reply) == [_item(0)]

    def test_should_find_nothing_when_the_reply_holds_no_quiz_list(self) -> None:
        assert quiz_items("no quiz today") == []

    def test_should_keep_the_envelope_when_the_reply_parses_whole(self) -> None:
        reply = json.dumps({"quiz": [_item(0), "not a question", _item(1)]})

        assert len(quiz_items(reply)) == 3


# ─── The stage: the S02 regression and the one retry ───


class TestS02Regression:
    async def test_should_ship_the_quiz_when_the_reply_closes_with_one_brace_too_many(
        self,
    ) -> None:
        result = await _quiz_v2(AsyncMock(return_value=_S02_REPLY_TAIL))

        assert result is not None and [q.timestamp for q in result.quiz] == [2720, 2840, 3460]

    async def test_should_make_one_call_when_the_broken_reply_holds_questions(self) -> None:
        llm = AsyncMock(return_value=_S02_REPLY_TAIL)

        await _quiz_v2(llm)

        assert llm.await_count == 1


class TestRetryWhenNothingParses:
    async def test_should_ship_the_second_reply_when_the_first_holds_no_question(self) -> None:
        result = await _quiz_v2(AsyncMock(side_effect=["no quiz today", _S02_REPLY_TAIL]))

        assert result is not None and len(result.quiz) == 3

    async def test_should_ask_no_third_time_when_the_second_reply_holds_no_question(
        self,
    ) -> None:
        llm = AsyncMock(side_effect=['{"quiz": []}', "still nothing", _S02_REPLY_TAIL])

        result = await _quiz_v2(llm)

        assert (result, llm.await_count) == (None, 2)

    async def test_should_ask_once_more_when_no_item_is_a_valid_question(self) -> None:
        llm = AsyncMock(side_effect=[json.dumps({"quiz": [{"question": "?"}]}), _S02_REPLY_TAIL])

        await _quiz_v2(llm)

        assert llm.await_count == 2

    async def test_should_not_ask_again_when_the_call_itself_failed(self) -> None:
        llm = AsyncMock(return_value=None)

        await _quiz_v2(llm)

        assert llm.await_count == 1
