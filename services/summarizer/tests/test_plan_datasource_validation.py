"""Plan-time dataSource validation against the registry (pipeline-1min 0.5).

An unregistered dataSource copied verbatim from the plan LLM used to reach
extraction and burn retries. The plan stage now swaps in a same-domain sibling
rendered by the same component, else drops the tab and persists why.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from src.models.pipeline_types import PlanResult
from src.services.pipeline import plan as plan_mod
from src.services.pipeline.plan import _validate_data_sources, _validate_tabs
from tests.test_phase_assembly_cache import _build_ctx


def _tab(data_source: str, component: str = "step_player", tab_id: str = "steps") -> dict:
    return {"id": tab_id, "label": "Steps", "component": component, "dataSource": data_source}


class TestValidateDataSources:
    def test_should_keep_registered_path_unchanged(self) -> None:
        kept, dropped = _validate_data_sources([_tab("food.steps")])

        assert (kept[0]["dataSource"], dropped) == ("food.steps", [])

    def test_should_swap_in_sibling_when_path_is_not_registered(self) -> None:
        kept, _ = _validate_data_sources([_tab("food.recipeSteps")])

        assert kept[0]["dataSource"] == "food.steps"

    def test_should_pick_sibling_rendered_by_the_tabs_component(self) -> None:
        kept, _ = _validate_data_sources([_tab("food.shoppingList", component="checklist")])

        assert kept[0]["dataSource"] == "food.ingredients"

    def test_should_drop_tab_when_no_sibling_has_its_component(self) -> None:
        kept, dropped = _validate_data_sources([_tab("food.lyrics", component="lyrics_viewer")])

        assert kept == []
        assert dropped == [
            {
                "id": "steps",
                "component": "lyrics_viewer",
                "dataSource": "food.lyrics",
                "reason": "invalid_datasource",
            }
        ]

    def test_should_drop_tab_when_domain_is_unknown(self) -> None:
        kept, dropped = _validate_data_sources([_tab("recipes.steps")])

        assert (kept, dropped[0]["reason"]) == ([], "invalid_datasource")

    def test_should_keep_frames_datasource(self) -> None:
        kept, _ = _validate_data_sources([_tab("frames", component="video_filmstrip")])

        assert kept[0]["dataSource"] == "frames"

    def test_should_leave_empty_datasource_alone(self) -> None:
        kept, dropped = _validate_data_sources([_tab("")])

        assert (kept[0]["dataSource"], dropped) == ("", [])


class TestPathsTheRegistryDoesNotList:
    """Paths assembly resolves although they are not registry entries (review p0.5 #1/#2/#4)."""

    def test_should_keep_bare_domain_when_tab_reads_the_whole_domain(self) -> None:
        kept, _ = _validate_data_sources(
            [_tab("review", component="comparison", tab_id="pros_cons")]
        )

        assert kept[0]["dataSource"] == "review"

    def test_should_keep_bare_domain_when_workout_reads_all_of_fitness(self) -> None:
        kept, _ = _validate_data_sources([_tab("fitness", component="workout_room")])

        assert kept[0]["dataSource"] == "fitness"

    def test_should_keep_domain_wildcard_when_tab_reads_the_whole_domain(self) -> None:
        kept, _ = _validate_data_sources([_tab("food.*", component="info_grid")])

        assert kept[0]["dataSource"] == "food.*"

    def test_should_drop_bare_name_when_it_is_not_a_domain(self) -> None:
        kept, dropped = _validate_data_sources([_tab("recipes", component="info_grid")])

        assert (kept, dropped[0]["reason"]) == ([], "invalid_datasource")

    def test_should_keep_finance_field_when_modifier_model_has_it(self) -> None:
        tabs = [
            _tab("finance.costs", component="budget", tab_id="budget"),
            _tab("finance.savingTips", component="info_grid", tab_id="saving"),
            _tab("finance", component="budget", tab_id="money"),
        ]

        kept, dropped = _validate_data_sources(tabs)

        assert ([t["dataSource"] for t in kept], dropped) == (
            ["finance.costs", "finance.savingTips", "finance"],
            [],
        )

    def test_should_drop_modifier_path_when_model_has_no_such_field(self) -> None:
        kept, dropped = _validate_data_sources([_tab("finance.budget", component="budget")])

        assert (kept, dropped[0]["dataSource"]) == ([], "finance.budget")

    def test_should_keep_overview_tab_when_its_path_is_unregistered(self) -> None:
        kept, dropped = _validate_data_sources([_tab("synthesis.summary", component="overview")])

        assert (kept[0]["dataSource"], dropped) == ("synthesis.summary", [])

    def test_should_keep_overview_id_when_component_is_mislabeled(self) -> None:
        tab = _tab("learning.summary", component="info_grid", tab_id="overview")

        kept, _ = _validate_data_sources([tab])

        assert kept[0]["dataSource"] == "learning.summary"


class TestNonStringDataSource:
    """A null/list/dict dataSource from the LLM is an unset source (review p0.5 #3)."""

    def _raw(self, data_source: object) -> dict:
        return {
            "id": "steps",
            "label": "Steps",
            "component": "step_player",
            "dataSource": data_source,
        }

    def test_should_keep_tab_when_datasource_is_null(self) -> None:
        kept, dropped = _validate_data_sources(_validate_tabs([self._raw(None)]))

        assert (kept[0]["dataSource"], dropped) == ("", [])

    def test_should_not_raise_when_datasource_is_a_list(self) -> None:
        kept, _ = _validate_data_sources(_validate_tabs([self._raw(["food.steps"])]))

        assert kept[0]["dataSource"] == ""

    def test_should_not_raise_when_datasource_is_a_dict(self) -> None:
        kept, _ = _validate_data_sources(_validate_tabs([self._raw({"path": "food.steps"})]))

        assert kept[0]["dataSource"] == ""


def _plan_json(tabs: list[dict]) -> str:
    return json.dumps(
        {"contentTags": ["food"], "primaryTag": "food", "confidence": 0.9, "tabs": tabs}
    )


async def _run_plan_with(tabs: list[dict]) -> PlanResult:
    with (
        patch.object(plan_mod, "_load_plan_prompt", return_value="<video>{title}</video>"),
        patch.object(plan_mod, "_load_component_toolkit", return_value=""),
        patch.object(plan_mod, "call_llm_with_retry", AsyncMock(return_value=_plan_json(tabs))),
    ):
        return await plan_mod.run_plan(
            title="T",
            channel="C",
            description="",
            duration=600,
            category_hint="food",
            content_format=None,
            transcript_preview="",
            llm_service=MagicMock(),
        )


class TestPlanFallbackFlag:
    """Defaults standing in for an emptied plan are flagged (review p0.5 #5)."""

    async def test_should_flag_fallback_when_every_tab_is_dropped(self) -> None:
        bad = {"id": "lyrics", "label": "L", "component": "lyrics_karaoke", "dataSource": "food.x"}

        result = await _run_plan_with([bad])

        assert (result.plan_fallback, len(result.dropped_tabs)) == (True, 1)

    async def test_should_not_flag_fallback_when_a_planned_tab_survives(self) -> None:
        good = {"id": "steps", "label": "S", "component": "step_player", "dataSource": "food.steps"}

        result = await _run_plan_with([good])

        assert result.plan_fallback is False


async def _saved_assembly(
    plan_result: PlanResult, triage_tabs: list[dict], drops: list[dict]
) -> dict:
    from src.services.pipeline.phases import assembly as phase

    ctx = _build_ctx()
    ctx.plan_result = plan_result
    ctx.triage = SimpleNamespace(tabs=triage_tabs)
    with (
        patch.object(
            phase,
            "assemble_response",
            return_value={"tabs": [], "meta": {}, "dropped": drops},
        ),
        patch.object(
            phase,
            "settings",
            SimpleNamespace(REDIS_ENABLED=False, QDRANT_ENABLED=False, PIPELINE_VERSION="vtest"),
        ),
        patch.object(phase, "response_cache"),
    ):
        _ = [chunk async for chunk in phase.run_phase_assembly(ctx)]  # type: ignore[arg-type]
    return ctx.repository.save_structured_result.call_args.args[1]["pipeline"]["assembly"]


async def test_should_count_only_plan_drops_as_designed_when_defaults_stand_in() -> None:
    drops = [{"id": f"t{i}", "component": "c", "dataSource": "d", "reason": "r"} for i in range(2)]
    plan = PlanResult.model_validate({"droppedTabs": drops, "planFallback": True})

    saved = await _saved_assembly(plan, [{"id": "a"}, {"id": "b"}, {"id": "c"}], [])

    assert (saved["tabsDesigned"], saved["planFallback"]) == (2, True)


async def test_should_count_kept_tabs_plus_drops_when_planner_tabs_survive() -> None:
    drops = [{"id": "x", "component": "c", "dataSource": "d", "reason": "r"}]
    plan = PlanResult.model_validate({"droppedTabs": drops})

    saved = await _saved_assembly(plan, [{"id": "a"}, {"id": "b"}], [])

    assert (saved["tabsDesigned"], "planFallback" in saved) == (3, False)


async def test_should_persist_plan_drops_ahead_of_assembly_drops() -> None:
    plan_drop = {"id": "x", "component": "checklist", "dataSource": "food.nope", "reason": "r"}
    assembly_drop = {"id": "y", "component": "info_grid", "dataSource": "food.tips", "reason": "s"}
    plan = PlanResult.model_validate({"droppedTabs": [plan_drop]})

    saved = await _saved_assembly(plan, [], [assembly_drop])

    assert saved["droppedTabs"] == [plan_drop, assembly_drop]
