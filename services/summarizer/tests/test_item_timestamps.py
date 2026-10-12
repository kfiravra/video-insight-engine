"""Non-moment items keep their content but lose a timestamp past the video's end."""

from __future__ import annotations

from src.services.pipeline.assembly import assemble_response
from src.services.pipeline.assembly.item_timestamps import clear_impossible_item_timestamps


def _tab(component: str, items: list[dict]) -> dict:
    return {"id": "t", "component": component, "props": {"exercises": items}}


class TestClearImpossibleItemTimestamps:
    def test_should_clear_timestamps_past_the_end(self) -> None:
        tabs = [
            _tab("workout_room", [{"name": "a", "timestamp": 640}, {"name": "b", "timestamp": 60}])
        ]

        clear_impossible_item_timestamps(tabs, 570, 10.0)

        assert [i["timestamp"] for i in tabs[0]["props"]["exercises"]] == [None, 60]

    def test_should_keep_the_item_whose_timestamp_was_cleared(self) -> None:
        tabs = [_tab("workout_room", [{"name": "a", "timestamp": 640}])]

        clear_impossible_item_timestamps(tabs, 570, 10.0)

        assert tabs[0]["props"]["exercises"][0]["name"] == "a"

    def test_should_keep_a_timestamp_inside_the_tolerance(self) -> None:
        tabs = [_tab("workout_room", [{"name": "a", "timestamp": 578}])]

        clear_impossible_item_timestamps(tabs, 570, 10.0)

        assert tabs[0]["props"]["exercises"][0]["timestamp"] == 578

    def test_should_leave_moment_track_tabs_to_their_own_hygiene(self) -> None:
        tabs = [{"id": "m", "component": "moment_track", "props": {"items": [{"timestamp": 900}]}}]

        assert clear_impossible_item_timestamps(tabs, 570, 10.0) == 0

    def test_should_do_nothing_without_a_duration(self) -> None:
        tabs = [_tab("workout_room", [{"name": "a", "timestamp": 640}])]

        assert clear_impossible_item_timestamps(tabs, None, 10.0) == 0


def test_should_ship_no_exercise_timestamp_past_the_end_when_assembling() -> None:
    """Dev golden 2026-10-12: fitness-7min-workout (570 s) with exercises at 580-724 s."""
    triage = {
        "contentTags": ["fitness"],
        "primaryTag": "fitness",
        "tabs": [
            {
                "id": "exercises",
                "label": "Workout",
                "emoji": "💪",
                "component": "workout_room",
                "dataSource": "fitness.exercises",
            }
        ],
    }
    exercises = [
        {"name": f"ex {i}", "duration": "30", "timestamp": t}
        for i, t in enumerate([20, 300, 560, 580, 640, 724])
    ]

    result = assemble_response(
        triage, {"fitness": {"exercises": exercises}}, None, None, video_meta={"duration": 570}
    )

    workout = next(t for t in result["tabs"] if t["component"] == "workout_room")
    assert all((e.get("timestamp") or 0) <= 580 for e in workout["props"]["exercises"])
