"""Quiz sized to the video, stamped per question, never hero-level (output-quality p1.13, L30).

The prompt asks for 3 / 5 / 8 questions by the video's length (D9), every
question carries the ``timestamp`` of the sentence that answers it, and a
question the hero alone answers is forbidden. These tests pin the rendered
prompt, the question model's stamp parsing and the enrichment's past-the-end
filter. The LLM is never called.
"""

from __future__ import annotations

import json
import math
import re
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.models.memory_types import MemoryResult
from src.models.pipeline_types import EnrichmentData, QuizQuestion, quiz_timestamp_seconds
from src.services.pipeline import enrichment as enrichment_mod
from src.services.pipeline.enrichment import (
    EMPTY_HERO,
    HERO_TAKEAWAYS_MAX,
    QUIZ_TIMESTAMP_TOLERANCE_S,
    build_quiz_prompt,
    enrich_quiz,
    parse_quiz,
    quiz_count_bands,
    quiz_question_count,
    render_hero,
)
from src.shared_config.domain_config import data_source

_PATCH_LLM = "src.services.pipeline.enrichment.call_llm_with_retry"
_QUIZ_CAP = (data_source("enrichment.quiz") or {"cap": 0})["cap"]
_PLACEHOLDER_RE = re.compile(r"\{[a-z_]+\}")
_TEN_MIN, _THIRTY_MIN, _HOUR_PLUS = 600, 1800, 3780
_EXTRACTION = {"learning": {"keyIdeas": [{"claim": "Pay debt first", "timestamp": 312}]}}
_VIDEO_MEMORY = "<video_memory>\ndomains: learning · goal: Pay off debt\n</video_memory>"
_HERO = "title: Debt first\ntldr: Pay the card before you invest.\n- A 24% APR beats a 7% fund"


def _question(n: int = 0, **overrides: Any) -> dict[str, Any]:
    item: dict[str, Any] = {
        "question": f"Why does paying the card beat investing the same dollar (#{n})?",
        "options": [
            "A 24% APR costs more than a 7% fund returns",
            "Card debt is forgiven after a year",
            "Index funds never return 7%",
            "The card issuer matches payments",
        ],
        "correctIndex": 0,
        "explanation": "At (5:12) the speaker says every dollar on the card loses 24 cents a year.",
    }
    item.update(overrides)
    return item


def _prompt(duration_s: float | None, hero: str = "") -> str:
    prompt = build_quiz_prompt(
        "learning", _EXTRACTION, _VIDEO_MEMORY, [], duration_s=duration_s, hero=hero
    )
    assert prompt is not None
    return prompt


def _llm_reply(*questions: dict[str, Any]) -> str:
    return json.dumps({"quiz": list(questions)})


async def _enrich(
    reply: str | None, duration_s: float | None, hero: str = ""
) -> tuple[EnrichmentData | None, AsyncMock]:
    llm = AsyncMock(return_value=reply)
    with patch(_PATCH_LLM, new=llm):
        result = await enrich_quiz(
            MagicMock(),
            primary_tag="learning",
            extraction_data=_EXTRACTION,
            video_memory=_VIDEO_MEMORY,
            tabs=[],
            duration_s=duration_s,
            hero=hero,
        )
    return result, llm


# ─── Count by duration ───


class TestQuizQuestionCount:
    @pytest.mark.parametrize(
        ("duration_s", "expected"),
        [
            (None, 3),
            (0, 3),
            (math.nan, 3),
            (_TEN_MIN, 3),
            (15 * 60, 3),
            (15 * 60 + 1, 5),
            (_THIRTY_MIN, 5),
            (45 * 60, 5),
            (45 * 60 + 1, 8),
            (_HOUR_PLUS, 8),
            (3 * 3600, 8),
        ],
    )
    def test_should_size_the_quiz_to_the_video_when_the_cap_allows(
        self, duration_s: float | None, expected: int
    ) -> None:
        assert quiz_question_count(duration_s, cap=8) == expected

    def test_should_never_exceed_the_cap_when_the_band_is_larger(self) -> None:
        assert quiz_question_count(_HOUR_PLUS, cap=5) == 5

    def test_should_keep_the_three_bands_of_d9(self) -> None:
        assert [count for _, count in quiz_count_bands()] == [3, 5, 8]


# ─── The rendered prompt ───


class TestPromptByDuration:
    @pytest.mark.parametrize(
        ("duration_s", "line"),
        [
            (_TEN_MIN, "write 3 questions"),
            (_THIRTY_MIN, "write 5 questions"),
            (_HOUR_PLUS, "write 8 questions"),
        ],
    )
    def test_should_ask_for_the_count_the_video_length_sets(
        self, duration_s: int, line: str
    ) -> None:
        assert line in _prompt(duration_s)

    @pytest.mark.parametrize(
        ("duration_s", "length"),
        [(_TEN_MIN, "10:00"), (_THIRTY_MIN, "30:00"), (_HOUR_PLUS, "1:03:00")],
    )
    def test_should_bound_every_timestamp_by_the_video_length(
        self, duration_s: int, length: str
    ) -> None:
        assert f"inside the video (0:00–{length})" in _prompt(duration_s)

    def test_should_say_the_length_is_unknown_when_none_is_given(self) -> None:
        assert "write 3 questions" in _prompt(None) and "(unknown)" in _prompt(None)

    def test_should_keep_the_registry_cap_as_the_ceiling(self) -> None:
        with patch.object(enrichment_mod, "_quiz_cap", return_value=5):
            assert "write 5 questions" in _prompt(_HOUR_PLUS)

    def test_should_ask_for_the_timestamp_of_the_sentence_that_answers(self) -> None:
        prompt = _prompt(_TEN_MIN)

        assert '"timestamp": the second the answer is spoken' in prompt
        assert "the sentence that settles the question" in prompt

    def test_should_forbid_hero_level_questions(self) -> None:
        assert "Never hero-level" in _prompt(_TEN_MIN)

    def test_should_forbid_dates_and_figures_the_video_does_not_state(self) -> None:
        assert "Nothing the video does not state: no year, date" in _prompt(_TEN_MIN)

    def test_should_ask_the_explanation_to_cite_the_same_time_in_parentheses(self) -> None:
        assert 'as (m:ss) in parentheses — the same time as "timestamp"' in _prompt(_TEN_MIN)

    def test_should_place_the_hero_block_verbatim(self) -> None:
        assert f"<hero>\n{_HERO}\n</hero>" in _prompt(_TEN_MIN, hero=_HERO)

    def test_should_say_not_available_without_a_hero(self) -> None:
        assert f"<hero>\n{EMPTY_HERO}\n</hero>" in _prompt(_TEN_MIN)

    def test_should_leave_no_placeholder_unfilled(self) -> None:
        assert not _PLACEHOLDER_RE.findall(_prompt(_HOUR_PLUS, hero=_HERO))

    def test_should_show_a_stamped_question_in_the_example(self) -> None:
        example = next(
            line for line in _prompt(_TEN_MIN).splitlines() if line.startswith('{"quiz"')
        )

        question = json.loads(example)["quiz"][0]

        assert question["timestamp"] == 272 and "(4:32)" in question["explanation"]


class TestRenderHero:
    def test_should_list_title_tldr_and_takeaways(self) -> None:
        memory = MemoryResult(tldr="Pay the card first.", takeaways=["24% beats 7%", "Then invest"])

        assert render_hero("Debt first", memory) == (
            "title: Debt first\ntldr: Pay the card first.\n- 24% beats 7%\n- Then invest"
        )

    def test_should_show_the_title_alone_when_memory_failed(self) -> None:
        assert render_hero("Debt first", None) == "title: Debt first"

    def test_should_be_empty_when_there_is_nothing_to_show(self) -> None:
        assert render_hero("", MemoryResult()) == ""

    def test_should_strip_template_and_tag_characters_from_memory_text(self) -> None:
        memory = MemoryResult(tldr="use {braces}", takeaways=["<b>bold</b>"])

        hero = render_hero("t", memory)

        assert "{" not in hero and "<" not in hero

    def test_should_cap_the_takeaways(self) -> None:
        memory = MemoryResult(takeaways=[f"takeaway {i}" for i in range(HERO_TAKEAWAYS_MAX + 3)])

        assert render_hero("t", memory).count("\n- ") == HERO_TAKEAWAYS_MAX


# ─── The question model ───


class TestQuizTimestampSeconds:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            ("4:32", 272),
            ("1:02:05", 3725),
            ("[4:32]", 272),
            ("at 12:08 he says so", 728),
            ("272", 272),
            (272, 272),
            (272.9, 272),
            (0, 0),
            (None, None),
            ("", None),
            ("soon", None),
            ("12:60", None),
            (True, None),
            (-5, None),
            (math.nan, None),
            (math.inf, None),
            ([272], None),
        ],
    )
    def test_should_read_seconds_or_a_clock_and_nothing_else(
        self, value: object, expected: int | None
    ) -> None:
        assert quiz_timestamp_seconds(value) == expected

    def test_should_parse_the_timestamp_on_a_question(self) -> None:
        assert QuizQuestion.model_validate(_question(timestamp="4:32")).timestamp == 272

    def test_should_keep_a_question_that_has_no_timestamp(self) -> None:
        question = QuizQuestion.model_validate(_question())

        assert question.timestamp is None

    def test_should_keep_a_question_whose_timestamp_is_unreadable(self) -> None:
        question = QuizQuestion.model_validate(_question(timestamp="later on"))

        assert question.timestamp is None

    def test_should_dump_the_timestamp_for_the_stored_row(self) -> None:
        question = QuizQuestion.model_validate(_question(timestamp=272))

        assert question.model_dump(by_alias=True)["timestamp"] == 272


# ─── The past-the-end filter ───


class TestParseQuizRange:
    def test_should_drop_a_question_stamped_past_the_video_end(self) -> None:
        past = _question(1, timestamp=_TEN_MIN + QUIZ_TIMESTAMP_TOLERANCE_S + 1)

        questions = parse_quiz({"quiz": [past, _question(2, timestamp=590)]}, 8, _TEN_MIN)

        assert [q.timestamp for q in questions] == [590]

    def test_should_keep_a_question_within_the_drift_slack(self) -> None:
        near = _question(1, timestamp=_TEN_MIN + QUIZ_TIMESTAMP_TOLERANCE_S)

        assert len(parse_quiz({"quiz": [near]}, 8, _TEN_MIN)) == 1

    def test_should_keep_a_question_stamped_at_the_end(self) -> None:
        assert len(parse_quiz({"quiz": [_question(1, timestamp=_TEN_MIN)]}, 8, _TEN_MIN)) == 1

    def test_should_keep_a_question_without_a_timestamp(self) -> None:
        assert len(parse_quiz({"quiz": [_question(1)]}, 8, _TEN_MIN)) == 1

    def test_should_drop_nothing_when_the_duration_is_unknown(self) -> None:
        assert len(parse_quiz({"quiz": [_question(1, timestamp=99_999)]}, 8, None)) == 1

    def test_should_let_a_later_valid_question_fill_the_dropped_ones_slot(self) -> None:
        items = [
            _question(1, timestamp=9_000),
            _question(2, timestamp=100),
            _question(3, timestamp=200),
        ]

        questions = parse_quiz({"quiz": items}, 2, _TEN_MIN)

        assert [q.timestamp for q in questions] == [100, 200]


# ─── The stage ───


class TestEnrichQuizSized:
    @pytest.mark.parametrize(
        ("duration_s", "expected"),
        [(_TEN_MIN, 3), (_THIRTY_MIN, 5), (_HOUR_PLUS, min(8, _QUIZ_CAP))],
    )
    async def test_should_keep_the_count_the_video_length_sets(
        self, duration_s: int, expected: int
    ) -> None:
        reply = _llm_reply(*(_question(i, timestamp=30 * i) for i in range(1, 11)))

        result, _ = await _enrich(reply, duration_s)

        assert result is not None and len(result.quiz) == expected

    async def test_should_hand_the_count_and_the_hero_to_the_prompt(self) -> None:
        _, llm = await _enrich(_llm_reply(_question(1), _question(2)), _TEN_MIN, hero=_HERO)

        assert llm.await_args is not None
        prompt = llm.await_args.args[1]

        assert "write 3 questions" in prompt and _HERO in prompt

    async def test_should_return_none_when_the_past_the_end_drop_leaves_one(self) -> None:
        reply = _llm_reply(_question(1, timestamp=100), _question(2, timestamp=9_000))

        result, _ = await _enrich(reply, _TEN_MIN)

        assert result is None

    async def test_should_carry_every_timestamp_into_the_enrichment_data(self) -> None:
        reply = _llm_reply(_question(1, timestamp="1:40"), _question(2, timestamp=200))

        result, _ = await _enrich(reply, _TEN_MIN)

        assert result is not None and [q.timestamp for q in result.quiz] == [100, 200]
