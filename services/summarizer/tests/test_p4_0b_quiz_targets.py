"""Every quiz v2 question tests a thesis, an argument or a number (output-quality 4.0 b,
G3-1 / G3-3).

Gate 3's reviewers called the quiz "recall / trivia": S03 asked about the opening
anecdote, S14 asked three metformin questions. The key ideas a question may test are
now the thesis / argument / number ones, each shown with an id the question names; a
question naming none is matched to the target stamped within 90 s of it or dropped, a
second question on one idea ranks behind every first, and one question per outline
section ships before two in one. A translated quote (S15's Hebrew) takes its idea's
location, never a clock copied from elsewhere. The LLM is faked at the retry wrapper.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from src.models.pipeline_types import EnrichmentData, QuizQuestion
from src.services.pipeline.quiz_v2 import QuizV2Request, enrich_quiz_v2, keep_checked
from src.services.pipeline.quiz_v2_ideas import QuizTarget, quiz_targets
from src.services.pipeline.quiz_v2_prompt import build_quiz_v2_prompts
from src.services.pipeline.quiz_v2_select import select_quiz
from src.services.pipeline.quiz_v2_targets import Candidate
from src.services.pipeline.quote_verify import QuoteIndex

_PATCH_LLM = "src.services.pipeline.quiz_v2.call_llm_with_retry"
_TEN_MIN = 600
_LINES = {
    30: "A card at 24% costs you more than any index fund returns at 7%.",
    70: "Every spare dollar goes to the card first because interest compounds.",
    160: "Then, and only then, the same dollar goes to the index fund.",
    400: "My uncle once lost his car keys in the bank lobby on a Tuesday.",
    520: "Keep one month of expenses in cash so a surprise never lands on the card.",
}
_SEGMENTS = [{"start": second, "text": text} for second, text in _LINES.items()]
_TRANSCRIPT = "\n".join(f"[{s // 60}:{s % 60:02d}] {text}" for s, text in _LINES.items())
_IDEAS = [
    {"claim": "Pay the card first", "why": "24% beats 7%", "timestamp": 30, "kind": "argument"},
    {"claim": "Fund second", "why": "Only then", "timestamp": 160, "kind": "thesis"},
    {"claim": "The uncle's lost keys", "why": "A joke", "timestamp": 400, "kind": "story"},
    {"claim": "One month of cash", "why": "A buffer", "timestamp": 520, "kind": "number"},
]


def _item(name: str, second: int, idea: str | None) -> dict[str, Any]:
    item: dict[str, Any] = {
        "question": f"What does the speaker say about {name}?",
        "options": [f"{name} first", f"{name} second", f"{name} third", f"{name} last"],
        "correctIndex": 0,
        "explanation": "The speaker says so.",
        "quote": _LINES[second],
        "timestamp": f"{second // 60}:{second % 60:02d}",
    }
    if idea is not None:
        item["idea"] = idea
    return item


def _request(ideas: list[dict[str, Any]] | None = None) -> QuizV2Request:
    return QuizV2Request(
        primary_tag="learning",
        title="Pay the card first",
        duration_s=_TEN_MIN,
        transcript=_TRANSCRIPT,
        extraction={"learning": {"keyIdeas": _IDEAS if ideas is None else ideas}},
    )


async def _quiz(*items: dict[str, Any]) -> EnrichmentData:
    reply = json.dumps({"quiz": list(items)})
    with patch(_PATCH_LLM, new=AsyncMock(return_value=reply)):
        result = await enrich_quiz_v2(MagicMock(), _request(), QuoteIndex.build(_SEGMENTS))
    assert result is not None
    return result


def _asked(result: EnrichmentData) -> tuple[list[str], list[str]]:
    """The shipped and the spare questions' names."""
    return (
        [q.question.removeprefix("What does the speaker say about ") for q in result.quiz],
        [q.question.removeprefix("What does the speaker say about ") for q in result.spares],
    )


# ─── Which ideas are targets ───


def _key_ideas_block(prompt: str) -> list[str]:
    return prompt.split("<key_ideas>\n", 1)[1].split("\n</key_ideas>", 1)[0].splitlines()


class TestTargets:
    def test_should_render_only_thesis_argument_and_number_ideas_with_ids_when_items_carry_kinds(
        self,
    ) -> None:
        (call,) = build_quiz_v2_prompts(_request())

        assert _key_ideas_block(call.text) == [
            "- K1 [0:30] argument: Pay the card first — 24% beats 7%",
            "- K2 [2:40] thesis: Fund second — Only then",
            "- K3 [8:40] number: One month of cash — A buffer",
        ]

    def test_should_treat_every_item_as_a_target_when_no_item_carries_a_kind(self) -> None:
        extraction = {"news": {"claims": [{"claim": "Prices rose"}, {"claim": "Wages fell"}]}}

        assert [target.id for target in quiz_targets("news", extraction)] == ["K1", "K2"]

    def test_should_ask_every_question_to_name_its_key_ideas_id(self) -> None:
        (call,) = build_quiz_v2_prompts(_request())

        assert 'names that entry\'s id in "idea"' in call.text


# ─── Which idea a question tests ───


class TestTargetCheck:
    async def test_should_drop_a_question_whose_idea_is_no_target_id(self) -> None:
        result = await _quiz(
            _item("the card", 30, "K1"),
            _item("the keys", 400, "K9"),
            _item("the fund", 160, "K2"),
            _item("the cash", 520, "K3"),
        )

        assert "the keys?" not in [*_asked(result)[0], *_asked(result)[1]]

    async def test_should_match_a_question_without_an_id_to_the_target_idea_stamped_within_90_s(
        self,
    ) -> None:
        result = await _quiz(
            _item("the interest", 70, None),
            _item("the fund", 160, "K2"),
            _item("the cash", 520, "K3"),
        )

        assert "the interest?" in _asked(result)[0]

    async def test_should_rank_a_second_question_on_the_same_idea_after_every_first(self) -> None:
        result = await _quiz(
            _item("the card", 30, "K1"),
            _item("the interest", 70, "K1"),
            _item("the fund", 160, "K2"),
            _item("the cash", 520, "K3"),
        )

        assert _asked(result) == (["the card?", "the fund?", "the cash?"], ["the interest?"])


def _question(n: int, timestamp: int) -> QuizQuestion:
    return QuizQuestion.model_validate(
        {
            "question": f"Why does the speaker say so (#{n})?",
            "options": [f"Answer {n:02d}", "Wrong one A", "Wrong one B", "Wrong one C"],
            "correctIndex": 0,
            "explanation": "The speaker gives this reason.",
            "timestamp": timestamp,
        }
    )


class TestOutlineSections:
    def test_should_ship_one_question_per_outline_section_before_filling_by_rank(self) -> None:
        # S14: the second and third metformin questions sat in one section.
        questions = [_question(n, stamp) for n, stamp in enumerate([100, 150, 200, 400])]

        shipped, _ = select_quiz(questions, 3, [], outline=(0, 300))

        assert [q.timestamp for q in shipped] == [100, 150, 400]


# ─── Translated questions (G3-3) ───

# S15's chunk 2: the key idea's Hebrew quote is said at 43:16 (2596 s).
_HEBREW = "התשובה היא שביטוי חדשים נוצרים באופן אקראי כסף שנופל מהשמיים"
_HEBREW_SEGMENTS = [
    {"start": 2580, "text": "המחשב זוכה במטבעות"},
    {"start": 2596, "text": _HEBREW},
    {"start": 2620, "text": "יהיה עשרים ואחד מיליון"},
]
_S15_CHUNK_2 = (2400, 4282)


def _translated(timestamp: int, idea: str | None) -> Candidate:
    question = QuizQuestion.model_validate(
        {
            "question": "How are new bitcoins created?",
            "options": [
                "At random by the network",
                "By a bank",
                "By the state",
                "By miners' votes",
            ],
            "correctIndex": 0,
            "explanation": "The network creates them at random.",
            "quote": "new coins are created at random, money falling from the sky",
            "timestamp": timestamp,
        }
    )
    target = QuizTarget("K1", {}, None, "") if idea == "unstamped" else None
    return Candidate(question, _S15_CHUNK_2, () if target is None else (target,), target, True)


def _hebrew_index() -> QuoteIndex:
    return QuoteIndex.build(_HEBREW_SEGMENTS, "he")


class TestTranslatedStamp:
    def test_should_stamp_a_translated_question_at_its_ideas_verified_quote(self) -> None:
        # The idea's stamp is the model's late 56:20; its own Hebrew quote is said at 43:16.
        target = QuizTarget("K1", {"claim": "New coins"}, 3380, _HEBREW)
        question = _translated(80, None).question  # the hero's 1:20, copied
        candidate = Candidate(question, _S15_CHUNK_2, (target,), target, True)

        (checked,) = keep_checked([candidate], _hebrew_index(), 4282)

        assert checked.question.timestamp == 2596

    def test_should_drop_a_translated_question_stamped_outside_its_part_when_its_idea_has_no_stamp(
        self,
    ) -> None:
        assert keep_checked([_translated(80, "unstamped")], _hebrew_index(), 4282) == []

    def test_should_keep_a_translated_question_stamped_inside_its_part_when_its_idea_has_no_stamp(
        self,
    ) -> None:
        (checked,) = keep_checked([_translated(2700, "unstamped")], _hebrew_index(), 4282)

        assert checked.question.timestamp == 2700
