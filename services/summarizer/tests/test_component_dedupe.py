"""One tab per component (tab-dedupe hotfix, Kfir 2026-11-04/05).

S02 and S13 shipped two moment_tracks, S06 two checklists, S07 two comparisons: the
plan's first tab of a component family keeps it, a later one drops as
``duplicate_component`` (logged, listed in ``droppedTabs``), and an interview's Q&A
moment_track beside another timeline ships as an info_grid instead. Key Moments joins
only a plan without a moment_track. The rule runs at plan parse and in assembly.
"""

from __future__ import annotations

import json
import logging
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.models.memory_types import MemoryResult
from src.models.pipeline_types import PlanResult
from src.services.pipeline import plan as plan_mod
from src.services.pipeline import plan_prompt
from src.services.pipeline import reconcile
from src.services.pipeline.assembly import assemble_comparison, assemble_response
from src.services.pipeline.assembly.component_dedupe import (
    DUPLICATE_COMPONENT,
    dedupe_components,
    family,
    reroute_duplicates,
)
from src.services.pipeline.assembly.promotion import demote_component, promote_component
from src.services.pipeline.reconcile import reconcile_at_plan_done
from src.services.pipeline.reconcile_pass import ReconcilePass

_OUTLINE = [
    {"start": 0, "end": 300, "title": "The stack", "establishes": "Why the stack matters"},
    {"start": 300, "end": 900, "title": "Agent workflow", "establishes": "How agents ship"},
]
_META = {"duration": 900, "outline": _OUTLINE, "chapters": []}


def _tab(tab_id: str, component: str, source: str) -> dict[str, Any]:
    return {"id": tab_id, "label": tab_id, "component": component, "dataSource": source,
            "goal": "After this you know it."}  # fmt: skip


def _ids(tabs: list[dict]) -> list[str]:
    return [t["id"] for t in tabs]


def _components(result: dict) -> list[str]:
    return [t["component"] for t in result["tabs"]]


def _drops(result: dict) -> list[tuple[str, str]]:
    return [(d["id"], d["reason"]) for d in result["dropped"]]


# ─── The helper ───


class TestDedupeComponents:
    def test_should_keep_the_first_tab_of_a_component(self) -> None:
        tabs = [_tab("qa", "moment_track", "podcast.segments"), _tab("map", "moment_track", "x.y")]

        assert _ids(dedupe_components(tabs, [], stage="test")) == ["qa"]

    def test_should_count_a_radar_as_a_comparison(self) -> None:
        tabs = [_tab("a", "comparison", "review.pros"), _tab("b", "comparison_radar", "r.c")]

        assert _ids(dedupe_components(tabs, [], stage="test")) == ["a"]

    def test_should_count_a_step_canvas_as_a_step_player(self) -> None:
        tabs = [_tab("a", "step_flow_canvas", "food.steps"), _tab("b", "step_player", "x.y")]

        assert _ids(dedupe_components(tabs, [], stage="test")) == ["a"]

    def test_should_let_a_grid_and_spot_cards_share_a_page(self) -> None:
        tabs = [_tab("a", "spot_explorer", "x.a"), _tab("b", "info_grid", "x.b")]

        assert _ids(dedupe_components(tabs, [], stage="test")) == ["a", "b"]

    def test_should_never_touch_the_overview(self) -> None:
        tabs = [_tab("overview", "overview", ""), _tab("again", "overview", "")]

        assert _ids(dedupe_components(tabs, [], stage="test")) == ["overview", "again"]

    def test_should_record_the_drop_with_its_source_and_the_keeper(self) -> None:
        dropped: list[dict] = []
        tabs = [_tab("a", "checklist", "food.ingredients"), _tab("b", "checklist", "food.x")]

        dedupe_components(tabs, dropped, stage="test")

        assert dropped == [{"id": "b", "component": "checklist", "dataSource": "food.x",
                            "reason": DUPLICATE_COMPONENT, "detail": "kept a"}]  # fmt: skip

    def test_should_log_the_drop(self, caplog: pytest.LogCaptureFixture) -> None:
        tabs = [_tab("a", "checklist", "food.ingredients"), _tab("b", "checklist", "food.x")]

        with caplog.at_level(logging.WARNING):
            dedupe_components(tabs, [], stage="test")

        assert "TAB DROPPED (test): id='b'" in caplog.text


class TestRerouteDuplicates:
    def test_should_ship_a_qa_timeline_beside_another_as_an_info_grid(self) -> None:
        tabs = [_tab("qa", "moment_track", "podcast.qa"),
                _tab("map", "moment_track", "podcast.segments")]  # fmt: skip

        assert [t["component"] for t in reroute_duplicates(tabs)] == ["info_grid", "moment_track"]

    def test_should_keep_a_lone_qa_timeline(self) -> None:
        tabs = [_tab("qa", "moment_track", "podcast.qa"), _tab("p", "spot_explorer", "l.k")]

        assert [t["component"] for t in reroute_duplicates(tabs)] == [
            "moment_track",
            "spot_explorer",
        ]

    def test_should_reroute_beside_a_planned_grid(self) -> None:
        """The timeline is never risked for a grid: the plan-done fold or assembly's
        first-that-ships rule settles the two grids."""
        tabs = [_tab("qa", "moment_track", "podcast.qa"),
                _tab("topics", "info_grid", "podcast.topics"),
                _tab("map", "moment_track", "podcast.segments")]  # fmt: skip

        assert [t["component"] for t in reroute_duplicates(tabs)] == [
            "info_grid",
            "info_grid",
            "moment_track",
        ]


# ─── The first tab wins (Kfir 2026-11-05) ───


def _shipped(result: dict) -> list[tuple[str, str]]:
    return [(t["id"], t["component"]) for t in result["tabs"] if t["component"] != "overview"]


class TestFirstTabWins:
    def test_should_route_the_later_spot_tab_to_a_grid_when_the_tier_list_demotes(self) -> None:
        """S09: the opening tier list nobody ranked demotes to spot cards; the later
        planned spot tab is the one that walks its ladder, to an info_grid."""
        pulls = [{"item": f"Card {i}", "reason": "why it matters", "timestamp": 60 * i}
                 for i in range(1, 5)]  # fmt: skip
        needs = [{"name": f"Need {i}", "description": f"What need {i} is for in the deck.",
                  "timestamp": 300 + 60 * i} for i in range(1, 4)]  # fmt: skip
        triage = {"primaryTag": "gaming", "contentTags": ["gaming"], "tabs": [
            _tab("pulls", "tier_list", "gaming.rankings"),
            _tab("deck_needs", "spot_explorer", "gaming.loadout"),
        ]}  # fmt: skip

        result = assemble_response(
            triage, {"gaming": {"rankings": pulls, "loadout": needs}}, None, None, video_meta=_META
        )
        needs_tab = next(t for t in result["tabs"] if t["id"] == "deck_needs")

        assert ("pulls", "spot_explorer") in _shipped(result)
        assert (needs_tab["component"], needs_tab.get("degradedFrom")) == (
            "info_grid",
            "spot_explorer",
        )

    def test_should_let_an_earlier_grid_promote_past_a_later_spot_tab(self) -> None:
        """Only earlier tabs count: the grid promotes to spot cards, and the later
        planned spot tab ships as a grid instead."""
        long = "a row long enough to read as a card, " * 5
        grid = [{"key": f"Row {i}", "value": f"{long}{i}"} for i in range(3)]
        spots = [{"name": f"Spot {i}", "description": f"Why spot {i} matters here.",
                  "timestamp": 60 * i} for i in range(1, 4)]  # fmt: skip
        triage = {"primaryTag": "tech", "contentTags": ["tech"], "tabs": [
            _tab("facts", "info_grid", "tech.cheatSheet"),
            _tab("topics", "spot_explorer", "tech.topics"),
        ]}  # fmt: skip

        result = assemble_response(
            triage, {"tech": {"cheatSheet": grid, "topics": spots}}, None, None, video_meta=_META
        )

        assert [(i, c) for i, c in _shipped(result) if i in ("facts", "topics")] == [
            ("facts", "spot_explorer"),
            ("topics", "info_grid"),
        ]

    def test_should_drop_a_later_tab_planned_in_the_same_family(self) -> None:
        """Planned twice (a cached plan): the later one drops, it does not walk its ladder."""
        segments = [{"title": f"Part {i}", "summary": f"What part {i} concluded.",
                     "timestamp": 100 * i} for i in range(1, 5)]  # fmt: skip
        result = _podcast(
            _tab("map", "moment_track", "podcast.segments"),
            _tab("beats", "moment_track", "learning.timestamps"),
            extraction={"podcast": {"segments": segments}},
        )

        assert "beats" not in _ids(result["tabs"])


# ─── Promotion and demotion never land on a component another tab holds ───


_LONG = "x " * 80


class TestPromotionAndDemotion:
    def test_should_skip_a_promotion_onto_a_family_another_tab_holds(self) -> None:
        props = {"items": [{"key": f"k{i}", "value": _LONG} for i in range(3)]}
        data = [{"key": f"k{i}", "value": _LONG} for i in range(3)]

        component, _ = promote_component(
            "info_grid", props, data, {}, "learning", taken={"spot_explorer"}
        )

        assert component == "info_grid"

    def test_should_skip_a_demotion_rung_another_tab_holds(self) -> None:
        tab = _tab("pulls", "tier_list", "gaming.rankings")
        data = [{"item": f"Card {i}", "reason": "why it matters", "timestamp": 60 * i}
                for i in range(1, 5)]  # fmt: skip

        demoted = demote_component("tier_list", tab, data, {}, None, blocked={"spot_explorer"})

        assert demoted is not None and demoted[0] != "spot_explorer"


# ─── Through the assembler ───


def _podcast(*tabs: dict, extraction: dict | None = None) -> dict:
    triage = {"primaryTag": "podcast", "contentTags": ["podcast", "learning"],
              "contentFormat": "podcast", "tabs": list(tabs)}  # fmt: skip
    segments = [{"title": f"Part {i}", "summary": f"What part {i} concluded.",
                 "timestamp": 100 * i, "speaker": "Guest"} for i in range(1, 5)]  # fmt: skip
    return assemble_response(
        triage, extraction or {"podcast": {"segments": segments}}, None, None, video_meta=_META
    )


class TestAssembly:
    def test_should_ship_one_moment_track_when_the_plan_has_two(self) -> None:
        result = _podcast(_tab("map", "moment_track", "podcast.segments"),
                          _tab("beats", "moment_track", "learning.timestamps"))  # fmt: skip

        assert _components(result).count("moment_track") == 1

    def test_should_keep_the_first_planned_moment_track(self) -> None:
        result = _podcast(_tab("map", "moment_track", "podcast.segments"),
                          _tab("beats", "moment_track", "learning.timestamps"))  # fmt: skip

        assert [t["id"] for t in result["tabs"] if t["component"] == "moment_track"] == ["map"]

    def test_should_record_the_later_one_as_a_duplicate_component(self) -> None:
        result = _podcast(_tab("map", "moment_track", "podcast.segments"),
                          _tab("beats", "moment_track", "learning.timestamps"))  # fmt: skip

        assert ("beats", DUPLICATE_COMPONENT) in _drops(result)

    def test_should_hand_the_slot_on_when_the_first_tab_dies(self) -> None:
        """The first planned checklist ships nothing, so the next one keeps the component."""
        items = [{"item": f"Item {i}", "note": "why"} for i in range(1, 4)]
        result = _podcast(
            _tab("first", "checklist", "learning.takeaways"),
            _tab("second", "checklist", "learning.keyPoints"),
            extraction={"learning": {"takeaways": [], "keyPoints": items}},
        )

        assert [i for i, c in _shipped(result) if c == "checklist"] == ["second"]

    def test_should_keep_the_timeline_when_an_interviews_qa_empties(self) -> None:
        """Review p1 (major 1): a planned grid beside an interview whose Q&A empties. The
        Q&A re-routes to a grid and dies as one; the Conversation map ships."""
        topics = [{"topic": "Raft", "detail": "Consensus"}, {"topic": "CAP", "detail": "Trade"},
                  {"topic": "Paxos", "detail": "Older"}]  # fmt: skip
        segments = [{"title": f"Part {i}", "summary": f"What part {i} concluded.",
                     "timestamp": 100 * i} for i in range(1, 5)]  # fmt: skip
        result = _podcast(
            _tab("qa", "moment_track", "podcast.qa"),
            _tab("topics", "info_grid", "podcast.topics"),
            _tab("map", "moment_track", "podcast.segments"),
            extraction={"podcast": {"segments": segments, "topics": topics, "qa": []}},
        )

        assert [i for i, c in _shipped(result) if c in ("moment_track", "info_grid")] == [
            "topics",
            "map",
        ]

    def test_should_drop_an_unfolded_grid_that_repeats_a_shipped_one(self) -> None:
        """The backstop: a host that ships nothing gives its folded grid back as a tab
        (R6), which no loop check sees; the pass over the assembled tabs drops it."""
        facts = [{"key": f"Fact {i}", "value": f"Value {i}"} for i in range(3)]
        more = [{"key": f"More {i}", "value": f"Other {i}"} for i in range(3)]
        host = {**_tab("host", "spot_explorer", "tech.topics"),
                "folds": [{"id": "more", "label": "More", "dataSource": "tech.patterns",
                           "brief": {"what": "", "where": [], "expect": 3}}]}  # fmt: skip
        triage = {"primaryTag": "tech", "contentTags": ["tech"], "tabs": [
            _tab("facts", "info_grid", "tech.cheatSheet"), host,
        ]}  # fmt: skip
        extraction = {"tech": {"cheatSheet": facts, "patterns": more, "topics": []}}

        result = assemble_response(triage, extraction, None, None, video_meta=_META)

        assert [i for i, c in _shipped(result) if c == "info_grid"] == ["facts"]
        assert ("more", DUPLICATE_COMPONENT) in _drops(result)


class TestKeyMoments:
    def test_should_add_key_moments_when_the_plan_has_no_moment_track(self) -> None:
        result = _podcast(_tab("p", "spot_explorer", "learning.keyIdeas"),
                          extraction={"learning": {"keyIdeas": []}})  # fmt: skip

        assert "key_moments" in _ids(result["tabs"])

    def test_should_not_replace_a_planned_moment_track_that_died(self) -> None:
        """Path A: the planned Moments tab ships the spot cards' items again, drops as
        their twin, and no outline Key Moments stands in for it."""
        items = [{"name": f"Topic {i}", "label": f"Topic {i}", "timestamp": 60 * i,
                  "description": "What it is and why it matters here."} for i in range(1, 5)]  # fmt: skip
        triage = {"primaryTag": "tech", "contentTags": ["tech"], "tabs": [
            _tab("topics", "spot_explorer", "tech.topics"),
            _tab("moments", "moment_track", "tech.topics"),
            _tab("code", "code_playground", "tech.snippets"),
        ]}  # fmt: skip
        snippets = [{"filename": "a.py", "language": "python", "code": "x = 1",
                     "explanation": "demo"}]  # fmt: skip

        result = assemble_response(
            triage, {"tech": {"topics": items, "snippets": snippets}}, None, None, video_meta=_META
        )

        assert [t for t in _components(result) if t == "moment_track"] == []


class TestReview:
    def test_should_ship_one_comparison_tab_with_rows_pros_and_cons(self) -> None:
        rows = [{"feature": f"F{i}", "thisProduct": "better", "competitor": "worse",
                 "competitorName": "Sony"} for i in range(3)]  # fmt: skip
        review = {"product": "AirPods", "pros": ["Great ANC"], "cons": ["Pricey"],
                  "comparisons": rows}  # fmt: skip
        triage = {"primaryTag": "review", "contentTags": ["review"], "tabs": [
            _tab("pros_cons", "comparison", "review.pros"),
            _tab("vs", "comparison", "review.comparisons"),
        ]}  # fmt: skip

        result = assemble_response(triage, {"review": review}, None, None)
        props = next(t["props"] for t in result["tabs"] if t["component"] == "comparison")

        assert (_components(result).count("comparison"), len(props["comparisons"]),
                props["pros"], props["cons"]) == (1, 3, ["Great ANC"], ["Pricey"])  # fmt: skip


# ─── Plan parse ───


async def _run_plan(tabs: list[dict]) -> PlanResult:
    answer = json.dumps({"contentTags": ["podcast", "learning"], "primaryTag": "podcast",
                         "confidence": 0.9, "evidence": {}, "tabs": tabs})  # fmt: skip
    with (
        patch.object(plan_prompt, "_load_plan_prompt", return_value="<video>{title}</video>"),
        patch.object(plan_prompt, "_load_component_toolkit", return_value=""),
        patch.object(plan_mod, "call_llm_with_retry", AsyncMock(return_value=answer)),
    ):
        return await plan_mod.run_plan(
            title="T", channel="C", description="", duration=2400, category_hint="podcast",
            content_format="interview", transcript="", llm_service=MagicMock(),
        )  # fmt: skip


class TestPlanParse:
    async def test_should_route_the_interview_qa_to_a_grid_beside_the_map(self) -> None:
        result = await _run_plan([
            _tab("positions", "spot_explorer", "learning.keyIdeas"),
            _tab("qa", "moment_track", "podcast.qa"),
            _tab("map", "moment_track", "podcast.segments"),
        ])  # fmt: skip

        assert [(t["id"], t["component"]) for t in result.tabs] == [
            ("positions", "spot_explorer"), ("qa", "info_grid"), ("map", "moment_track"),
        ]  # fmt: skip

    async def test_should_route_the_qa_beside_a_planned_grid(self) -> None:
        """Review p1 (major 1): the planned grid no longer blocks the re-route, and the
        Conversation map reaches extraction."""
        result = await _run_plan([
            _tab("positions", "spot_explorer", "learning.keyIdeas"),
            _tab("qa", "moment_track", "podcast.qa"),
            _tab("topics", "info_grid", "podcast.topics"),
            _tab("map", "moment_track", "podcast.segments"),
        ])  # fmt: skip

        assert [(t["id"], t["component"]) for t in result.tabs] == [
            ("positions", "spot_explorer"), ("qa", "info_grid"),
            ("topics", "info_grid"), ("map", "moment_track"),
        ]  # fmt: skip

    async def test_should_keep_a_repeated_component_for_assembly_to_settle(self) -> None:
        """Whether the first checklist ships is known in assembly only, so plan parse
        drops neither."""
        result = await _run_plan([
            _tab("positions", "spot_explorer", "learning.keyIdeas"),
            _tab("asks", "checklist", "learning.takeaways"),
            _tab("map", "moment_track", "podcast.segments"),
            _tab("more", "checklist", "learning.keyPoints"),
        ])  # fmt: skip

        assert {"asks", "more"} <= set(_ids(result.tabs))

    @pytest.mark.parametrize("domain", ["tech", "fitness", "review"])
    def test_should_build_a_fallback_plan_with_one_tab_per_component(self, domain: str) -> None:
        """Review p1 (minor 3): tech, fitness and review list a component twice in their
        defaultTabs; the fallback plan keeps the first."""
        families = [family(t["component"]) for t in plan_mod._fallback_tabs(domain)]

        assert len(families) == len(set(families))


# ─── Reconcile never fills with a planned component ───


class TestReconcileFill:
    def test_should_never_fill_with_a_component_already_planned(self) -> None:
        plan = PlanResult.model_validate({
            "contentTags": ["learning"], "primaryTag": "learning", "confidence": 0.9,
            "evidence": {}, "tabs": [
                _tab("ideas", "spot_explorer", "learning.keyIdeas"),
                _tab("canvas", "concept_canvas", "learning.keyPoints"),
            ],
        })  # fmt: skip

        outcome = reconcile_at_plan_done(plan, MemoryResult(), None)
        components = [t["component"] for t in outcome.tabs]

        assert len(components) == len(set(components))


# ─── Plan-done reconcile: the evidence demotion, first tab wins ───


def _tech_plan(*tabs: dict) -> PlanResult:
    return PlanResult.model_validate({
        "contentTags": ["tech"], "primaryTag": "tech", "confidence": 0.9,
        "evidence": {"has_claims": False}, "tabs": list(tabs),
    })  # fmt: skip


_CLAIMS = _tab("claims", "claims_tracker", "tech.claims")
_PATTERNS = _tab("patterns", "info_grid", "tech.patterns")


class TestReconcileDemotion:
    """``has_claims`` doubly false: the claims tracker demotes to an info_grid."""

    def test_should_drop_a_later_grid_when_an_earlier_demotion_takes_the_component(self) -> None:
        outcome = reconcile_at_plan_done(
            _tech_plan(_CLAIMS, _PATTERNS), MemoryResult(evidence={"has_claims": False}), None
        )
        grids = [t["id"] for t in outcome.tabs if t["component"] == "info_grid"]

        assert grids == ["claims"]
        assert ("patterns", "reconcile_duplicate_component") in [
            (d["id"], d["reason"]) for d in outcome.dropped
        ]

    def test_should_never_demote_onto_a_component_an_earlier_tab_holds(self) -> None:
        outcome = reconcile_at_plan_done(
            _tech_plan(_PATTERNS, _CLAIMS), MemoryResult(evidence={"has_claims": False}), None
        )

        assert [t["id"] for t in outcome.tabs if t["component"] == "info_grid"] == ["patterns"]


class TestReconcileChainedMoves:
    """Review p1 (nit 9): a tab moved down its ladder by the settle pass holds its rung."""

    def test_should_not_let_a_moved_tab_share_its_rung_with_a_later_one(self) -> None:
        run = ReconcilePass(
            tabs=[
                _tab("drills", "checklist", "fitness.exercises"),
                _tab("needs", "checklist", "gaming.loadout"),
                _tab("grid", "info_grid", "gaming.rankings"),
            ],
            active=["fitness", "gaming"], evidence={}, stage="plan_done", readers={},
        )  # fmt: skip

        reconcile._settle_demoted_components(run, {"drills"})

        assert [(t["id"], t["component"]) for t in run.tabs] == [
            ("drills", "checklist"),
            ("needs", "info_grid"),
        ]
        assert [d["id"] for d in run.dropped] == ["grid"]


class TestReviewVerdictPath:
    """Review p1 (major 2): a comparison tab on the review's verdict dict ships the
    reviewer's pros and cons from the review block."""

    def test_should_ship_pros_and_cons_on_a_tab_planned_on_the_verdict(self) -> None:
        review = {"product": "AirPods", "pros": ["Great ANC"], "cons": ["Pricey"],
                  "verdict": {"bottomLine": "Buy it"}}  # fmt: skip
        tab = _tab("verdict", "comparison", "review.verdict")

        props = assemble_comparison(tab, review["verdict"], {"review": review}, None)

        assert props is not None and (props["pros"], props["cons"]) == (["Great ANC"], ["Pricey"])
