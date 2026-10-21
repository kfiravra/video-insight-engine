"""Task 1.10 follow-up: plan validation logs a goal that breaks the viewer-outcome rule.

The rule is observability only — the tab ships unchanged either way.
"""

from __future__ import annotations

import logging

import pytest

from src.services.pipeline.plan import _validate_tabs

LOGGER = "src.services.pipeline.plan"


def _tab(goal: str) -> dict:
    return {
        "id": "key_ideas",
        "label": "Key ideas",
        "component": "spot_explorer",
        "dataSource": "learning.keyIdeas",
        "goal": goal,
    }


class TestGoalRuleLogged:
    def test_should_log_the_tab_when_its_goal_names_a_component(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level(logging.INFO, logger=LOGGER):
            _validate_tabs([_tab("A moment_track of the keynote")])

        assert "goal breaks the viewer-outcome rule" in caplog.text

    def test_should_keep_the_tab_and_its_goal_when_the_rule_is_broken(self) -> None:
        tabs = _validate_tabs([_tab("A moment_track of the keynote")])

        assert tabs[0]["goal"] == "A moment_track of the keynote"

    def test_should_not_log_when_the_goal_names_a_viewer_outcome(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        goal = "After this you know the five claims DHH makes about agents and why"
        with caplog.at_level(logging.INFO, logger=LOGGER):
            _validate_tabs([_tab(goal)])

        assert "viewer-outcome rule" not in caplog.text
