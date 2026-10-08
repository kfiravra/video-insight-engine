"""The quiz tab is always the last tab (quizPolicy.position, pipeline-1min 1d.2)."""

from __future__ import annotations

from unittest.mock import patch

from src.services.pipeline.assembly import assemble_response
from src.services.pipeline.assembly import core as assembly_core
from src.services.pipeline.assembly.core import _move_quiz_tabs_last

_QUESTIONS = [
    {"question": f"Q{i}?", "options": ["a", "b", "c", "d"], "correctIndex": 1, "explanation": "b"}
    for i in range(3)
]


def _tabs(*components: str) -> list[dict]:
    return [{"id": f"t{i}", "component": c} for i, c in enumerate(components)]


def _snippet(code: str) -> dict:
    return {"filename": f"{code}.py", "language": "python", "code": code, "explanation": "demo"}


class TestMoveQuizTabsLast:
    def test_should_move_the_quiz_after_every_other_tab(self) -> None:
        tabs = _tabs("overview", "quiz_arena", "info_grid", "video_filmstrip")

        _move_quiz_tabs_last(tabs)

        assert [t["component"] for t in tabs] == [
            "overview",
            "info_grid",
            "video_filmstrip",
            "quiz_arena",
        ]

    def test_should_keep_the_order_of_the_other_tabs(self) -> None:
        tabs = _tabs("overview", "quiz_arena", "code_playground", "info_grid")

        _move_quiz_tabs_last(tabs)

        assert [t["id"] for t in tabs] == ["t0", "t2", "t3", "t1"]

    def test_should_leave_tabs_alone_when_there_is_no_quiz(self) -> None:
        tabs = _tabs("overview", "info_grid", "checklist")

        _move_quiz_tabs_last(tabs)

        assert [t["id"] for t in tabs] == ["t0", "t1", "t2"]

    def test_should_leave_tabs_alone_when_the_policy_does_not_say_last(self) -> None:
        tabs = _tabs("overview", "quiz_arena", "info_grid")
        policy = {
            "position": "first",
            "requiresEvidence": "is_learnable",
            "attachmentHostsExclude": [],
        }

        with patch.object(assembly_core, "quiz_policy", return_value=policy):
            _move_quiz_tabs_last(tabs)

        assert [t["id"] for t in tabs] == ["t0", "t1", "t2"]


class TestQuizLastInAssembly:
    def test_should_end_with_the_quiz_when_a_backfilled_tab_is_appended_after_it(self) -> None:
        triage = {
            "contentTags": ["tech"],
            "primaryTag": "tech",
            "tabs": [
                {"id": "quiz", "label": "Quiz", "component": "quiz_arena",
                 "dataSource": "enrichment.quiz", "goal": "Check it"},
            ],
        }  # fmt: skip
        extraction = {"tech": {"snippets": [_snippet("x = 1"), _snippet("y = 2")]}}

        result = assemble_response(triage, extraction, {"quiz": _QUESTIONS}, None)

        components = [t["component"] for t in result["tabs"]]
        assert "code_playground" in components
        assert components[-1] == "quiz_arena"
