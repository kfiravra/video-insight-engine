"""output-quality 0b follow-ups: no provisional Moments tab when the steps are the timeline.

D6 (0b.2) ships no Moments tab for a response navigated by a step player, a step
flow canvas or a workout room; the plan-done preview must not stream one that
assembly would only withdraw at ``complete``.
"""

from __future__ import annotations

import pytest

from src.services.pipeline.assembly import core as assembly_core
from src.services.pipeline.assembly import moment_fallback
from src.services.pipeline.assembly.moment_fallback import (
    provisional_moments_tab,
    steps_are_the_timeline,
)

_OUTLINE = [
    {"start": 0, "end": 300, "title": "Prep the sauce"},
    {"start": 300, "end": 900, "title": "Layer and bake"},
]
_META = {"duration": 900, "outline": _OUTLINE, "chapters": []}
_MOMENTS_TAB = {"id": "moments", "label": "Key Moments", "component": "moment_track",
                "dataSource": "food.steps", "goal": "Jump to a step"}  # fmt: skip


def _steps_tab(component: str) -> dict:
    return {"id": "steps", "label": "Steps", "component": component,
            "dataSource": "food.steps", "goal": "Cook it"}  # fmt: skip


class TestProvisionalMomentsWhenStepsAreTheTimeline:
    @pytest.mark.parametrize("component", ["step_player", "step_flow_canvas", "workout_room"])
    def test_should_stream_no_provisional_tab_when_a_step_component_is_planned(
        self, component: str
    ) -> None:
        plan = [_steps_tab(component), _MOMENTS_TAB]

        assert provisional_moments_tab(plan, _META, None, frozenset()) is None

    def test_should_stream_no_provisional_tab_when_the_plan_has_no_moment_tab(self) -> None:
        plan = [_steps_tab("step_player")]

        assert provisional_moments_tab(plan, _META, None, frozenset()) is None

    def test_should_still_stream_the_provisional_tab_when_no_step_component_is_planned(
        self,
    ) -> None:
        plan = [_steps_tab("checklist"), _MOMENTS_TAB]

        built = provisional_moments_tab(plan, _META, None, frozenset())

        assert built is not None and built[0]["id"] == "moments"


class TestStepsAreTheTimeline:
    def test_should_read_assembled_and_plan_tabs_alike(self) -> None:
        assert steps_are_the_timeline([{"component": "step_player"}]) is True

    def test_should_be_false_without_a_step_component(self) -> None:
        assert steps_are_the_timeline([{"component": "moment_track"}]) is False

    def test_should_stay_importable_from_core_as_the_same_function(self) -> None:
        assert assembly_core.steps_are_the_timeline is moment_fallback.steps_are_the_timeline
