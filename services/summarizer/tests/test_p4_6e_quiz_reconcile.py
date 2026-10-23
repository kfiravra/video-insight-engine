"""Reconcile drops a planned quiz on a format that carries none (output-quality 4.6e).

D9 revised (Kfir 2026-10-23): the quiz ships only on instructional formats
(``quizPolicy.formats``: lecture, tutorial). At plan-done a ``quiz_arena`` tab on a talk,
an interview, a podcast or the news leaves the plan, listed in ``droppedTabs`` as
``reconcile_quiz_format``; on a lecture or a tutorial — or with no probe format — it stays,
last.
"""

from __future__ import annotations

import pytest

from src.models.pipeline_types import PlanResult
from src.services.pipeline.reconcile import reconcile_at_plan_done
from src.services.pipeline.reconcile_pass import ReconcileOutcome

_QUIZ = {"id": "quiz", "label": "Quiz", "component": "quiz_arena", "dataSource": "enrichment.quiz"}
_TABS = [
    _QUIZ,
    {
        "id": "ideas",
        "label": "Ideas",
        "component": "spot_explorer",
        "dataSource": "learning.keyIdeas",
    },
    {"id": "map", "label": "Map", "component": "moment_track", "dataSource": "learning.timestamps"},
]


def _reconciled(content_format: str | None) -> ReconcileOutcome:
    plan = PlanResult.model_validate(
        {"contentTags": ["learning"], "primaryTag": "learning", "confidence": 0.9, "tabs": _TABS}
    )
    return reconcile_at_plan_done(plan, None, content_format)


def _ids(outcome: ReconcileOutcome) -> list[str]:
    return [tab["id"] for tab in outcome.tabs]


class TestQuizFormatAtPlanDone:
    @pytest.mark.parametrize("content_format", ["talk", "interview", "podcast", "news"])
    def test_should_drop_the_quiz_tab_when_the_format_is_not_instructional(
        self, content_format: str
    ) -> None:
        assert "quiz" not in _ids(_reconciled(content_format))

    def test_should_list_the_drop_as_reconcile_quiz_format_when_the_video_is_a_talk(
        self,
    ) -> None:
        dropped = [(d["id"], d["reason"]) for d in _reconciled("talk").dropped]

        assert dropped == [("quiz", "reconcile_quiz_format")]

    def test_should_record_the_drop_in_the_reconcile_rows_when_the_video_is_a_podcast(
        self,
    ) -> None:
        rows = [(r["tabId"], r["action"], r["reason"]) for r in _reconciled("podcast").records]

        assert ("quiz", "dropped", "quiz_format") in rows

    @pytest.mark.parametrize("content_format", ["lecture", "tutorial", None])
    def test_should_keep_the_quiz_last_when_the_format_may_carry_one(
        self, content_format: str | None
    ) -> None:
        assert _ids(_reconciled(content_format))[-1] == "quiz"
