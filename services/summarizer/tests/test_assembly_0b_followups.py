"""output-quality 0b follow-ups and their phase-0 review fixes.

D6 (0b.2) ships no Moments tab for a recipe, a workout or a step tutorial — a food,
fitness or project response navigated by a step player, a step flow canvas or a
workout room; the plan-done preview must not stream one that assembly would only
withdraw at ``complete``. The primary domain decides (review C-A1): one step tab
under a podcast or a lecture never costs the domain its timeline, and a moment_track
the domain requires is never dropped. Review W-A2: a twin tab keeps the items only
it ships (the trim's shapes; the rule itself is pinned in ``test_assembly_0b5_dedupe``).
Review W-A3: a fallback never borrows a list another tab ships. Review W-A4: a
procedure ships whole, in order.
"""

from __future__ import annotations

import pytest

from src.services.pipeline.assembly import assemble_response, moment_fallback
from src.services.pipeline.assembly import core as assembly_core
from src.services.pipeline.assembly.core import (
    DUPLICATE_ITEMS,
    DUPLICATE_TAB,
    _dedupe_tabs,
    count_dropped_tabs,
)
from src.services.pipeline.assembly.moment_fallback import (
    provisional_moments_tab,
    steps_are_the_timeline,
)
from src.shared_config.domain_config import effective_requirements
from tests.test_assembly_0b5_dedupe import _assembled, _canvas, _keys, _tab

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


def _provisional(plan: list[dict], primary: str = "food") -> tuple[dict, int] | None:
    return provisional_moments_tab(plan, _META, None, primary, effective_requirements(primary))


def _ids(result: dict) -> list[str]:
    return [t["id"] for t in result["tabs"]]


class TestProvisionalMomentsWhenStepsAreTheTimeline:
    @pytest.mark.parametrize("component", ["step_player", "step_flow_canvas", "workout_room"])
    def test_should_stream_no_provisional_tab_when_a_step_component_is_planned(
        self, component: str
    ) -> None:
        plan = [_steps_tab(component), _MOMENTS_TAB]

        assert _provisional(plan) is None

    def test_should_stream_no_provisional_tab_when_the_plan_has_no_moment_tab(self) -> None:
        plan = [_steps_tab("step_player")]

        assert _provisional(plan) is None

    def test_should_still_stream_the_provisional_tab_when_no_step_component_is_planned(
        self,
    ) -> None:
        plan = [_steps_tab("checklist"), _MOMENTS_TAB]

        built = _provisional(plan)

        assert built is not None and built[0]["id"] == "moments"


class TestStepsAreTheTimeline:
    def test_should_read_assembled_and_plan_tabs_alike(self) -> None:
        assert steps_are_the_timeline([{"component": "step_player"}], "food", []) is True

    @pytest.mark.parametrize("primary", ["food", "fitness", "project"])
    def test_should_hold_for_each_domain_navigated_by_its_steps(self, primary: str) -> None:
        assert steps_are_the_timeline([{"component": "workout_room"}], primary, []) is True

    def test_should_be_false_without_a_step_component(self) -> None:
        assert steps_are_the_timeline([{"component": "moment_track"}], "food", []) is False

    @pytest.mark.parametrize("primary", ["podcast", "learning", "science", "tech", "travel"])
    def test_should_be_false_for_a_primary_not_navigated_by_steps(self, primary: str) -> None:
        assert steps_are_the_timeline([{"component": "step_player"}], primary, []) is False

    def test_should_be_false_when_the_domain_requires_moment_track(self) -> None:
        tabs = [{"component": "step_player"}]

        assert steps_are_the_timeline(tabs, "food", ["step_player", "moment_track"]) is False

    def test_should_stay_importable_from_core_as_the_same_function(self) -> None:
        assert assembly_core.steps_are_the_timeline is moment_fallback.steps_are_the_timeline


# ─── C-A1: one step tab never costs the domain its timeline ───

_SEGMENTS = [{"title": f"Segment {i}", "summary": f"Talk about topic {i}", "timestamp": i * 600}
             for i in range(12)]  # fmt: skip
_EXERCISES = [{"name": n, "formCues": ["keep your back straight"], "sets": 3, "reps": 10}
              for n in ("Squat", "Push-up", "Plank")]  # fmt: skip
_PODCAST_TABS = [
    {"id": "segments", "label": "Segments", "component": "moment_track",
     "dataSource": "podcast.segments", "goal": "Jump"},
    {"id": "routine", "label": "The routine", "component": "workout_room",
     "dataSource": "fitness.exercises", "goal": "Train"},
]  # fmt: skip
_PODCAST = {"podcast": {"segments": _SEGMENTS}, "fitness": {"exercises": _EXERCISES}}
_TIMESTAMPS = [{"time": f"{m}:00", "seconds": m * 60, "label": f"Idea {chr(65 + m)}"}
               for m in (1, 5, 9)]  # fmt: skip
_EXPERIMENTS = [{"number": i, "title": f"Trial {i}", "instruction": f"Mix solution {i} slowly.",
                 "timestamp": 100 * i} for i in (1, 2, 3)]  # fmt: skip
_LECTURE_TABS = [
    {"id": "moments", "label": "Moments", "component": "moment_track",
     "dataSource": "learning.timestamps", "goal": "Jump"},
    {"id": "experiment", "label": "Experiment", "component": "step_player",
     "dataSource": "science.experiments", "goal": "Try it"},
]  # fmt: skip
_LECTURE = {"learning": {"timestamps": _TIMESTAMPS}, "science": {"experiments": _EXPERIMENTS}}


def _triage(primary: str, tags: list[str], tabs: list[dict]) -> dict:
    return {"primaryTag": primary, "contentTags": tags, "tabs": tabs}


def _podcast() -> dict:
    triage = _triage("podcast", ["podcast", "fitness"], _PODCAST_TABS)
    return assemble_response(triage, _PODCAST, None, None, {"duration": 7200})


def _lecture() -> dict:
    triage = _triage("learning", ["learning", "science"], _LECTURE_TABS)
    return assemble_response(triage, _LECTURE, None, None, _META)


class TestPrimaryDomainDecides:
    def test_should_keep_a_podcasts_required_segments_beside_one_workout_room(self) -> None:
        assert _ids(_podcast()) == ["overview", "segments", "routine"]

    def test_should_record_no_steps_are_the_timeline_drop_for_a_podcast(self) -> None:
        assert [d["id"] for d in _podcast()["dropped"]] == []

    def test_should_keep_a_lectures_timeline_beside_one_science_step_player(self) -> None:
        assert "moments" in _ids(_lecture())

    def test_should_still_ship_no_moments_tab_for_a_recipe(self) -> None:
        triage = _triage("food", ["food"], [_steps_tab("step_player"), _MOMENTS_TAB])
        steps = [{"number": i, "instruction": f"Step {i}: stir well.", "timestamp": 60 * i}
                 for i in (1, 2, 3)]  # fmt: skip

        result = assemble_response(triage, {"food": {"steps": steps}}, None, None, _META)

        assert [t["component"] for t in result["tabs"]].count("moment_track") == 0


class TestProvisionalAgreesWithAssembly:
    def test_should_preview_a_podcasts_segments_beside_one_workout_room(self) -> None:
        built = _provisional(_PODCAST_TABS, "podcast")

        assert (built or ({"id": None}, 0))[0]["id"] == "segments"

    def test_should_preview_a_lectures_timeline_beside_one_science_step_player(self) -> None:
        built = _provisional(_LECTURE_TABS, "learning")

        assert (built or ({"id": None}, 0))[0]["id"] in _ids(_lecture())

    def test_should_preview_nothing_for_a_recipe(self) -> None:
        assert _provisional([_steps_tab("step_player"), _MOMENTS_TAB], "food") is None


# ─── W-A2: a twin keeps the items only it ships ───


class TestTwinsKeepTheirOwnItems:
    def test_should_never_count_a_trimmed_twin_as_a_dropped_tab(self) -> None:
        assert count_dropped_tabs([{"reason": DUPLICATE_ITEMS}, {"reason": DUPLICATE_TAB}]) == 1

    def test_should_remap_the_sections_of_a_trimmed_itinerary(self) -> None:
        spots = [{"name": n} for n in "ABC"]
        days = [{"label": "Day 1", "spotIndices": [0, 1]}, {"label": "Day 2", "spotIndices": [2]}]
        later = _assembled("days", "spot_explorer", spots, "spots")
        later["props"]["sections"] = days
        tabs = [_assembled("top", "spot_explorer", spots[:2], "spots"), later]

        _dedupe_tabs(tabs, [])

        assert later["props"] == {
            "spots": [{"name": "C"}],
            "sections": [{"label": "Day 2", "spotIndices": [0]}],
        }

    def test_should_drop_a_trimmed_canvas_edge_to_a_trimmed_concept(self) -> None:
        later = _canvas("later", ["G1", "G2"])
        later["props"]["concepts"] = [
            {"name": "Concept A", "group": "G1", "connections": []},
            {
                "name": "Concept Z",
                "group": "G2",
                "connections": [{"to": "Concept A", "type": "uses"}],
            },
        ]
        tabs = [_canvas("earlier", []), later]

        _dedupe_tabs(tabs, [])

        assert later["props"] == {
            "concepts": [{"name": "Concept Z", "group": "G2", "connections": []}],
            "groups": ["G2"],
        }

    def test_should_ship_a_procedure_twin_with_steps_of_its_own_whole(self) -> None:
        later = _assembled("flow", "step_flow_canvas", _keys("x", "y", "z"), "steps")
        tabs = [_assembled("steps", "step_player", _keys("x", "y"), "steps"), later]

        _dedupe_tabs(tabs, [])

        assert [(t["id"], len(t["props"]["steps"])) for t in tabs] == [("steps", 2), ("flow", 3)]

    def test_should_ship_a_twin_whole_when_its_own_items_sit_beside_its_rows(self) -> None:
        rows = [{"feature": "Battery"}, {"feature": "Price"}]
        later = {
            "id": "b",
            "component": "comparison",
            "props": {"comparisons": rows, "pros": ["Fast"]},
        }
        tabs = [{"id": "a", "component": "comparison", "props": {"comparisons": list(rows)}}, later]

        _dedupe_tabs(tabs, [])

        assert [(t["id"], len(t["props"]["comparisons"])) for t in tabs] == [("a", 2), ("b", 2)]

    def test_should_trim_an_earlier_twin_of_a_required_tab_to_its_own_items(self) -> None:
        tabs = [
            _assembled("stops", "checklist", _keys(*"xyzw")),
            _assembled("itinerary", "spot_explorer", [{"name": n} for n in "xyz"], "spots"),
        ]

        _dedupe_tabs(tabs, [], required={"spot_explorer"})

        assert [len(next(iter(t["props"].values()))) for t in tabs] == [1, 3]

    def test_should_keep_the_cool_downs_own_exercise(self) -> None:
        """W-A2: the cool-down repeats two warm-up moves; Child's pose ships nowhere else."""

        def moves(*names: str) -> list[dict]:
            return [{"name": n, "formCues": ["slow and controlled"], "reps": 8} for n in names]

        triage = {"contentTags": ["fitness"], "primaryTag": "fitness", "tabs": [
            _tab("warmup", "workout_room", "fitness.warmup"),
            _tab("cooldown", "workout_room", "fitness.cooldown"),
        ]}  # fmt: skip
        fitness = {"warmup": moves("Arm circles", "Hip openers", "Cat-cow"),
                   "cooldown": moves("Cat-cow", "Hip openers", "Child's pose")}  # fmt: skip

        result = assemble_response(triage, {"fitness": fitness}, None, None)

        cooldown = next(t for t in result["tabs"] if t["id"] == "cooldown")
        assert [e["name"] for e in cooldown["props"]["exercises"]] == ["Child's pose"]


# ─── W-A3: a fallback never borrows a list another tab ships ───

_TAKEAWAYS = [f"Takeaway {i} explains a distinct idea of the talk" for i in range(6)]
_FACTS_TAB = {"id": "facts", "label": "Key Facts", "component": "info_grid",
              "dataSource": "learning.keyFacts", "goal": "Facts"}  # fmt: skip


def _with_owner(owner_source: str, extraction: dict) -> dict:
    owner = {"id": "owner", "label": "Takeaways", "component": "checklist",
             "dataSource": owner_source, "goal": "Do"}  # fmt: skip
    triage = _triage("learning", ["learning", "narrative"], [_FACTS_TAB, owner])
    return assemble_response(triage, extraction, None, {"tldr": "x"}, {"duration": 1200})


class TestFallbacksRefuseClaimedLists:
    def test_should_keep_the_longest_list_on_the_tab_that_planned_it(self) -> None:
        result = _with_owner("learning.takeaways", {"learning": {"takeaways": _TAKEAWAYS}})

        assert _ids(result) == ["overview", "owner"]

    def test_should_never_borrow_another_domains_claimed_equivalent(self) -> None:
        extraction = {"learning": {}, "narrative": {"takeaways": _TAKEAWAYS}}

        result = _with_owner("narrative.takeaways", extraction)

        assert _ids(result) == ["overview", "owner"]

    def test_should_still_borrow_an_unclaimed_equivalent(self) -> None:
        extraction = {"learning": {"takeaways": _TAKEAWAYS}, "narrative": {"takeaways": ["x", "y"]}}

        result = _with_owner("learning.takeaways", extraction)

        assert _ids(result) == ["overview", "facts", "owner"]


# ─── W-A4: a procedure ships whole ───


def test_should_ship_every_step_of_a_30_step_build_in_order() -> None:
    steps = [{"instruction": f"Do build step {i}", "timestamp": 60 * i} for i in range(1, 31)]
    for step in steps[2::4]:
        step["tips"] = "Keep the panel square before fastening."
    tab = {"id": "steps", "label": "30 Steps", "component": "step_player",
           "dataSource": "project.steps", "goal": "Build"}  # fmt: skip

    result = assemble_response(
        _triage("project", ["project"], [tab]), {"project": {"steps": steps}}, None, None,
        {"duration": 1900},
    )  # fmt: skip

    built = next(t for t in result["tabs"] if t["id"] == "steps")
    assert [s["number"] for s in built["props"]["steps"]] == list(range(1, 31))
