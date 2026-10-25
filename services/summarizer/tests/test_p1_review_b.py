"""Phase-1 review fixes, lane B — the pipeline (review-p1-findings.md).

Each class pins one finding: the plan survives a malformed goal (R3-C1), the
goal rule's surface reads (R3-W8), the synthesis hero fallback written by
memory's rules (R8-W1), cut takeaway stamps logged (R8-W4), the quiz count's
tail band and unknown length (R6-W5, R6-W6), stampless questions logged
(R6-W7), the primary's moment source never read off the screen (R7-W3), fold
hosts outliving a trim and the fold at frames-done (R11-W4, R11-W7), and a
small first extraction slice absorbed (R12-W2). The model fixes are pinned in
``test_p1_review_b_models.py``. No LLM is called.
"""

from __future__ import annotations

import json
import logging
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.models.pipeline_types import PlanResult
from src.services.pipeline import enrichment as enrichment_mod
from src.services.pipeline import plan as plan_mod
from src.services.pipeline import plan_prompt
from src.services.pipeline import synthesis as synthesis_mod
from src.services.pipeline.assembly.info_grid_fold import FOLDS_KEY
from src.services.pipeline.enrichment import build_quiz_prompt, parse_quiz, quiz_question_count
from src.services.pipeline.field_groups import build_groups
from src.services.pipeline.field_requests import FieldRequest
from src.services.pipeline.memory import repair_takeaways, takeaway_count
from src.services.pipeline.plan import _validate_tabs
from src.services.pipeline.plan_prompt import goal_rule_violation
from src.services.pipeline.reconcile import reconcile_at_plan_done, resolve_screen_code
from src.services.pipeline.reconcile_pass import ReconcilePass, size_step
from src.services.pipeline.synthesis import SynthesisInput, build_synthesis_prompt
from src.shared_config.domain_config import GroupingConfig

_PLAN_LOGGER = "src.services.pipeline.plan"


def _tab(tab_id: str, component: str, source: str, **extra: Any) -> dict:
    return {"id": tab_id, "label": tab_id, "component": component, "dataSource": source, **extra}


# ─── R3-C1: a malformed goal never replaces the plan ───


async def _run_plan_with(tabs: list[dict]) -> PlanResult:
    answer = json.dumps(
        {"contentTags": ["food"], "primaryTag": "food", "confidence": 0.9, "tabs": tabs}
    )
    with (
        patch.object(plan_prompt, "_load_plan_prompt", return_value="<video>{title}</video>"),
        patch.object(plan_prompt, "_load_component_toolkit", return_value=""),
        patch.object(plan_mod, "call_llm_with_retry", AsyncMock(return_value=answer)),
    ):
        return await plan_mod.run_plan(
            title="T",
            channel="C",
            description="",
            duration=600,
            category_hint="food",
            content_format=None,
            transcript="",
            llm_service=MagicMock(),
        )


class TestMalformedGoal:
    @pytest.mark.parametrize("goal", [None, ["after this you can cook it"], {"text": "x"}, 7])
    async def test_should_keep_the_planned_plan_when_a_goal_is_not_text(self, goal: object) -> None:
        result = await _run_plan_with([_tab("steps", "step_player", "food.steps", goal=goal)])

        assert result.confidence == 0.9

    @pytest.mark.parametrize("goal", [None, ["a list"]])
    def test_should_ship_an_empty_goal_when_the_goal_is_not_text(self, goal: object) -> None:
        tabs = _validate_tabs([_tab("steps", "step_player", "food.steps", goal=goal)])

        assert tabs[0]["goal"] == ""

    def test_should_log_the_empty_goal_when_the_goal_is_null(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level(logging.INFO, logger=_PLAN_LOGGER):
            _validate_tabs([_tab("steps", "step_player", "food.steps", goal=None)])

        assert "viewer-outcome rule: empty" in caplog.text


# ─── R3-W8: the goal rule's surface reads ───


class TestGoalRuleReads:
    def test_should_pass_a_goal_that_addresses_the_viewer_as_yourself(self) -> None:
        assert goal_rule_violation("Test yourself on why each claim holds") is None

    def test_should_pass_an_article_goal_whose_component_word_is_a_later_verb(self) -> None:
        assert goal_rule_violation("The tricks you can map to your own game, in order") is None

    def test_should_fail_an_article_goal_that_opens_on_a_component(self) -> None:
        reason = goal_rule_violation("An interactive timeline of every beat you hear")

        assert reason == "describes the component, not what the viewer gets"


# ─── R8-W1: the synthesis hero fallback follows memory's rules ───


@pytest.fixture
def _disk_synthesis_prompt():
    with patch.object(synthesis_mod, "_load_synthesis_prompt", synthesis_mod.PROMPT_PATH.read_text):
        yield


def _hero_prompt() -> str:
    return build_synthesis_prompt(
        SynthesisInput(
            title="t",
            channel="c",
            duration=1001,
            output_type="food",
            extraction_summary="{}",
            hero_fallback=True,
        )
    )


@pytest.mark.usefixtures("_disk_synthesis_prompt")
class TestSynthesisHeroFallback:
    def test_should_ask_for_the_thesis_when_memory_left_the_tldr_empty(self) -> None:
        assert "stating what the video claims — its thesis" in _hero_prompt()

    def test_should_ask_for_memorys_count_by_length(self) -> None:
        assert "(3 up to 15 minutes, 5 up to 45 minutes, 7 when longer)" in _hero_prompt()

    def test_should_ask_every_takeaway_to_end_on_its_stamp(self) -> None:
        assert "each ending with the [m:ss] of the extracted item" in _hero_prompt()

    def test_should_no_longer_ask_a_number_of_every_takeaway(self) -> None:
        assert "each carrying a specific number" not in _hero_prompt()

    def test_should_forbid_a_number_the_speaker_did_not_say(self) -> None:
        assert "Never state a duration, quantity or number the speaker did not say" in (
            _hero_prompt()
        )

    def test_should_keep_the_count_the_video_length_sets_when_parsing(self) -> None:
        data = {"masterSummary": "m", "keyTakeaways": [f"claim {i} [0:1{i}]" for i in range(6)]}

        result = synthesis_mod.parse_synthesis_output(data, 600)

        assert len(result.key_takeaways) == takeaway_count(600)

    def test_should_cut_a_stamp_past_the_end_when_parsing(self) -> None:
        data = {"masterSummary": "m", "keyTakeaways": ["a [0:10]", "b [0:20]", "c [20:00]"]}

        result = synthesis_mod.parse_synthesis_output(data, 600)

        assert result.key_takeaways == ["a [0:10]", "b [0:20]", "c"]


# ─── R8-W4: a cut takeaway stamp is logged ───


class TestCutStampsLogged:
    def test_should_log_the_cut_stamps_and_their_count(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        raw = ["a [0:10]", "b [11:00]", "c [12:30]"]
        with caplog.at_level(logging.WARNING, logger="src.services.pipeline.memory"):
            repair_takeaways(raw, 600)

        assert "cut 2 stamp(s) past the 600s end: [11:00], [12:30]" in caplog.text

    def test_should_log_nothing_when_every_stamp_is_inside_the_video(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level(logging.WARNING, logger="src.services.pipeline.memory"):
            repair_takeaways(["a [0:10]", "b [1:00]", "c [2:00]"], 600)

        assert "past the" not in caplog.text


# ─── R6-W5 / R6-W6 / R6-W7: the quiz count and its stamps ───


_BOUNDED_POLICY = {
    "questionsByDuration": [
        {"maxMinutes": 15, "questions": 3},
        {"maxMinutes": 45, "questions": 5},
        {"maxMinutes": 90, "questions": 8},
    ]
}


def _quiz_prompt(duration_s: float | None) -> str:
    prompt = build_quiz_prompt(
        "learning",
        {"learning": {}},
        "<video_memory>\nm\n</video_memory>",
        [],
        duration_s=duration_s,
    )
    assert prompt is not None
    return prompt


def _question(n: int, timestamp: int | None) -> dict[str, Any]:
    return {
        "question": f"Why does the speaker rank point {n} first?",
        "options": ["a", "b", "c", "d"],
        "correctIndex": 0,
        "explanation": "Because.",
        "timestamp": timestamp,
    }


class TestQuizCount:
    def test_should_take_the_last_band_when_no_band_bounds_the_length(self) -> None:
        with patch.object(enrichment_mod, "quiz_policy", return_value=_BOUNDED_POLICY):
            assert quiz_question_count(3 * 3600, cap=8) == 8

    @pytest.mark.parametrize("duration_s", [None, 0, float("nan")])
    def test_should_size_an_unknown_length_as_memory_does(self, duration_s: float | None) -> None:
        assert quiz_question_count(duration_s, cap=8) == takeaway_count(None)

    def test_should_say_inside_the_video_when_the_length_is_unknown(self) -> None:
        assert "Every question has one, inside the video.\n" in _quiz_prompt(None)

    def test_should_never_say_unknown_when_the_length_is_unknown(self) -> None:
        assert "unknown" not in _quiz_prompt(None)

    def test_should_keep_the_range_when_the_length_is_known(self) -> None:
        assert "inside the video (0:00–10:00)." in _quiz_prompt(600)


class TestStamplessQuestionsLogged:
    def test_should_log_how_many_kept_questions_carry_no_timestamp(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        items = [_question(1, 30), _question(2, None), _question(3, None)]
        with caplog.at_level(logging.WARNING, logger="src.services.pipeline.enrichment"):
            parse_quiz({"quiz": items}, 8, 600)

        assert "2 of 3 kept question(s) carry no timestamp" in caplog.text

    def test_should_log_nothing_when_every_question_is_stamped(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level(logging.WARNING, logger="src.services.pipeline.enrichment"):
            parse_quiz({"quiz": [_question(1, 30), _question(2, 60)]}, 8, 600)

        assert "no timestamp" not in caplog.text


# ─── R7-W3: the primary's moment source is never read off the screen ───


def _moments_plan(primary: str) -> PlanResult:
    return PlanResult.model_validate(
        {
            "contentTags": [primary, "learning"],
            "primaryTag": primary,
            "confidence": 0.9,
            "tabs": [_tab("moments", "moment_track", "learning.timestamps")],
        }
    )


def _moments_source(primary: str) -> str:
    outcome = reconcile_at_plan_done(_moments_plan(primary), None, None)
    return next(t["dataSource"] for t in outcome.tabs if t["id"] == "moments")


class TestPrimaryMomentSource:
    def test_should_keep_the_transcript_list_when_gamings_highlights_wait_for_frames(
        self,
    ) -> None:
        assert _moments_source("gaming") == "learning.timestamps"

    def test_should_repoint_to_the_match_events_when_the_primary_is_sport(self) -> None:
        assert _moments_source("sport") == "sport.matchEvents"

    def test_should_repoint_to_the_structure_when_the_primary_is_music(self) -> None:
        assert _moments_source("music") == "music.structure"


# ─── R11-W4 / R11-W7: folds survive the trim; the fold runs at frames-done ───


_GRID_FOLD = {"id": "facts", "label": "Facts", "dataSource": "learning.keyPoints"}


def _over_max_run() -> ReconcilePass:
    tabs = [
        _tab("ideas", "spot_explorer", "learning.keyIdeas"),
        _tab("moments", "moment_track", "learning.timestamps"),
        _tab("concepts", "concept_canvas", "learning.concepts"),
        _tab("claims", "claims_tracker", "learning.claims"),
        _tab("points", "spot_explorer", "learning.keyPoints"),
        _tab("narrative", "spot_explorer", "narrative.takeaways"),
        _tab("asks", "checklist", "learning.takeaways", **{FOLDS_KEY: [_GRID_FOLD]}),
    ]
    return ReconcilePass(
        tabs=tabs,
        active=["learning", "narrative"],
        evidence={},
        stage="plan_done",
        readers={},
        primary="learning",
    )


class TestFoldHostOutlivesTheTrim:
    def test_should_keep_the_host_carrying_folds_when_the_plan_is_over_max(self) -> None:
        run = _over_max_run()

        size_step(run)

        assert "asks" in [t["id"] for t in run.tabs]

    def test_should_trim_and_record_another_tab_when_the_last_one_hosts_folds(self) -> None:
        run = _over_max_run()

        size_step(run)

        assert [(r["tabId"], r["reason"]) for r in run.records] == [("narrative", "over_max")]


def _deferred_code_plan() -> PlanResult:
    """A tech plan whose code tab waited for the screen: three settled tabs at
    plan-done (no fold below the minimum), four once the screen keeps the code."""
    return PlanResult.model_validate(
        {
            "contentTags": ["tech"],
            "primaryTag": "tech",
            "confidence": 0.9,
            "evidence": {"has_code": False},
            "deferredTabs": ["code"],
            "tabs": [
                _tab("code", "code_playground", "tech.snippets"),
                _tab("topics", "spot_explorer", "tech.topics"),
                _tab("grid", "info_grid", "tech.topics"),
                _tab("quiz", "quiz_arena", "enrichment.quiz"),
            ],
        }
    )


class TestFoldAtFramesDone:
    def test_should_fold_a_standing_grid_when_the_screen_keeps_the_code_tab(self) -> None:
        outcome = resolve_screen_code(_deferred_code_plan(), [{"scene_type": "code"}], [], None)

        assert "grid" not in [t["id"] for t in outcome.tabs]

    def test_should_record_the_fold_at_frames_done(self) -> None:
        outcome = resolve_screen_code(_deferred_code_plan(), [{"scene_type": "code"}], [], None)

        assert any(r["action"] == "folded" and r["tabId"] == "grid" for r in outcome.records)


# ─── R12-W2: a small first slice rides with the next ───


_GROUPING = GroupingConfig(maxTextGroups=5, groupOutputBudget=1500, minGroupOutput=400)


def _field(name: str, tokens: int) -> FieldRequest:
    return FieldRequest(
        domain="learning",
        field=name,
        kind="list",
        reason="planned",
        tab_ids=(name,),
        cap=None,
        expect=1,
        weight=tokens,
        visual=False,
    )


def _calls(fields: list[FieldRequest]) -> int:
    tabs = [{"id": f.field} for f in fields]
    return len(build_groups(fields, tabs, _GROUPING))


class TestSmallFirstSlice:
    def test_should_absorb_a_small_first_slice_into_the_next(self) -> None:
        fields = [_field("asks", 300), _field("ideas", 1400), _field("moments", 1400)]

        assert _calls(fields) == 2

    def test_should_cut_as_many_calls_when_the_small_field_comes_last(self) -> None:
        fields = [_field("asks", 300), _field("ideas", 1400), _field("moments", 1400)]

        assert _calls(fields) == _calls(fields[::-1])

    def test_should_keep_the_plan_order_inside_the_absorbing_group(self) -> None:
        fields = [_field("asks", 300), _field("ideas", 1400), _field("moments", 1400)]
        tabs = [{"id": f.field} for f in fields]

        first = build_groups(fields, tabs, _GROUPING)[0]

        assert [f.field for f in first.fields] == ["asks", "ideas"]
