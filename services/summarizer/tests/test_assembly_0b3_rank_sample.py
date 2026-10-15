"""Capped lists rank-sampled by richness, never head-sliced (output-quality 0b.3 — R9).

``_cap_tab_items`` cut every capped list at the head (S02's 47 key points lost everything
after 58 min; its 36 concepts lost the closing argument). Every cap now keeps the richest
items — equals even-sampled — in their original (chronological) order, so the tail of a
long video survives.
"""

from __future__ import annotations

from src.services.pipeline.assembly.assemblers_overhaul import assemble_concept_canvas
from src.services.pipeline.assembly.core import _post_process_tabs
from src.services.pipeline.assembly.sampling import concept_richness, item_richness


def _spot(i: int, **extra: object) -> dict:
    return {"name": f"S{i}", "description": "d", **extra}


def _tab(component: str, key: str, items: list) -> dict:
    return {"id": "t", "label": "T", "emoji": "", "component": component, "props": {key: items}}


# ─── Richness scoring ───


class TestItemRichness:
    def test_should_score_a_bare_name_zero(self) -> None:
        assert item_richness({"name": "x"}) == 0

    def test_should_count_evidence_fields_a_timestamp_and_a_long_text(self) -> None:
        item = {"name": "x", "detail": "y" * 120, "evidence": "e", "timestamp": 12}

        assert item_richness(item) == 4

    def test_should_ignore_empty_fields(self) -> None:
        assert item_richness({"name": "x", "detail": "", "tips": [], "timestamp": None}) == 0

    def test_should_score_a_non_dict_zero(self) -> None:
        assert item_richness("just a string") == 0


class TestConceptRichness:
    def test_should_count_example_analogy_timestamp_and_connections(self) -> None:
        concept = {
            "name": "A",
            "definition": "d",
            "example": "e",
            "analogy": "a",
            "timestamp": 3,
            "connections": [{"to": "B", "type": "relatesTo"}] * 5,
        }

        assert concept_richness(concept) == 6

    def test_should_score_a_name_and_definition_zero(self) -> None:
        assert concept_richness({"name": "A", "definition": "d", "connections": []}) == 0


# ─── Tab caps keep the tail ───


class TestCapKeepsTheTail:
    def test_should_keep_the_last_spot_of_an_oversized_flat_list(self) -> None:
        tab = _tab("spot_explorer", "spots", [_spot(i) for i in range(40)])

        _post_process_tabs([tab])

        assert tab["props"]["spots"][-1]["name"] == "S39"
        assert len(tab["props"]["spots"]) == 25

    def test_should_keep_the_original_order(self) -> None:
        spots = [_spot(i, timestamp=i * 10) for i in range(40)]
        spots[38]["evidence"] = "rich"
        tab = _tab("spot_explorer", "spots", spots)

        _post_process_tabs([tab])

        kept = [s["timestamp"] for s in tab["props"]["spots"]]
        assert kept == sorted(kept)

    def test_should_prefer_rich_items_over_bare_ones(self) -> None:
        bare = [{"name": f"B{i}"} for i in range(10)]
        rich = [_spot(i, evidence="e", timestamp=i) for i in range(25)]
        tab = _tab("spot_explorer", "spots", bare + rich)

        _post_process_tabs([tab])

        assert [s["name"] for s in tab["props"]["spots"]] == [r["name"] for r in rich]

    def test_should_even_sample_an_info_grid_of_equals(self) -> None:
        items = [{"key": f"K{i}", "value": "v"} for i in range(40)]
        tab = _tab("info_grid", "items", items)

        _post_process_tabs([tab])

        keys = [it["key"] for it in tab["props"]["items"]]
        assert len(keys) == 20
        assert (keys[0], keys[-1]) == ("K0", "K39")

    def test_should_leave_a_list_under_the_cap_alone(self) -> None:
        tab = _tab("spot_explorer", "spots", [_spot(i) for i in range(5)])

        _post_process_tabs([tab])

        assert len(tab["props"]["spots"]) == 5


# ─── Concept canvas cap ───


def _concept(i: int, **extra: object) -> dict:
    return {"name": f"C{i}", "definition": f"def {i}", "connections": [], **extra}


class TestConceptCanvasCap:
    def test_should_keep_the_closing_concepts_when_they_are_rich(self) -> None:
        concepts = [_concept(i) for i in range(36)]
        for i in range(30, 36):
            concepts[i]["example"] = "late but rich"

        props = assemble_concept_canvas({}, concepts, {}, None)

        assert props is not None
        names = [c["name"] for c in props["concepts"]]
        assert len(names) == 30
        assert names[-6:] == [f"C{i}" for i in range(30, 36)]

    def test_should_keep_the_original_order_and_span_the_list(self) -> None:
        props = assemble_concept_canvas({}, [_concept(i) for i in range(60)], {}, None)

        assert props is not None
        indexes = [int(c["name"][1:]) for c in props["concepts"]]
        assert indexes == sorted(indexes)
        assert (indexes[0], indexes[-1]) == (0, 59)

    def test_should_keep_connections_between_surviving_concepts(self) -> None:
        concepts = [_concept(i) for i in range(40)]
        concepts[0]["connections"] = [{"to": "C39", "type": "causes"}]
        concepts[39]["connections"] = [{"to": "C0", "type": "causes"}]

        props = assemble_concept_canvas({}, concepts, {}, None)

        assert props is not None
        by_name = {c["name"]: c for c in props["concepts"]}
        assert by_name["C0"]["connections"] == [{"to": "C39", "type": "causes"}]
