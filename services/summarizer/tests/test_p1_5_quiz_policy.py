"""p1.5 — ``quizPolicy`` (D9, R12, L30): where a quiz may be, how big, untimed, last.

A quiz is allowed on ``is_learnable`` for podcast and news too; it gets 3 / 5 / 8
questions by the video's length (``quizPolicy.questionsByDuration``, the single
source the quiz call reads); ``timer`` is false (the web registry passes it on);
it is always the last tab. Assembly carries each question's ``timestamp`` (the
second its answer is said, task 1.13's field) into ``quiz_arena`` and the
``quick_quiz`` strip.
"""

from __future__ import annotations

import math

import pytest

from src.models.memory_types import MemoryResult
from src.models.pipeline_types import EnrichmentData, PlanResult
from src.services.pipeline.assembly import assemble_response
from src.services.pipeline.assembly.assemblers_overhaul import assemble_quiz_arena
from src.services.pipeline.assembly.assemblers_secondary import assemble_quick_quiz
from src.services.pipeline.enrichment import (
    needs_quiz,
    quiz_allowed,
    quiz_count_bands,
    quiz_question_count,
)
from src.services.pipeline.reconcile import reconcile_at_plan_done
from src.shared_config.domain_config import quiz_policy

_QUESTIONS = [
    {
        "question": "Why did 37signals put pencils down?",
        "options": ["Cost", "Agents"],
        "correctIndex": 1,
        "explanation": "Agents write the code (21:02).",
        "timestamp": 1262,
    },
    {
        "question": "What is P-bloom?",
        "options": ["Optimism", "Doom"],
        "correctIndex": 0,
        "explanation": "Optimism over doom (58:26).",
        "timestamp": None,
    },
]


def _tab(tab_id: str, component: str, source: str) -> dict:
    return {"id": tab_id, "label": tab_id, "component": component, "dataSource": source}


def _plan(primary: str, tabs: list[dict], evidence: dict | None = None) -> PlanResult:
    return PlanResult.model_validate(
        {
            "contentTags": [primary, "learning"],
            "primaryTag": primary,
            "confidence": 0.9,
            "evidence": evidence or {},
            "tabs": tabs,
        }
    )


# ─── The policy in the registry ───


class TestPolicy:
    def test_should_keep_the_quiz_untimed(self) -> None:
        assert quiz_policy()["timer"] is False

    def test_should_keep_the_quiz_last(self) -> None:
        assert quiz_policy()["position"] == "last"

    def test_should_read_three_bands_of_d9_from_the_registry(self) -> None:
        assert quiz_count_bands() == ((15 * 60, 3), (45 * 60, 5), (math.inf, 8))

    @pytest.mark.parametrize(
        ("minutes", "count"), [(10, 3), (15, 3), (16, 5), (45, 5), (63, 8), (140, 8)]
    )
    def test_should_size_the_quiz_by_the_videos_length(self, minutes: int, count: int) -> None:
        assert quiz_question_count(minutes * 60, cap=8) == count

    def test_should_never_ask_for_more_than_the_registry_cap(self) -> None:
        assert quiz_question_count(63 * 60, cap=5) == 5


# ─── Where a quiz may be ───


class TestWhereAQuizMayBe:
    @pytest.mark.parametrize("domain", ["podcast", "news", "learning", "tech"])
    def test_should_allow_a_quiz_when_the_domain_is_learnable(self, domain: str) -> None:
        assert quiz_allowed(domain)

    @pytest.mark.parametrize("domain", ["review", "food", "gaming", "travel"])
    def test_should_not_allow_a_quiz_when_the_domain_is_entertainment(self, domain: str) -> None:
        assert not quiz_allowed(domain)

    def test_should_call_the_quiz_when_a_podcast_plans_one(self) -> None:
        plan = _plan("podcast", [_tab("quiz", "quiz_arena", "enrichment.quiz")])

        assert needs_quiz(plan, "podcast")

    def test_should_keep_a_lecture_quiz_and_move_it_last_at_plan_done(self) -> None:
        tabs = [
            _tab("quiz", "quiz_arena", "enrichment.quiz"),
            _tab("map", "moment_track", "learning.timestamps"),
            _tab("ideas", "spot_explorer", "learning.keyIdeas"),
        ]

        outcome = reconcile_at_plan_done(_plan("learning", tabs), None, "lecture")

        assert [t["id"] for t in outcome.tabs][-1] == "quiz"

    def test_should_drop_a_news_quiz_when_both_readers_deny_learnable(self) -> None:
        tabs = [
            _tab("claims", "claims_tracker", "news.claims"),
            _tab("quiz", "quiz_arena", "enrichment.quiz"),
        ]
        memory = MemoryResult(evidence={"is_learnable": False})

        outcome = reconcile_at_plan_done(_plan("news", tabs, {"is_learnable": False}), memory, None)

        assert "quiz" not in [t["id"] for t in outcome.tabs]


# ─── Assembly carries the answer's timestamp ───


class TestQuizTimestamps:
    def test_should_carry_each_questions_timestamp_into_the_arena(self) -> None:
        props = assemble_quiz_arena({}, _QUESTIONS, {}, None)

        assert props is not None
        assert [q.get("timestamp") for q in props["questions"]] == [1262, None]

    def test_should_carry_the_timestamp_into_the_quick_quiz_strip(self) -> None:
        props = assemble_quick_quiz({}, _QUESTIONS[:1], {}, None)

        assert props is not None and props["questions"][0]["timestamp"] == 1262

    def test_should_ship_the_validated_quiz_last_with_its_stamps(self) -> None:
        enrichment = EnrichmentData.model_validate({"quiz": _QUESTIONS}).model_dump(by_alias=True)
        triage = {
            "primaryTag": "podcast",
            "contentTags": ["podcast", "learning"],
            "tabs": [
                _tab("quiz", "quiz_arena", "enrichment.quiz"),
                _tab("map", "moment_track", "podcast.segments"),
            ],
        }
        segments = [
            {
                "title": f"Part {i}",
                "summary": f"What part {i} concluded, in full.",
                "timestamp": 600 * i,
            }
            for i in range(1, 4)
        ]

        result = assemble_response(
            triage,
            {"podcast": {"segments": segments}},
            enrichment,
            None,
            video_meta={"duration": 4980},
        )
        last = result["tabs"][-1]

        assert (last["component"], last["props"]["questions"][0]["timestamp"]) == (
            "quiz_arena",
            1262,
        )
