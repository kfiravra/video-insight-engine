"""Reconcile at plan-done and the frames-done screen-code check (pipeline-1min 3.2).

Appendix C + Kfir's gate-2 decision 7: a key is false only when BOTH readers
answered false; ``has_code`` tabs doubly false in the transcript are decided
at frames-done on what the screen shows.
"""

from __future__ import annotations

from typing import Any

import pytest

from src.models.memory_types import MemoryResult
from src.models.pipeline_types import PlanResult
from src.services.pipeline.reconcile import (
    ReconcileOutcome,
    doubly_false_view,
    reconcile_at_plan_done,
    reconcile_plan,
    resolve_deferred,
    screen_shows_code,
)

_PLAN = {"t": True, "f": False, "∅": None}
_MEMORY = {"t": True, "f": False, "∅": None, "None": None}
# plan × memory answers for one key: true / false / silent (∅), memory also failed (None).
_COMBINATIONS = [(p, m) for p in _PLAN for m in _MEMORY]
_IDS = [f"plan={p}-memory={m}" for p, m in _COMBINATIONS]


def _tab(tab_id: str, component: str, data_source: str) -> dict[str, Any]:
    return {"id": tab_id, "label": tab_id, "component": component, "dataSource": data_source}


def _plan(
    primary: str, tabs: list[dict], evidence: dict | None = None, tags: list[str] | None = None
) -> PlanResult:
    return PlanResult.model_validate(
        {
            "contentTags": tags or [primary],
            "primaryTag": primary,
            "confidence": 0.9,
            "evidence": evidence or {},
            "tabs": tabs,
        }
    )


def _answers(key: str, answer: bool | None) -> dict[str, bool]:
    return {} if answer is None else {key: answer}


def _readers(key: str, p: str, m: str) -> tuple[dict[str, bool], MemoryResult | None]:
    memory = None if m == "None" else MemoryResult(evidence=_answers(key, _MEMORY[m]))
    return _answers(key, _PLAN[p]), memory


def _action(outcome: ReconcileOutcome | PlanResult, tab_id: str) -> str | None:
    records = outcome.records if isinstance(outcome, ReconcileOutcome) else outcome.reconcile
    return next((r["action"] for r in records if r["tabId"] == tab_id), None)


def _ids(tabs: list[dict]) -> list[str]:
    return [tab["id"] for tab in tabs]


# ─── The doubly-false rule, table-driven ───


def _travel_tabs() -> list[dict]:
    return [
        _tab("itinerary", "spot_explorer", "travel.itinerary"),
        _tab("budget", "budget", "travel.budget"),
        _tab("packing", "packing_mission", "travel.packingList"),
        _tab("stops", "moment_track", "travel.itinerary"),
    ]


def _food_tabs_without_checklist() -> list[dict]:
    return [
        _tab("steps", "step_player", "food.steps"),
        _tab("tips", "info_grid", "food.tips"),
        _tab("moments", "moment_track", "food.steps"),
    ]


def _learning_tabs_with_quiz() -> list[dict]:
    return [
        _tab("concepts", "concept_canvas", "learning.concepts"),
        _tab("quiz", "quiz_arena", "enrichment.quiz"),
        _tab("takeaways", "checklist", "learning.takeaways"),
        _tab("moments", "moment_track", "learning.timestamps"),
    ]


class TestDoublyFalseTable:
    @pytest.mark.parametrize(("p", "m"), _COMBINATIONS, ids=_IDS)
    def test_should_drop_an_evidence_gated_tab_only_when_both_readers_say_false(self, p, m):
        plan_ev, memory = _readers("has_packing", p, m)

        outcome = reconcile_at_plan_done(_plan("travel", _travel_tabs(), plan_ev), memory, None)

        assert _action(outcome, "packing") == ("dropped" if (p, m) == ("f", "f") else None)

    @pytest.mark.parametrize(("p", "m"), _COMBINATIONS, ids=_IDS)
    def test_should_backfill_a_required_component_unless_both_readers_say_false(self, p, m):
        plan_ev, memory = _readers("has_ingredients", p, m)
        plan = _plan("food", _food_tabs_without_checklist(), plan_ev)

        outcome = reconcile_at_plan_done(plan, memory, None)

        assert _action(outcome, "ingredients") == (None if (p, m) == ("f", "f") else "backfilled")

    @pytest.mark.parametrize(("p", "m"), _COMBINATIONS, ids=_IDS)
    def test_should_drop_the_quiz_only_when_both_readers_say_not_learnable(self, p, m):
        plan_ev, memory = _readers("is_learnable", p, m)
        plan = _plan("learning", _learning_tabs_with_quiz(), plan_ev)

        outcome = reconcile_at_plan_done(plan, memory, None)

        assert _action(outcome, "quiz") == ("dropped" if (p, m) == ("f", "f") else "moved_last")


class TestEvidenceView:
    def test_should_be_true_when_either_reader_says_true(self):
        assert doubly_false_view({"has_code": False}, {"has_code": True}) == {"has_code": True}

    def test_should_be_false_only_when_both_readers_say_false(self):
        assert doubly_false_view({"has_code": False}, {"has_code": False}) == {"has_code": False}

    def test_should_have_no_opinion_when_memory_is_silent(self):
        assert doubly_false_view({"has_code": False}, {}) == {}

    def test_should_have_no_opinion_when_memory_failed(self):
        assert doubly_false_view({"has_code": False}, None) == {}


# ─── Code evidence (Kfir's decision 7): T1dQhQAm8Tc's workflow tab ───

_NO_CODE = {"has_code": False, "is_learnable": True}


def _frame(index: int, scene_type: str, text: str = "", value: str | None = "shown") -> dict:
    """One vision description, as ``ctx.frame_descriptions`` holds it."""
    return {
        "original_index": index,
        "timestamp_sec": 10.0 * index,
        "scene_type": scene_type,
        "content": f"{scene_type} frame",
        "text_visible": text,
        "educational_value": value,
    }


_CODE_FRAME = _frame(4, "code")
_SLIDE = _frame(2, "slide", "Effect makes TypeScript errors typed")
_TALKING_HEAD = _frame(1, "talking_head", value=None)


def _t1dq_tabs(*, with_stack: bool = True) -> list[dict]:
    """T1dQhQAm8Tc as prod planned it (2026-10-12): a workflow tab on a non-code talk."""
    stack = [_tab("stack_overview", "info_grid", "tech.topics")] if with_stack else []
    return [
        *stack,
        _tab("workflow", "step_player", "tech.setup.commands"),
        _tab("concepts", "flash_deck", "learning.concepts"),
        _tab("quiz", "quiz_arena", "enrichment.quiz"),
    ]


def _t1dq(memory_code: bool | None = False, *, with_stack: bool = True) -> PlanResult:
    plan = _plan("tech", _t1dq_tabs(with_stack=with_stack), _NO_CODE, ["tech", "learning"])
    memory = MemoryResult(evidence=_answers("has_code", memory_code))
    return reconcile_plan(plan, memory, None)


def _ocr(text: str) -> dict[str, Any]:
    return {"index": 7, "timestamp": 90.0, "ocr_text": text}


class TestCodeEvidence:
    def test_should_defer_code_paths_at_plan_done(self):
        plan = _t1dq()

        assert (plan.deferred_tabs, "workflow" in _ids(plan.tabs)) == (["workflow"], True)

    def test_should_not_defer_when_either_reader_saw_code(self):
        assert _t1dq(memory_code=True).deferred_tabs == []

    def test_should_drop_t1dq_workflow_when_has_code_is_doubly_false_and_no_frame_shows_code(self):
        plan = resolve_deferred(_t1dq(), [_TALKING_HEAD, _SLIDE], [], None)

        assert _ids(plan.tabs) == ["stack_overview", "concepts", "quiz"]

    def test_should_list_the_dropped_workflow_tab_in_dropped_tabs(self):
        plan = resolve_deferred(_t1dq(), [], [], None)

        assert plan.dropped_tabs[-1]["reason"] == "reconcile_evidence_false"

    def test_should_keep_the_workflow_tab_when_a_frames_scene_type_is_code(self):
        plan = resolve_deferred(_t1dq(), [_TALKING_HEAD, _CODE_FRAME], [], None)

        assert (plan.reconcile[-1]["action"], "workflow" in _ids(plan.tabs)) == (
            "kept_screen_code",
            True,
        )

    def test_should_keep_the_workflow_tab_when_ocr_shows_a_shell_command(self):
        plan = resolve_deferred(_t1dq(), [], [_ocr("$ npm install effect@4.0")], None)

        assert "workflow" in _ids(plan.tabs)

    def test_should_mark_has_code_true_when_the_screen_shows_code(self):
        plan = resolve_deferred(_t1dq(), [_CODE_FRAME], [], None)

        assert plan.evidence["has_code"] is True

    def test_should_decide_on_the_transcript_alone_when_vision_is_off(self):
        plan = resolve_deferred(_t1dq(), [], [], None)

        assert "workflow" not in _ids(plan.tabs)

    def test_should_record_the_frames_done_decision_with_the_screen_answer(self):
        plan = resolve_deferred(_t1dq(), [], [], None)

        assert (plan.reconcile[-1]["stage"], plan.reconcile[-1]["evidence"]) == (
            "frames_done",
            {"key": "has_code", "screen": False},
        )

    def test_should_clear_the_deferral_once_decided(self):
        assert resolve_deferred(_t1dq(), [_CODE_FRAME], [], None).deferred_tabs == []

    def test_should_leave_the_plan_untouched_when_nothing_was_deferred(self):
        plan = _t1dq(memory_code=True)

        assert resolve_deferred(plan, [], [], None) is plan


_CODE_SHAPED = [
    "$ npm install effect",
    "pip install litellm",
    "def handler(event):",
    "const app = express()",
    "import { Effect } from 'effect'",
    "items.map(x => x.id)",
]
_CODE_SHAPED += [
    "curl -X POST https://api.example.com/v1",
    "git clone https://github.com/x/y",
    "npx create-react-app my-app",
    "npm i effect@4.0",
    "docker run -p 80:80 nginx",
    "function main() {",
    "onClick={() => setOpen(true)}",
]
_PROSE = [
    "Effect makes TypeScript errors typed",
    "Import duties rose in 2024",
    "Let's talk about agents",
    "Loss function (MSE)",
    "Objective function (cost)",
    "$ 500 per month",
    "curl up with a book",
]


class TestScreenShowsCode:
    @pytest.mark.parametrize("text", _CODE_SHAPED)
    def test_should_see_code_when_ocr_text_is_code_shaped(self, text):
        assert screen_shows_code([], [_ocr(text)])

    @pytest.mark.parametrize("text", _PROSE)
    def test_should_not_see_code_when_on_screen_text_is_prose(self, text):
        assert not screen_shows_code([], [_ocr(text)])

    def test_should_see_code_when_vision_says_the_scene_is_code(self):
        assert screen_shows_code([_CODE_FRAME], [])

    def test_should_read_visions_transcription_of_the_screen(self):
        terminal = {**_SLIDE, "scene_type": "screen_recording", "text_visible": "git clone repo"}

        assert screen_shows_code([terminal], [])


# ─── Step 1 fall-back chain ───


def _gaming_tabs(third: dict) -> list[dict]:
    return [
        _tab("pulls", "tier_list", "gaming.rankings"),
        _tab("highlights", "moment_track", "gaming.highlights"),
        third,
    ]


def _not_ranked(tabs: list[dict]) -> ReconcileOutcome:
    plan = _plan("gaming", tabs, {"has_ranking": False})
    return reconcile_at_plan_done(plan, MemoryResult(evidence={"has_ranking": False}), None)


def _three_tab_learning_talk() -> PlanResult:
    """A code-free learning talk whose workflow tab can neither re-point nor demote."""
    tabs = [
        _tab("stack", "info_grid", "tech.topics"),
        _tab("workflow", "step_player", "tech.setup.commands"),
        _tab("concepts", "concept_canvas", "learning.concepts"),
    ]
    plan = _plan("learning", tabs, _NO_CODE, ["learning", "tech"])
    return reconcile_plan(plan, MemoryResult(evidence={"has_code": False}), None)


class TestFallBack:
    def test_should_repoint_to_a_same_component_source(self):
        plan = _plan(
            "food", [_tab("shop", "checklist", "food.ingredients")], {"has_ingredients": False}
        )

        outcome = reconcile_at_plan_done(
            plan, MemoryResult(evidence={"has_ingredients": False}), None
        )

        assert outcome.tabs[0]["dataSource"] == "food.equipment"

    def test_should_demote_onto_an_eligible_source_when_the_rung_exists(self):
        outcome = _not_ranked(_gaming_tabs(_tab("route", "step_flow_canvas", "gaming.walkthrough")))

        assert (outcome.tabs[0]["component"], outcome.tabs[0]["dataSource"]) == (
            "info_grid",
            "gaming.loadout",
        )

    def test_should_record_the_demotion_rung(self):
        outcome = _not_ranked(_gaming_tabs(_tab("route", "step_flow_canvas", "gaming.walkthrough")))

        assert (outcome.records[0]["action"], outcome.records[0]["toComponent"]) == (
            "demoted",
            "info_grid",
        )

    def test_should_drop_rather_than_duplicate_a_source_another_tab_shows(self):
        outcome = _not_ranked(_gaming_tabs(_tab("set_facts", "info_grid", "gaming.loadout")))

        assert _action(outcome, "pulls") == "dropped"

    def test_should_demote_t1dq_workflow_onto_topics_when_no_tab_shows_them(self):
        plan = resolve_deferred(_t1dq(with_stack=False), [], [], None)

        assert (plan.tabs[0]["id"], plan.tabs[0]["dataSource"]) == ("workflow", "tech.topics")

    def test_should_reset_the_brief_of_a_demoted_tab_to_its_time_ranges(self):
        pulls = {**_tab("pulls", "tier_list", "gaming.rankings"), "goal": "Rank every pull"}
        pulls["brief"] = {"what": "every pulled card", "where": ["1:10-2:40"], "expect": 12}
        third = _tab("route", "step_flow_canvas", "gaming.walkthrough")

        demoted = _not_ranked([pulls, *_gaming_tabs(third)[1:]]).tabs[0]

        assert demoted["brief"] == {"what": "", "where": ["1:10-2:40"], "expect": 0}

    def test_should_drop_the_goal_of_a_demoted_tab(self):
        pulls = {**_tab("pulls", "tier_list", "gaming.rankings"), "goal": "Rank every pull"}
        third = _tab("route", "step_flow_canvas", "gaming.walkthrough")

        assert "goal" not in _not_ranked([pulls, *_gaming_tabs(third)[1:]]).tabs[0]

    def test_should_label_a_demoted_tab_after_the_default_tab_of_its_new_source(self):
        outcome = _not_ranked(_gaming_tabs(_tab("route", "step_flow_canvas", "gaming.walkthrough")))

        assert (outcome.tabs[0]["id"], outcome.tabs[0]["label"]) == ("pulls", "Build / Loadout")

    def test_should_fill_to_three_when_a_frames_done_drop_leaves_two(self):
        resolved = resolve_deferred(_three_tab_learning_talk(), [], [], None)

        assert _ids(resolved.tabs) == ["stack", "concepts", "takeaways"]

    def test_should_record_the_frames_done_fill(self):
        resolved = resolve_deferred(_three_tab_learning_talk(), [], [], None)

        assert (resolved.reconcile[-1]["action"], resolved.reconcile[-1]["stage"]) == (
            "filled",
            "frames_done",
        )

    def test_should_keep_a_doubly_false_moment_tab_for_the_fallback_chain(self):
        tabs = [_tab("moments", "moment_track", "food.steps")]
        plan = _plan("food", tabs, {"has_steps": False})

        outcome = reconcile_at_plan_done(plan, MemoryResult(evidence={"has_steps": False}), None)

        assert _action(outcome, "moments") == "kept_for_moment_fallback"


# ─── pipeline.reconcile ───


class TestRecords:
    def test_should_record_stage_and_evidence_in_pipeline_reconcile(self):
        assert _t1dq().reconcile == [
            {
                "tabId": "workflow",
                "component": "step_player",
                "from": "tech.setup.commands",
                "to": None,
                "reason": "evidence_false",
                "action": "deferred_to_frames",
                "stage": "plan_done",
                "evidence": {"key": "has_code", "plan": False, "memory": False},
            }
        ]

    def test_should_append_plan_done_records_after_the_parse_records(self):
        parse_row = {"tabId": "x", "action": "dropped", "stage": "plan_parse"}
        plan = _plan("tech", _t1dq_tabs(), _NO_CODE, ["tech", "learning"])
        plan = plan.model_copy(update={"reconcile": [parse_row]})

        reconciled = reconcile_plan(plan, MemoryResult(evidence={"has_code": False}), None)

        assert [r["stage"] for r in reconciled.reconcile] == ["plan_parse", "plan_done"]

    def test_should_keep_the_readers_own_answers(self):
        plan = _t1dq()

        assert (plan.plan_evidence, plan.memory_evidence) == (_NO_CODE, {"has_code": False})

    def test_should_keep_no_memory_answers_when_memory_failed(self):
        plan = _plan("tech", _t1dq_tabs(), _NO_CODE, ["tech", "learning"])

        assert reconcile_plan(plan, None, None).memory_evidence is None

    def test_should_change_nothing_on_the_recorded_t1dq_plan_when_no_reader_answered(self):
        """The replay cassettes' premise: pre-1b plans and synthetic memory carry no evidence."""
        tabs = [
            _tab("stack_explorer", "spot_explorer", "tech.topics"),
            _tab("code_snippets", "code_playground", "tech.snippets"),
            _tab("key_moments", "moment_track", "learning.timestamps"),
            _tab("concepts", "concept_canvas", "learning.concepts"),
            _tab("quiz", "quiz_arena", "enrichment.quiz"),
        ]
        plan = _plan("tech", tabs, {}, ["tech", "learning"])

        outcome = reconcile_at_plan_done(plan, MemoryResult(), None)

        assert (outcome.records, outcome.tabs) == ([], plan.tabs)
