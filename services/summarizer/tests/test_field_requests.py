"""Phase 3.3: requested extraction fields — planned, fallbacks, side reads, scalars."""

from __future__ import annotations

import math

import pytest

from src.services.pipeline.field_requests import (
    SCALAR_DOMAIN_WEIGHT,
    UNREGISTERED_FIELD_WEIGHT,
    FieldRequest,
    requested_fields,
)
from src.services.pipeline.schema_fields import scalar_fields
from src.shared_config.domain_config import data_source, data_sources


def _tab(tab_id: str, component: str, source: str, expect: int | None = None) -> dict:
    tab: dict = {"id": tab_id, "label": tab_id, "component": component, "dataSource": source}
    if expect is not None:
        tab["brief"] = {"what": "", "where": [], "expect": expect}
    return tab


def _by_path(fields: list[FieldRequest]) -> dict[str, FieldRequest]:
    return {f.path: f for f in fields}


def _request(
    tabs: list[dict], tags: list[str], modifiers: list[str] | None = None
) -> list[FieldRequest]:
    """requested_fields with the real scalar keys; the first content tag is the primary."""
    return requested_fields(tabs, tags, modifiers or [], scalar_fields, primary_tag=tags[0])


class TestPlannedFields:
    def test_should_request_planned_active_siblings_side_reads_and_scalars_when_tabs_are_reconciled(
        self,
    ) -> None:
        tabs = [
            _tab("code", "code_playground", "tech.snippets"),
            _tab("verdict", "comparison", "review.verdict"),
        ]

        fields = _request(tabs, ["tech", "review"])

        assert {f.path: f.reason for f in fields} == {
            "tech.snippets": "planned",
            "tech.patterns": "fallback",
            "review.verdict": "planned",
            "review.pros": "upgrade",
            "review.cons": "upgrade",
            "review.comparisons": "upgrade",
            "review.product": "upgrade",
            "review.rating": "upgrade",
            "review.price": "scalar",
        }

    def test_should_skip_overview_frames_and_enrichment_tabs(self) -> None:
        tabs = [
            _tab("overview", "overview", "learning.keyPoints"),
            _tab("tips", "overview", "food.tips"),
            _tab("filmstrip", "video_filmstrip", "frames"),
            _tab("quiz", "quiz_arena", "enrichment.quiz"),
        ]

        assert _request(tabs, ["learning", "food"]) == []

    @pytest.mark.parametrize("source", ["fitness", "fitness.*"])
    def test_should_request_every_registry_field_of_a_domain_when_the_source_is_bare(
        self, source: str
    ) -> None:
        fields = _request([_tab("all", "info_grid", source)], ["fitness"])

        registry = [p for p, s in data_sources().items() if s["domain"] == "fitness"]
        assert [f.path for f in fields if f.kind != "scalar"] == registry

    def test_should_union_tab_ids_when_two_tabs_read_the_same_field(self) -> None:
        tabs = [
            _tab("cook_steps", "step_player", "food.steps"),
            _tab("key_moments", "moment_track", "food.steps"),
        ]

        fields = _by_path(_request(tabs, ["food"]))

        assert fields["food.steps"].tab_ids == ("cook_steps", "key_moments")

    def test_should_keep_planned_as_the_reason_when_a_planned_field_is_also_a_sibling(
        self,
    ) -> None:
        tabs = [
            _tab("code", "code_playground", "tech.snippets"),
            _tab("patterns", "code_playground", "tech.patterns"),
        ]

        patterns = _by_path(_request(tabs, ["tech"]))["tech.patterns"]

        assert (patterns.reason, patterns.tab_ids) == ("planned", ("code", "patterns"))

    def test_should_default_expect_to_ceil_cap_half_when_the_brief_has_none(self) -> None:
        spec = data_source("food.ingredients")
        assert spec is not None

        fields = _by_path(_request([_tab("ing", "checklist", "food.ingredients")], ["food"]))

        assert fields["food.ingredients"].expect == math.ceil(spec["cap"] / 2)

    def test_should_use_the_brief_expect_when_the_plan_gives_one(self) -> None:
        tabs = [_tab("ing", "checklist", "food.ingredients", expect=7)]

        fields = _by_path(_request(tabs, ["food"]))

        assert fields["food.ingredients"].expect == 7

    def test_should_clamp_the_brief_expect_to_the_cap(self) -> None:
        spec = data_source("food.ingredients")
        assert spec is not None
        tabs = [_tab("ing", "checklist", "food.ingredients", expect=spec["cap"] + 50)]

        fields = _by_path(_request(tabs, ["food"]))

        assert fields["food.ingredients"].expect == spec["cap"]

    def test_should_weigh_a_field_by_its_registry_output_weight(self) -> None:
        spec = data_source("food.steps")
        assert spec is not None

        fields = _by_path(_request([_tab("steps", "step_player", "food.steps")], ["food"]))

        assert fields["food.steps"].weight == spec["outputWeight"]


class TestFallbacksAndSideReads:
    def test_should_not_request_a_sibling_when_its_domain_is_inactive(self) -> None:
        tabs = [_tab("concepts", "concept_canvas", "learning.concepts")]

        paths = [f.path for f in _request(tabs, ["learning"])]

        assert "science.concepts" not in paths

    def test_should_request_a_sibling_when_a_modifier_activates_its_domain(self) -> None:
        tabs = [_tab("moments", "moment_track", "learning.timestamps")]

        fields = _by_path(_request(tabs, ["learning"], ["narrative"]))

        assert fields["narrative.keyMoments"].reason == "fallback"

    @pytest.mark.parametrize(
        ("component", "source", "tags", "upgrades"),
        [
            ("flash_deck", "language.vocabulary", ["learning", "language"], "learning.concepts"),
            ("flash_deck", "language.vocabulary", ["language", "learning"], ""),
            ("lyrics_karaoke", "music.lyrics", ["music"], "music.structure"),
            ("workout_room", "fitness.exercises", ["fitness"], "fitness.warmup fitness.cooldown"),
            (
                "comparison",
                "review.pros",
                ["review"],
                "review.cons review.comparisons review.verdict review.product review.rating",
            ),
            (
                "comparison_radar",
                "review.comparisons",
                ["review"],
                "review.pros review.cons review.verdict review.product review.rating",
            ),
            ("moment_track", "gaming.highlights", ["gaming", "learning"], "learning.timestamps"),
            ("moment_track", "food.steps", ["food"], ""),
        ],
    )
    def test_should_request_the_components_side_reads_as_upgrades(
        self, component: str, source: str, tags: list[str], upgrades: str
    ) -> None:
        fields = _request([_tab("t", component, source)], tags)

        assert " ".join(f.path for f in fields if f.reason == "upgrade") == upgrades


class TestModifiersAndScalars:
    def test_should_request_a_modifier_field_as_given_when_the_registry_lacks_it(self) -> None:
        (costs,) = _request([_tab("costs", "info_grid", "finance.costs")], ["travel"], ["finance"])

        expected = ("finance.costs", "list", ("costs",), None, UNREGISTERED_FIELD_WEIGHT)
        assert (costs.path, costs.kind, costs.tab_ids, costs.cap, costs.weight) == expected

    def test_should_request_a_modifiers_model_keys_when_a_tab_reads_the_bare_modifier(
        self,
    ) -> None:
        fields = _request([_tab("money", "info_grid", "finance")], ["travel"], ["finance"])

        expected = [("finance.costs", ("money",)), ("finance.savingTips", ("money",))]
        assert [(f.path, f.tab_ids) for f in fields] == expected

    def test_should_request_no_modifier_field_when_no_tab_reads_it(self) -> None:
        tabs = [_tab("itinerary", "spot_explorer", "travel.itinerary")]

        fields = _request(tabs, ["travel"], ["finance", "narrative"])

        assert {f.domain for f in fields} == {"travel"}

    def test_should_spread_the_scalar_weight_over_a_domains_scalars(self) -> None:
        fields = _request([_tab("moments", "moment_track", "learning.timestamps")], ["learning"])

        assert sum(f.weight for f in fields if f.kind == "scalar") == SCALAR_DOMAIN_WEIGHT

    def test_should_mark_scalars_visual_when_their_domain_has_only_visual_fields(self) -> None:
        tabs = [_tab("highlights", "moment_track", "gaming.highlights")]

        fields = _by_path(_request(tabs, ["gaming"]))

        assert fields["gaming.title"].visual is True
