"""S13 iteration (2026-10-21): a podcast plan that reads learning.keyIdeas switches learning on.

D10 routes the argument tab of a talk, lecture, podcast or interview to learning.keyIdeas.
When the planner tagged the show podcast + tech, the plan-parse guard used to re-point
Positions to podcast.guests (the guest list) because learning was not a tag.
"""

from __future__ import annotations

from src.services.pipeline.plan import _normalize_plan_data
from src.services.pipeline.plan_reconcile import activate_argument_domains

POSITIONS = {"id": "positions", "component": "spot_explorer", "dataSource": "learning.keyIdeas"}
QA = {"id": "qa", "component": "moment_track", "dataSource": "podcast.qa"}


class TestActivateArgumentDomains:
    def test_should_add_learning_when_a_tab_reads_key_ideas(self) -> None:
        assert activate_argument_domains([POSITIONS, QA], ["podcast", "tech"]) == [
            "podcast",
            "tech",
            "learning",
        ]

    def test_should_leave_the_tags_alone_when_learning_is_already_one(self) -> None:
        assert activate_argument_domains([POSITIONS], ["podcast", "learning"]) == [
            "podcast",
            "learning",
        ]

    def test_should_drop_an_unread_tag_when_the_list_passes_three(self) -> None:
        tags = activate_argument_domains([POSITIONS, QA], ["podcast", "tech", "news"])

        assert tags == ["podcast", "tech", "learning"]

    def test_should_never_drop_the_primary_tag(self) -> None:
        tags = activate_argument_domains([POSITIONS], ["podcast", "tech", "news"])

        assert tags[0] == "podcast"


class TestPlanKeepsPositions:
    def test_should_keep_positions_on_key_ideas_when_the_plan_forgot_the_learning_tag(
        self,
    ) -> None:
        data = {
            "contentTags": ["podcast", "tech"],
            "primaryTag": "podcast",
            "tabs": [
                {**POSITIONS, "label": "Positions", "goal": "After this you know his claims"},
                {**QA, "label": "Questions Asked", "goal": "After this you know the answers"},
            ],
        }

        _normalize_plan_data(data, "interview")

        positions = next(t for t in data["tabs"] if t["id"] == "positions")
        assert positions["dataSource"] == "learning.keyIdeas"

    def test_should_record_no_repoint_when_learning_is_switched_on(self) -> None:
        data = {
            "contentTags": ["podcast", "tech"],
            "primaryTag": "podcast",
            "tabs": [{**POSITIONS, "label": "Positions", "goal": "After this you know"}],
        }

        _normalize_plan_data(data, "interview")

        assert data["reconcile"] == []
