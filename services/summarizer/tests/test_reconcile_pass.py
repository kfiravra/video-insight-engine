"""Reconcile steps 2–5 — required backfill, quiz, dedupe, 3–6 tabs (pipeline-1min 3.2)."""

from __future__ import annotations

import pytest

from src.models.memory_types import MemoryResult
from src.services.pipeline.reconcile import ReconcileOutcome, reconcile_at_plan_done
from src.shared_config.domain_config import domain_requirements, requirement_evidence
from tests.test_reconcile import (
    _action,
    _food_tabs_without_checklist,
    _ids,
    _learning_tabs_with_quiz,
    _plan,
    _tab,
)
from tests.test_requirement_evidence_backfill import _WOULD_ADD

# ─── Step 2: required components, every domain ───

# The component each domain's defaultTabs backfill (science's spot_explorer has
# no defaultTab, so assembly only warns — as before).
_BACKFILLED: dict[str, set[str]] = {
    "food": {"step_player", "checklist"},
    "project": {"step_player", "checklist"},
    "review": {"comparison"},
    "fitness": {"workout_room"},
    "tech": {"code_playground"},
    "travel": {"spot_explorer"},
    "music": set(),
    "learning": set(),
    "language": {"spot_explorer"},
    "science": set(),
    "podcast": {"moment_track"},
    "news": {"claims_tracker"},
    "gaming": {"tier_list"},
    "sport": {"formation_diagram"},
}
_GATED = [(d, c, k) for d, gates in requirement_evidence().items() for c, k in gates.items()]


def _fillers() -> list[dict]:
    return [_tab(f"t{i}", "info_grid", "") for i in range(3)]


def _backfilled(outcome: ReconcileOutcome) -> set[str]:
    return {r["component"] for r in outcome.records if r["action"] == "backfilled"}


class TestRequiredBackfill:
    def test_should_cover_every_domain(self):
        assert set(_BACKFILLED) == set(domain_requirements())

    @pytest.mark.parametrize("domain", sorted(_BACKFILLED))
    def test_should_backfill_each_domain_required_component_when_evidence_is_silent(self, domain):
        outcome = reconcile_at_plan_done(_plan(domain, _fillers()), MemoryResult(), None)

        assert _backfilled(outcome) == _BACKFILLED[domain]

    @pytest.mark.parametrize(("domain", "component", "key"), _GATED)
    def test_should_not_backfill_a_required_component_when_doubly_false(
        self, domain, component, key
    ):
        plan = _plan(domain, _fillers(), {key: False})

        outcome = reconcile_at_plan_done(plan, MemoryResult(evidence={key: False}), None)

        assert component not in _backfilled(outcome)

    @pytest.mark.parametrize(("domain", "component", "key"), _GATED)
    def test_should_record_a_ruled_out_requirement_under_the_default_tab_it_would_add(
        self, domain, component, key
    ):
        plan = _plan(domain, _fillers(), {key: False})

        outcome = reconcile_at_plan_done(plan, MemoryResult(evidence={key: False}), None)

        # has_code waits for the screen (frames-done decides); the rest are decided here.
        action = "deferred_to_frames" if key == "has_code" else "not_backfilled"
        rows = [
            (r["tabId"], r["action"], (r["evidence"] or {}).get("key")) for r in outcome.records
        ]
        assert (_WOULD_ADD[(domain, component)], action, key) in rows


# ─── Steps 3–5: quiz, dedupe, size ───


class TestQuizDedupeSize:
    def test_should_drop_the_quiz_when_the_primary_is_not_a_quiz_domain(self):
        tabs = [*_food_tabs_without_checklist(), _tab("quiz", "quiz_arena", "enrichment.quiz")]

        outcome = reconcile_at_plan_done(_plan("food", tabs), MemoryResult(), None)

        assert outcome.dropped[-1]["reason"] == "reconcile_quiz_domain"

    def test_should_move_the_quiz_last(self):
        outcome = reconcile_at_plan_done(
            _plan("learning", _learning_tabs_with_quiz()), MemoryResult(), None
        )

        assert _ids(outcome.tabs)[-1] == "quiz"

    def test_should_not_record_a_move_when_the_quiz_is_already_last(self):
        tabs = sorted(_learning_tabs_with_quiz(), key=lambda tab: tab["id"] == "quiz")

        outcome = reconcile_at_plan_done(_plan("learning", tabs), MemoryResult(), None)

        assert _action(outcome, "quiz") is None

    def test_should_dedupe_by_datasource_and_component(self):
        tabs = [*_food_tabs_without_checklist(), _tab("steps_again", "step_player", "food.steps")]

        outcome = reconcile_at_plan_done(_plan("food", tabs), MemoryResult(), None)

        assert outcome.dropped[-1] == {
            "id": "steps_again",
            "component": "step_player",
            "dataSource": "food.steps",
            "reason": "reconcile_duplicate",
        }

    def test_should_dedupe_a_component_and_its_promotion_target_on_one_source(self):
        """L26 (output-quality 0b.5): a flash_deck promotes to concept_canvas, so two
        tabs on one ``concepts`` list would ship the same canvas twice (S03)."""
        tabs = [*_learning_tabs_with_quiz(), _tab("thinkers", "flash_deck", "learning.concepts")]

        outcome = reconcile_at_plan_done(_plan("learning", tabs), MemoryResult(), None)

        assert _action(outcome, "thinkers") == "dropped"

    def test_should_keep_the_required_tab_over_an_earlier_twin_that_could_promote(self):
        """Phase-0 review W-A6: an info_grid promotes to spot_explorer, so it shares the
        pair of travel's required spot_explorer — the required tab stays."""
        tabs = [
            _tab("tips", "info_grid", "travel.itinerary"),
            _tab("spots", "spot_explorer", "travel.itinerary"),
            _tab("budget", "budget", "travel.budget"),
        ]

        outcome = reconcile_at_plan_done(_plan("travel", tabs), MemoryResult(), None)

        assert ("spots" in _ids(outcome.tabs), [d["id"] for d in outcome.dropped]) == (
            True,
            ["tips"],
        )

    def test_should_keep_the_first_twin_when_neither_is_required(self):
        tabs = [
            _tab("deck", "flash_deck", "learning.concepts"),
            _tab("canvas", "concept_canvas", "learning.concepts"),
            _tab("takeaways", "checklist", "learning.takeaways"),
        ]

        outcome = reconcile_at_plan_done(_plan("learning", tabs), MemoryResult(), None)

        assert [d["id"] for d in outcome.dropped] == ["canvas"]

    def test_should_keep_two_views_of_one_source(self):
        outcome = reconcile_at_plan_done(
            _plan("food", _food_tabs_without_checklist()), MemoryResult(), None
        )

        assert {"steps", "moments"} <= set(_ids(outcome.tabs))

    def test_should_fill_to_three_from_the_primary_default_tabs(self):
        tabs = [_tab("concepts", "concept_canvas", "learning.concepts")]

        outcome = reconcile_at_plan_done(_plan("learning", tabs), MemoryResult(), None)

        assert _ids(outcome.tabs) == ["concepts", "takeaways", "timestamps"]

    def test_should_never_fill_with_an_overview_tab(self):
        tabs = [_tab("concepts", "concept_canvas", "learning.concepts")]

        outcome = reconcile_at_plan_done(_plan("learning", tabs), MemoryResult(), None)

        assert all(tab["component"] != "overview" for tab in outcome.tabs)

    def test_should_trim_the_last_other_tab_never_a_required_or_moment_tab(self):
        tabs = [
            _tab("a", "info_grid", "tech.topics"),
            _tab("b", "spot_explorer", "tech.cheatSheet"),
            _tab("c", "concept_canvas", "learning.concepts"),
            _tab("d", "flash_deck", "learning.keyPoints"),
            _tab("e", "checklist", "learning.takeaways"),
            _tab("f", "moment_track", "learning.timestamps"),
            _tab("g", "code_playground", "tech.snippets"),
        ]

        outcome = reconcile_at_plan_done(_plan("tech", tabs, tags=["tech", "learning"]), None, None)

        assert [d["id"] for d in outcome.dropped] == ["e"]

    def test_should_keep_the_quiz_when_a_backfill_overfills_a_full_plan(self):
        tabs = [
            _tab("a", "info_grid", "tech.topics"),
            _tab("b", "spot_explorer", "tech.cheatSheet"),
            _tab("c", "concept_canvas", "learning.concepts"),
            _tab("d", "checklist", "learning.takeaways"),
            _tab("e", "moment_track", "learning.timestamps"),
            _tab("quiz", "quiz_arena", "enrichment.quiz"),
        ]
        plan = _plan("tech", tabs, tags=["tech", "learning"])

        outcome = reconcile_at_plan_done(plan, MemoryResult(), None)

        assert (_ids(outcome.tabs)[-1], [d["id"] for d in outcome.dropped]) == ("quiz", ["d"])

    def test_should_keep_a_planned_moment_tab_when_two_backfills_overfill_a_full_plan(self):
        tabs = [
            _tab("tips", "info_grid", "food.tips"),
            _tab("x", "spot_explorer", "food.ingredients"),
            _tab("moments", "moment_track", "food.steps"),
            _tab("y", "info_grid", "food.equipment"),
            _tab("z", "info_grid", "food.substitutions"),
            _tab("v", "overview", "food.tips"),
        ]

        outcome = reconcile_at_plan_done(_plan("food", tabs), MemoryResult(), None)

        assert ("moments" in _ids(outcome.tabs), [d["id"] for d in outcome.dropped]) == (
            True,
            ["v", "z"],
        )
