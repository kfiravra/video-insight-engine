"""Phase 3.3: extraction groups — budget split, text-group cap, text1, visual re-cut, cassettes."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.models.pipeline_types import PlanResult
from src.services.pipeline.field_groups import ExtractionGroup, build_groups, visual_group
from src.services.pipeline.field_requests import FieldReason, FieldRequest, requested_fields
from src.services.pipeline.plan import _normalize_plan_data
from src.services.pipeline.reconcile import reconcile_plan
from src.services.pipeline.schema_fields import scalar_fields
from src.shared_config.domain_config import GroupingConfig, grouping_config
from src.utils.json_parsing import parse_json_response
from tests.test_field_requests import _request, _tab


def _layout(groups: list[ExtractionGroup]) -> str:
    """``"text1: food.a food.b | text2: food.c"`` — group ids with their fields, in order."""
    return " | ".join(f"{g.group_id}: {' '.join(f.path for f in g.fields)}" for g in groups)


def _ids(*tab_ids: str) -> list[dict]:
    """Synthetic tabs that only carry ids (for build_groups' tab order)."""
    return [_tab(t, "x", "") for t in tab_ids]


def _req(
    path: str, cost: int, tab_id: str, *, reason: FieldReason = "planned", visual: bool = False
) -> FieldRequest:
    """A synthetic list field whose estimated output is ``cost`` tokens (weight × expect 1)."""
    domain, _, field = path.partition(".")
    return FieldRequest(domain, field, "list", reason, (tab_id,), None, 1, cost, visual)


def _config(max_text: int = 5, budget: int = 1500) -> GroupingConfig:
    return {"maxTextGroups": max_text, "groupOutputBudget": budget}


def _fired(groups: list[ExtractionGroup]) -> set[str]:
    return {f.path for g in groups if not g.visual for f in g.fields}


class TestBuildGroups:
    def test_should_put_waits_for_visual_fields_in_one_visual_group(self) -> None:
        tabs = [
            _tab("code", "code_playground", "tech.snippets"),
            _tab("setup", "step_player", "tech.setup.commands"),
            _tab("cheat", "info_grid", "tech.cheatSheet"),
            _tab("topics", "info_grid", "tech.topics"),
        ]

        groups = build_groups(_request(tabs, ["tech"]), tabs, _config(budget=1))

        assert [(g.group_id, g.visual) for g in groups if g.visual] == [("visual", True)]

    def test_should_keep_a_domain_in_one_group_when_it_fits_the_budget(self) -> None:
        fields = [_req("food.a", 500, "t0"), _req("food.b", 500, "t1")]

        groups = build_groups(fields, _ids("t0", "t1"), _config())

        assert _layout(groups) == "text1: food.a food.b"

    def test_should_split_a_domain_by_field_when_weight_times_expect_exceeds_the_budget(
        self,
    ) -> None:
        fields = [_req("food.a", 700, "t0"), _req("food.b", 700, "t1"), _req("food.c", 700, "t2")]

        groups = build_groups(fields, _ids("t0", "t1", "t2"), _config())

        assert _layout(groups) == "text1: food.a food.b | text2: food.c"

    def test_should_never_split_a_single_field(self) -> None:
        fields = [
            _req("food.a", 200, "t0"),
            _req("food.big", 5000, "t1"),
            _req("food.c", 200, "t2"),
        ]

        groups = build_groups(fields, _ids("t0", "t1", "t2"), _config())

        assert _layout(groups) == "text1: food.a | text2: food.big | text3: food.c"

    def test_should_place_a_domains_scalars_in_its_first_group(self) -> None:
        tabs = [
            _tab("ing", "checklist", "food.ingredients"),
            _tab("st", "step_player", "food.steps"),
        ]

        groups = build_groups(_request(tabs, ["food"]), tabs, _config(budget=1))

        assert _layout(groups) == "text1: food.ingredients food.meta | text2: food.steps"

    def test_should_cap_text_groups_at_max_text_groups_by_merging_the_smallest(self) -> None:
        costs = [("a.a1", 900), ("a.a2", 900), ("a.a3", 200), ("b.b1", 900), ("b.b2", 300)]
        costs += [("c.c1", 500), ("d.d1", 500)]
        fields = [_req(path, cost, f"t{i}") for i, (path, cost) in enumerate(costs)]

        groups = build_groups(fields, _ids(*(f"t{i}" for i in range(7))), _config(5, 1000))

        assert _layout(groups) == (
            "text1: a.a1 | text2: a.a2 a.a3 | text3: b.b1 b.b2 | text4: c.c1 | text5: d.d1"
        )

    def test_should_merge_the_two_smallest_across_domains_when_no_same_domain_pair_is_left(
        self,
    ) -> None:
        costs = [("a.x", 500), ("b.x", 100), ("c.x", 400), ("d.x", 50), ("e.x", 600), ("f.x", 700)]
        fields = [_req(path, cost, f"t{i}") for i, (path, cost) in enumerate(costs)]

        groups = build_groups(fields, _ids(*(f"t{i}" for i in range(6))), _config(max_text=5))

        assert (
            _layout(groups) == "text1: a.x | text2: b.x d.x | text3: c.x | text4: e.x | text5: f.x"
        )

    def test_should_make_the_first_planned_tabs_text_group_text1(self) -> None:
        tabs = [
            _tab("tips", "info_grid", "food.tips"),
            _tab("ing", "checklist", "food.ingredients"),
            _tab("steps", "step_player", "food.steps"),
        ]

        groups = build_groups(_request(tabs, ["food"]), tabs, _config(budget=1))

        assert [f.path for f in groups[0].fields] == ["food.tips", "food.meta"]

    def test_should_skip_to_the_next_text_group_when_the_first_tab_is_visual(self) -> None:
        tabs = [
            _tab("code", "code_playground", "tech.snippets"),
            _tab("concepts", "concept_canvas", "learning.concepts"),
            _tab("topics", "info_grid", "tech.topics"),
        ]

        groups = build_groups(_request(tabs, ["tech", "learning"]), tabs, _config(budget=1))

        assert groups[0].tab_ids == ("concepts",)

    def test_should_lead_with_the_planned_group_when_a_fallback_serves_an_earlier_visual_tab(
        self,
    ) -> None:
        fields = [
            _req("v.shot", 100, "visual_tab", visual=True),
            _req("x.spare", 100, "visual_tab", reason="fallback"),
            _req("y.own", 100, "text_tab"),
        ]

        groups = build_groups(fields, _ids("visual_tab", "text_tab"), _config())

        assert _layout(groups) == "text1: y.own | text2: x.spare | visual: v.shot"

    def test_should_order_text_groups_by_their_first_served_tab(self) -> None:
        fields = [_req("a.x", 100, "t2"), _req("b.x", 100, "t0"), _req("c.x", 100, "t1")]

        groups = build_groups(fields, _ids("t0", "t1", "t2"), _config())

        assert _layout(groups) == "text1: b.x | text2: c.x | text3: a.x"

    def test_should_estimate_a_groups_output_as_the_sum_of_weight_times_expect(self) -> None:
        tabs = [_tab("ing", "checklist", "food.ingredients")]
        fields = _request(tabs, ["food"])

        (group,) = build_groups(fields, tabs, _config())

        assert group.est_output_tokens == sum(f.weight * f.expect for f in fields)

    def test_should_list_the_tabs_each_group_serves(self) -> None:
        tabs = [
            _tab("cook_steps", "step_player", "food.steps"),
            _tab("ing", "checklist", "food.ingredients"),
            _tab("key_moments", "moment_track", "food.steps"),
        ]

        groups = build_groups(_request(tabs, ["food"]), tabs, _config(budget=1))

        assert [g.tab_ids for g in groups] == [("cook_steps", "key_moments"), ("ing",)]

    def test_should_put_a_planned_modifier_field_in_its_own_text_group(self) -> None:
        tabs = [
            _tab("packing", "packing_mission", "travel.packingList"),
            _tab("costs", "info_grid", "finance.costs"),
        ]

        groups = build_groups(_request(tabs, ["travel"], ["finance"]), tabs, _config())

        assert _layout(groups) == (
            "text1: travel.packingList travel.bestSeason | text2: finance.costs"
        )


class TestVisualGroup:
    def test_should_return_none_when_every_field_already_fired(self) -> None:
        assert visual_group([_req("food.a", 100, "t0")], {"food.a"}) is None

    def test_should_recut_the_visual_group_when_a_visual_tab_is_dropped_at_frames_done(
        self,
    ) -> None:
        tabs = [
            _tab("code", "code_playground", "tech.snippets"),
            _tab("setup", "step_player", "tech.setup.commands"),
            _tab("topics", "info_grid", "tech.topics"),
        ]
        fired = _fired(build_groups(_request(tabs, ["tech"]), tabs, _config()))

        group = visual_group(_request(tabs[1:], ["tech"]), fired)

        assert group is not None and _layout([group]) == "visual: tech.setup.commands"

    def test_should_include_a_field_a_frames_done_demotion_moved_a_tab_onto(self) -> None:
        plan = [
            _tab("code", "code_playground", "tech.snippets"),
            _tab("concepts", "concept_canvas", "learning.concepts"),
        ]
        fired = _fired(build_groups(_request(plan, ["tech", "learning"]), plan, _config()))
        demoted = [_tab("code", "info_grid", "tech.topics"), plan[1]]

        group = visual_group(_request(demoted, ["tech", "learning"]), fired)

        assert group is not None and _layout([group]) == "visual: tech.topics"


_CASSETTES = Path(__file__).parent / "replay" / "cassettes"


def _span_output(cassette: dict, span: str) -> str:
    return next(e["output"] for e in cassette["llm"] if e["span"] == span)


def _reconciled_plan(video_id: str) -> PlanResult:
    """The cassette's recorded plan through today's parse (``_normalize_plan_data``: registry
    check + plan-parse guard, e.g. jMq8's key_moments re-pointed to food.steps) and reconcile
    at plan-done, with the probe's recorded format. The recorded memory carries no evidence,
    which reconcile reads as no opinion — the same as no memory."""
    cassette = json.loads((_CASSETTES / f"{video_id}.json").read_text())
    content_format = json.loads(_span_output(cassette, "tier_probe"))["format"]
    data = parse_json_response(_span_output(cassette, "plan"))
    _normalize_plan_data(data, content_format)
    return reconcile_plan(PlanResult.model_validate(data), None, content_format)


class TestCassetteCompositions:
    @pytest.mark.parametrize(
        ("video_id", "expected"),
        [
            (
                "T1dQhQAm8Tc",
                "text1: tech.topics"
                " | text2: learning.timestamps learning.keyQuestion learning.summary"
                " | text3: learning.concepts | visual: tech.snippets tech.patterns",
            ),
            (
                "jMq8lEu-of0",
                "text1: food.ingredients food.meta | text2: food.steps | text3: food.tips",
            ),
            (
                "uC45_4nnEAI",
                "text1: gaming.rankings gaming.title | text2: gaming.loadout"
                " | visual: gaming.highlights",
            ),
        ],
    )
    def test_should_group_the_recorded_plan_as_designed(self, video_id: str, expected: str) -> None:
        plan = _reconciled_plan(video_id)
        fields = requested_fields(
            plan.tabs,
            plan.content_tags,
            plan.modifiers,
            scalar_fields,
            primary_tag=plan.primary_tag,
        )

        groups = build_groups(fields, plan.tabs, grouping_config())

        assert _layout(groups) == expected
