"""Plan-time dataSource validation against the registry (pipeline-1min 0.5).

An unregistered dataSource copied verbatim from the plan LLM used to reach
extraction and burn retries. The plan stage now swaps in a same-domain sibling
rendered by the same component, else drops the tab and persists why.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from src.models.pipeline_types import PlanResult
from src.services.pipeline.plan import _validate_data_sources
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


async def test_should_persist_plan_drops_ahead_of_assembly_drops() -> None:
    from src.services.pipeline.phases import assembly as phase

    plan_drop = {"id": "x", "component": "checklist", "dataSource": "food.nope", "reason": "r"}
    assembly_drop = {"id": "y", "component": "info_grid", "dataSource": "food.tips", "reason": "s"}
    ctx = _build_ctx()
    ctx.plan_result = PlanResult.model_validate({"droppedTabs": [plan_drop]})
    with (
        patch.object(
            phase,
            "assemble_response",
            return_value={"tabs": [], "meta": {}, "dropped": [assembly_drop]},
        ),
        patch.object(
            phase,
            "settings",
            SimpleNamespace(REDIS_ENABLED=False, QDRANT_ENABLED=False, PIPELINE_VERSION="vtest"),
        ),
        patch.object(phase, "response_cache"),
    ):
        _ = [chunk async for chunk in phase.run_phase_assembly(ctx)]  # type: ignore[arg-type]

    saved = ctx.repository.save_structured_result.call_args.args[1]
    assert saved["pipeline"]["assembly"]["droppedTabs"] == [plan_drop, assembly_drop]
