"""p1.7 — grouping and sibling requests (L16, L17) and R5 (no key without a value).

A talk is cut into at most three text groups per chunk: key ideas with the
domain scalars, the moments, and nothing more — a slice under
``grouping.minGroupOutput`` (S01's asks, ~300 tokens) rides with the slice before
it instead of costing a call that re-reads the cached transcript. A registry
sibling is requested only inside a domain some tab plans on (S01 paid for
``narrative.*``, S03 for ``science.concepts``), and a Moments tab on a field of
its own reads no ``learning.timestamps`` stand-in (S13). R5 (Kfir 2026-10-21):
an info_grid row ships with a value — its evidence, or its category badge over
its text — or not at all; a grid of short names is a tag cloud.
"""

from __future__ import annotations

from src.services.pipeline.assembly import assemble_response
from src.services.pipeline.assembly.info_grid_items import require_grid_values, valued_row
from src.services.pipeline.field_groups import build_groups
from src.services.pipeline.field_requests import requested_fields
from src.services.pipeline.schema_fields import scalar_fields
from src.shared_config.domain_config import data_source, grouping_config


def _tab(tab_id: str, component: str, source: str, expect: int | None = None) -> dict:
    tab: dict = {"id": tab_id, "label": tab_id, "component": component, "dataSource": source}
    if expect is not None:
        tab["brief"] = {"what": "", "where": [], "expect": expect}
    return tab


# S01's plan on the phase-1 iteration run (63-min talk; learning + the narrative modifier).
_S01 = [
    _tab("key_ideas", "spot_explorer", "learning.keyIdeas", 14),
    _tab("moments", "moment_track", "learning.timestamps", 13),
    _tab("asks", "checklist", "learning.takeaways", 5),
    _tab("quiz", "quiz_arena", "enrichment.quiz", 8),
]
# S13's plan (42-min guest show).
_S13 = [
    _tab("positions", "spot_explorer", "learning.keyIdeas", 12),
    _tab("qa", "moment_track", "podcast.qa", 6),
    _tab("conversation_map", "moment_track", "podcast.segments", 10),
]


def _paths(tabs: list[dict], tags: list[str], modifiers: list[str] | None = None) -> set[str]:
    fields = requested_fields(tabs, tags, modifiers or [], scalar_fields, primary_tag=tags[0])
    return {f.path for f in fields}


def _text_groups(tabs: list[dict], tags: list[str], modifiers: list[str] | None = None) -> list:
    fields = requested_fields(tabs, tags, modifiers or [], scalar_fields, primary_tag=tags[0])
    return [g for g in build_groups(fields, tabs, grouping_config()) if not g.visual]


# ─── Grouping ───


class TestTalkGroups:
    def test_should_cut_a_talk_into_at_most_three_text_groups(self) -> None:
        assert len(_text_groups(_S01, ["learning"], ["narrative"])) <= 3

    def test_should_let_the_asks_ride_with_the_moments_instead_of_a_call_of_their_own(
        self,
    ) -> None:
        groups = _text_groups(_S01, ["learning"], ["narrative"])
        layout = [sorted(f.path for f in g.fields if f.kind != "scalar") for g in groups]

        assert layout == [["learning.keyIdeas"], ["learning.takeaways", "learning.timestamps"]]

    def test_should_weigh_a_key_idea_as_measured_on_the_s01_run(self) -> None:
        spec = data_source("learning.keyIdeas")

        assert spec is not None and spec["outputWeight"] == 203

    def test_should_keep_a_slice_of_its_own_when_it_is_worth_a_call(self) -> None:
        tabs = [
            _tab("i", "checklist", "food.ingredients", 20),
            _tab("s", "step_player", "food.steps", 16),
            _tab("t", "spot_explorer", "food.tips", 9),
        ]

        assert len(_text_groups(tabs, ["food"])) == 3


# ─── Sibling and side-read requests ───


class TestRequestsOnlyWhatAPlannedTabReads:
    def test_should_request_no_narrative_field_when_no_tab_plans_on_narrative(self) -> None:
        paths = _paths(_S01, ["learning"], ["narrative"])

        assert not {p for p in paths if p.startswith("narrative.")}

    def test_should_request_no_science_concepts_when_no_tab_plans_on_science(self) -> None:
        tabs = [_tab("concepts", "concept_canvas", "learning.concepts", 12)]

        assert "science.concepts" not in _paths(tabs, ["learning", "science"])

    def test_should_request_no_learning_timestamps_when_the_moments_have_their_own(self) -> None:
        assert "learning.timestamps" not in _paths(_S13, ["podcast", "learning"])

    def test_should_cut_s13_into_three_text_groups(self) -> None:
        assert len(_text_groups(_S13, ["podcast", "learning"])) == 3


# ─── R5: no key without a value ───


class TestValuedRows:
    def test_should_keep_a_row_with_a_value(self) -> None:
        row = {"key": "Display", "value": "6.7-inch OLED"}

        assert valued_row(row) == row

    def test_should_give_a_key_only_row_its_evidence_as_the_value(self) -> None:
        row = {"key": "Brown in batches", "value": "", "evidence": "so the meat sears"}

        assert valued_row(row) == {"key": "Brown in batches", "value": "so the meat sears"}

    def test_should_head_a_key_only_tip_by_its_category(self) -> None:
        row = {"key": "Salt the pasta water like the sea.", "value": "", "category": "Chef tip"}

        assert valued_row(row) == {"key": "Chef tip", "value": "Salt the pasta water like the sea."}

    def test_should_drop_a_key_only_row_with_nothing_to_give(self) -> None:
        assert valued_row({"key": "Use LangGraph when you need state.", "value": ""}) is None


class TestShippedGrids:
    def _grid(self, items: list[dict]) -> dict:
        return {
            "id": "rules",
            "label": "Rules",
            "component": "info_grid",
            "props": {"items": items},
        }

    def test_should_drop_the_valueless_rows_of_a_mixed_grid(self) -> None:
        grid = self._grid(
            [
                {"key": "Rule one", "value": "Do this"},
                {"key": "A sentence with no value at all here.", "value": ""},
            ]
        )

        require_grid_values([grid], [])

        assert [r["key"] for r in grid["props"]["items"]] == ["Rule one"]

    def test_should_ship_a_grid_of_short_names_as_a_tag_cloud(self) -> None:
        grid = self._grid([{"key": "Rails", "value": ""}, {"key": "Hotwire", "value": ""}])

        require_grid_values([grid], [])

        assert grid["props"]["mode"] == "tag_cloud"

    def test_should_drop_a_grid_tab_left_without_a_valued_row(self) -> None:
        grid = self._grid(
            [
                {"key": "One long sentence that is the whole tip.", "value": ""},
                {"key": "Another long sentence that is a tip too.", "value": ""},
                {"key": "Rails", "value": ""},
            ]
        )
        tabs, dropped = [grid], []

        require_grid_values(tabs, dropped)

        assert (tabs, dropped[0]["reason"]) == ([], "validation_empty_list")

    def test_should_value_the_rows_of_a_folded_grid_attachment(self) -> None:
        items = [
            {"key": "Tip", "value": "Brown in batches"},
            {"key": "No value here at all.", "value": ""},
        ]
        host = {
            "id": "steps",
            "component": "step_player",
            "props": {"steps": []},
            "attachments": [
                {"slot": "bottom", "component": "info_grid", "props": {"items": items}}
            ],
        }

        require_grid_values([host], [])

        assert host["attachments"][0]["props"]["items"] == [
            {"key": "Tip", "value": "Brown in batches"}
        ]

    def test_should_ship_no_key_only_chip_when_a_response_assembles(self) -> None:
        takeaways = [
            "Use Redis for sessions — it scales to 10x users",
            "A long takeaway sentence with no delimiter at all that ships whole.",
        ]
        triage = {
            "primaryTag": "learning",
            "contentTags": ["learning"],
            "tabs": [_tab("rules", "info_grid", "learning.takeaways")],
        }

        result = assemble_response(triage, {"learning": {"takeaways": takeaways}}, None, None)
        rules = next(t for t in result["tabs"] if t["id"] == "rules")

        assert all(row["value"] for row in rules["props"]["items"])
