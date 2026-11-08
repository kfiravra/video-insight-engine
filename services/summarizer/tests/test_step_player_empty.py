"""A step_player never ships a step without text (step-player-empty hotfix, 2026-11-05).

nz4F6hM7S9E (dev, Hebrew tech video): the plan put "Build It Locally" on
``project.steps``; plan-parse reconcile re-pointed it to ``tech.snippets`` (project was
not tagged), and the step adapter read none of a snippet's keys, so all six steps
shipped with ``instruction: ""`` and only their timestamps. The adapter now reads a
snippet's ``explanation``; a step list with no text under any key drops as
``empty_steps`` (logged, in ``droppedTabs``) and is never demoted.
"""

from __future__ import annotations

import logging
from typing import Any

import pytest

from src.services.pipeline.assembly import assemble_response, assemble_step_player
from src.services.pipeline.assembly.normalizers import step_text
from src.services.pipeline.assembly.step_guard import EMPTY_STEPS, empty_steps_drop
from src.services.pipeline.plan_reconcile import reconcile_plan_tabs

# The first two tech.snippets items of the nz4F6hM7S9E run, as stored (explanations cut).
_SNIPPETS: list[dict[str, Any]] = [
    {
        "filename": None,
        "language": "text",
        "code": "אחי קלוד היקר תתקין לי בבקשה את התוסף הזה של הייפרפריימס",
        "explanation": "Step 1 — Open a terminal, run claude, and prompt it to install "
        "the HyperFrames plugin.",
        "timestamp": 381,
    },
    {
        "filename": None,
        "language": "text",
        "code": "Node.js\nFFmpeg",
        "explanation": "Step 2 — The two things the HyperFrames engine needs on your "
        "machine: Node.js, and FFmpeg.",
        "timestamp": 420,
    },
]
_META = {"duration": 786, "outline": [], "chapters": []}


def _code_only(snippets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{k: v for k, v in s.items() if k != "explanation"} for s in snippets]


def _tab(tab_id: str, component: str, source: str) -> dict[str, Any]:
    return {"id": tab_id, "label": tab_id, "component": component, "dataSource": source,
            "goal": "After this you can set it up."}  # fmt: skip


def _tech(tab: dict[str, Any], snippets: list[dict[str, Any]]) -> dict[str, Any]:
    triage = {"primaryTag": "tech", "contentTags": ["tech", "learning"], "tabs": [tab]}
    return assemble_response(triage, {"tech": {"snippets": snippets}}, None, None, video_meta=_META)


def _shipped(result: dict[str, Any], tab_id: str) -> dict[str, Any] | None:
    return next((t for t in result["tabs"] if t["id"] == tab_id), None)


# ─── The route ───


class TestTheRoute:
    def test_should_repoint_a_project_step_tab_to_the_snippets_on_a_tech_video(self) -> None:
        """The path that fed step_player a snippet list (pins the registry order)."""
        kept, _, _ = reconcile_plan_tabs(
            [_tab("local_setup", "step_player", "project.steps")], ["tech", "learning"], []
        )

        assert kept[0]["dataSource"] == "tech.snippets"


# ─── The step's text ───


class TestStepText:
    def test_should_read_a_snippet_explanation_as_the_step_text(self) -> None:
        assert step_text(_SNIPPETS[0]).startswith("Step 1 — Open a terminal")

    def test_should_prefer_the_instruction_over_the_explanation(self) -> None:
        assert step_text({"instruction": "Run it.", "explanation": "Longer prose."}) == "Run it."

    def test_should_fall_through_a_blank_instruction(self) -> None:
        assert step_text({"instruction": "  ", "explanation": "Run it."}) == "Run it."

    def test_should_return_nothing_for_a_step_without_text(self) -> None:
        assert step_text(_code_only(_SNIPPETS)[0]) == ""


# ─── The assembler ───


class TestAssembleStepPlayer:
    def test_should_ship_the_snippet_explanations_as_steps(self) -> None:
        props = assemble_step_player({}, _SNIPPETS, {}, None)

        assert props is not None
        assert [(s["number"], s["instruction"][:6], s["timestamp"]) for s in props["steps"]] == [
            (1, "Step 1", 381),
            (2, "Step 2", 420),
        ]

    def test_should_skip_a_step_without_text_and_number_the_rest(self) -> None:
        data = [_SNIPPETS[0], _code_only(_SNIPPETS)[1], _SNIPPETS[1]]

        props = assemble_step_player({}, data, {}, None)

        assert props is not None
        assert [(s["number"], s["timestamp"]) for s in props["steps"]] == [(1, 381), (2, 420)]

    def test_should_return_none_when_no_step_has_text(self) -> None:
        assert assemble_step_player({}, _code_only(_SNIPPETS), {}, None) is None

    def test_should_still_coerce_bare_string_commands(self) -> None:
        props = assemble_step_player({}, ["docker build .", "  ", "docker run app"], {}, None)

        assert props is not None
        assert [(s["number"], s["instruction"]) for s in props["steps"]] == [
            (1, "docker build ."),
            (2, "docker run app"),
        ]


# ─── The guard ───


class TestEmptyStepsDrop:
    @pytest.mark.parametrize("component", ["step_player", "step_flow_canvas"])
    def test_should_name_the_drop_for_a_step_list_without_text(self, component: str) -> None:
        drop = empty_steps_drop("local_setup", component, "tech.snippets", _code_only(_SNIPPETS))

        assert drop == (EMPTY_STEPS, "2 steps without text")

    def test_should_leave_a_list_with_one_step_of_text(self) -> None:
        data = [_SNIPPETS[0], *_code_only(_SNIPPETS)]

        assert empty_steps_drop("local_setup", "step_player", "tech.snippets", data) is None

    @pytest.mark.parametrize(
        ("component", "data"),
        [("checklist", [{"code": "x"}]), ("step_player", []), ("step_player", {"code": "x"})],
    )
    def test_should_leave_anything_else(self, component: str, data: Any) -> None:
        assert empty_steps_drop("t", component, "tech.snippets", data) is None


class TestAssembly:
    def test_should_ship_the_hebrew_run_steps_with_their_text(self) -> None:
        result = _tech(_tab("local_setup", "step_player", "tech.snippets"), _SNIPPETS)
        tab = _shipped(result, "local_setup")

        assert tab is not None and tab["component"] == "step_player"
        assert all(s["instruction"].strip() for s in tab["props"]["steps"])

    def test_should_drop_a_step_tab_without_text_as_empty_steps(self) -> None:
        result = _tech(_tab("local_setup", "step_player", "tech.snippets"), _code_only(_SNIPPETS))

        assert _shipped(result, "local_setup") is None
        assert {
            "id": "local_setup",
            "component": "step_player",
            "dataSource": "tech.snippets",
            "reason": EMPTY_STEPS,
            "detail": "2 steps without text",
        } in result["dropped"]

    def test_should_not_demote_a_step_tab_without_text(self) -> None:
        result = _tech(_tab("local_setup", "step_player", "tech.snippets"), _code_only(_SNIPPETS))

        assert not [t for t in result["tabs"] if t.get("degradedFrom") == "step_player"]

    def test_should_log_the_drop(self, caplog: pytest.LogCaptureFixture) -> None:
        with caplog.at_level(logging.WARNING):
            _tech(_tab("local_setup", "step_player", "tech.snippets"), _code_only(_SNIPPETS))

        drops = [r for r in caplog.records if EMPTY_STEPS in r.getMessage()]
        assert [(r.levelno, "id='local_setup'" in r.getMessage()) for r in drops] == [
            (logging.WARNING, True)
        ]
