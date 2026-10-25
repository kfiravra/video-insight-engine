"""Post-promotion dedupe, sibling-fallback refusal, cross-tab links claimable by
several tabs, response-level attachments (output-quality 0b.5 — R5, L26, audit §2 #7/#8).

Reconcile dedupes planned tabs by (dataSource, component) before extraction; promotion
(flash_deck → concept_canvas) and the sibling fallback still made two tabs ship the
same items (S03's two canvases). Assembly now hashes every tab's items: a tab whose
items overlap an earlier tab's by ≥ 50 % sheds the shared ones, and goes only when none
of its own is left (phase-0 review W-A2: an item one tab ships is never lost). It also
refuses a sibling another tab reads, lets several tabs link one target (never the
overview), and decorates the response through ``attach_response_secondaries`` (answer
tab = first content tab).
"""

from __future__ import annotations

import pytest

from src.services.pipeline.assembly import assemble_response
from src.services.pipeline.assembly.core import (
    DUPLICATE_ITEMS,
    DUPLICATE_TAB,
    _dedupe_tabs,
    _link_tabs,
    tab_fingerprints,
)

_CONCEPTS = [
    {"name": "Body influences brain", "definition": "d1", "connections": ["Brain influences body"]},
    {"name": "Brain influences body", "definition": "d2", "connections": ["Body influences brain"]},
    {"name": "Categorical thinking", "definition": "d3", "connections": []},
]
_QUIZ = [
    {"question": f"Q{i}?", "options": ["a", "b", "c", "d"], "correctIndex": 0, "explanation": "a"}
    for i in range(3)
]


def _learning_triage(*tabs: dict) -> dict:
    return {"contentTags": ["learning"], "primaryTag": "learning", "tabs": list(tabs)}


def _tab(tab_id: str, component: str, data_source: str, label: str | None = None) -> dict:
    return {
        "id": tab_id,
        "label": label or tab_id,
        "emoji": "",
        "component": component,
        "dataSource": data_source,
        "goal": f"Goal of {tab_id}",
    }


def _assembled(tab_id: str, component: str, items: list, key: str = "items") -> dict:
    return {"id": tab_id, "label": tab_id, "component": component, "props": {key: items}}


def _keys(*names: str) -> list[dict]:
    return [{"label": name} for name in names]


def _canvas(tab_id: str, groups: list[str]) -> dict:
    concepts = [{"name": f"Concept {c}", "definition": "d"} for c in "ABCD"]
    props = {"concepts": concepts, "groups": groups}
    return {"id": tab_id, "label": tab_id, "component": "concept_canvas", "props": props}


# ─── Fingerprints and the overlap rule ───


class TestTabFingerprints:
    def test_should_take_each_items_first_text_field(self) -> None:
        tab = _assembled("t", "spot_explorer", [{"name": "A", "description": "x"}, "B"], "spots")

        assert tab_fingerprints(tab) == frozenset({"a", "b"})

    def test_should_read_only_the_components_own_list(self) -> None:
        tab = _canvas("t", ["Group 1", "Group 2"])

        assert tab_fingerprints(tab) == frozenset(f"concept {c}" for c in "abcd")

    def test_should_read_every_list_of_an_unregistered_component(self) -> None:
        tab = {"component": "display_section", "props": {"a": ["x"], "b": [{"title": "y"}]}}

        assert tab_fingerprints(tab) == frozenset({"x", "y"})

    def test_should_read_a_comparisons_rows_pros_and_cons(self) -> None:
        props = {"comparisons": [{"feature": "Battery"}], "pros": ["Light"], "cons": ["Pricey"]}

        fingerprints = tab_fingerprints({"component": "comparison", "props": props})

        assert fingerprints == frozenset({"battery", "light", "pricey"})

    def test_should_identify_a_code_snippet_by_its_code(self) -> None:
        snippets = [
            {"filename": "app.py", "code": "x = 1"},
            {"filename": "app.py", "code": "x = 2"},
        ]
        tab = _assembled("t", "code_playground", snippets, "snippets")

        assert tab_fingerprints(tab) == frozenset({"x = 1", "x = 2"})

    def test_should_identify_a_budget_row_by_its_category(self) -> None:
        tab = _assembled("t", "budget", [{"category": "Food", "amount": 20}], "breakdown")

        assert tab_fingerprints(tab) == frozenset({"food"})

    def test_should_fold_case_and_whitespace(self) -> None:
        tab = _assembled("t", "info_grid", [{"key": "  Great   Filter "}])

        assert tab_fingerprints(tab) == frozenset({"great filter"})

    def test_should_be_empty_without_lists(self) -> None:
        assert (
            tab_fingerprints({"id": "o", "component": "overview", "props": {"tldr": "x"}}) == set()
        )


class TestDedupeTabs:
    def test_should_drop_a_later_tab_repeating_an_earlier_tabs_items(self) -> None:
        items = [{"name": f"C{i}"} for i in range(6)]
        tabs = [_assembled("a", "concept_canvas", items, "concepts"),
                _assembled("b", "concept_canvas", list(items), "concepts")]  # fmt: skip
        dropped: list[dict] = []

        _dedupe_tabs(tabs, dropped)

        assert [t["id"] for t in tabs] == ["a"]

    def test_should_record_the_drop_with_the_tab_it_repeats(self) -> None:
        items = [{"name": f"C{i}"} for i in range(6)]
        tabs = [
            _assembled("a", "info_grid", items),
            _assembled("b", "spot_explorer", items, "spots"),
        ]
        dropped: list[dict] = []

        _dedupe_tabs(tabs, dropped)

        assert (dropped[0]["id"], dropped[0]["reason"]) == ("b", DUPLICATE_TAB)
        assert "a" in dropped[0]["detail"]

    def test_should_trim_a_half_overlapping_twin_to_its_own_items(self) -> None:
        tabs = [
            _assembled("a", "info_grid", _keys(*"xyzw")),
            _assembled("b", "info_grid", _keys(*"xypq")),
        ]

        _dedupe_tabs(tabs, [])

        assert [(t["id"], t["props"]["items"]) for t in tabs] == [
            ("a", _keys(*"xyzw")),
            ("b", _keys("p", "q")),
        ]

    def test_should_record_the_trim_as_duplicate_items_not_a_drop(self) -> None:
        tabs = [
            _assembled("a", "info_grid", _keys(*"xyzw")),
            _assembled("b", "info_grid", _keys(*"xypq")),
        ]
        dropped: list[dict] = []

        _dedupe_tabs(tabs, dropped)

        assert [(d["id"], d["reason"], d["detail"]) for d in dropped] == [
            ("b", DUPLICATE_ITEMS, "2 of its 4 items ship elsewhere (a)")
        ]

    def test_should_remove_the_dropped_twin_itself_never_an_equal_earlier_tab(self) -> None:
        first = _assembled("a", "info_grid", _keys("x", "y"))
        tabs = [first, dict(first)]

        _dedupe_tabs(tabs, [])

        assert tabs[0] is first and len(tabs) == 1

    def test_should_keep_a_tab_below_half_overlap(self) -> None:
        tabs = [
            _assembled("a", "info_grid", [{"key": "x"}, {"key": "y"}, {"key": "z"}]),
            _assembled("b", "info_grid", [{"key": "x"}, {"key": "p"}, {"key": "q"}]),
        ]
        dropped: list[dict] = []

        _dedupe_tabs(tabs, dropped)

        assert [t["id"] for t in tabs] == ["a", "b"]

    def test_should_never_touch_the_overview(self) -> None:
        tabs = [
            {"id": "overview", "component": "overview", "props": {"keyTakeaways": ["x", "y"]}},
            _assembled("a", "checklist", [{"label": "x"}, {"label": "y"}]),
        ]
        dropped: list[dict] = []

        _dedupe_tabs(tabs, dropped)

        assert [t["id"] for t in tabs] == ["overview", "a"]

    @pytest.mark.parametrize("later", ["plain", "grouped"])
    def test_should_dedupe_twin_canvases_whatever_their_group_labels(self, later: str) -> None:
        plain, grouped = _canvas("plain", []), _canvas("grouped", [f"G{i}" for i in range(5)])
        tabs = [grouped, plain] if later == "plain" else [plain, grouped]
        dropped: list[dict] = []

        _dedupe_tabs(tabs, dropped)

        assert [d["id"] for d in dropped] == [later]

    def test_should_drop_a_one_item_tab_whose_item_ships_elsewhere(self) -> None:
        tabs = [
            _assembled("a", "info_grid", _keys("x", "y", "z")),
            _assembled("b", "info_grid", _keys("y")),
        ]
        dropped: list[dict] = []

        _dedupe_tabs(tabs, dropped)

        assert [t["id"] for t in tabs] == ["a"]

    def test_should_keep_a_bigger_later_tab_holding_a_small_tabs_items(self) -> None:
        tabs = [
            _assembled("small", "info_grid", _keys("x", "y")),
            _assembled("big", "checklist", _keys(*"xyabcdefgh")),
        ]
        dropped: list[dict] = []

        _dedupe_tabs(tabs, dropped)

        assert [t["id"] for t in tabs] == ["small", "big"]

    def test_should_carry_the_planned_data_source_in_the_drop_record(self) -> None:
        tabs = [
            _assembled("a", "info_grid", _keys("x", "y")),
            _assembled("b", "checklist", _keys("x", "y")),
        ]
        dropped: list[dict] = []

        _dedupe_tabs(tabs, dropped, sources={"a": "food.tips", "b": "food.equipment"})

        assert dropped[0]["dataSource"] == "food.equipment"


class TestRequiredTwin:
    """A twin the domain requires outlives one it does not (the backfill's tab)."""

    def test_should_keep_the_later_twin_when_only_it_is_required(self) -> None:
        tabs = [
            _assembled("stops", "checklist", _keys("x", "y", "z")),
            _assembled("itinerary", "spot_explorer", [{"name": n} for n in "xyz"], "spots"),
        ]
        dropped: list[dict] = []

        _dedupe_tabs(tabs, dropped, required={"spot_explorer"})

        assert [t["id"] for t in tabs] == ["itinerary"]

    def test_should_name_the_required_survivor_in_the_drop_record(self) -> None:
        tabs = [
            _assembled("stops", "checklist", _keys("x", "y")),
            _assembled("itinerary", "spot_explorer", [{"name": n} for n in "xy"], "spots"),
        ]
        dropped: list[dict] = []

        _dedupe_tabs(tabs, dropped, required={"spot_explorer"})

        assert (dropped[0]["id"], dropped[0]["detail"]) == (
            "stops",
            "100% of its items ship on itinerary",
        )

    def test_should_keep_an_earlier_tab_whose_own_items_mostly_ship_nowhere_else(self) -> None:
        tabs = [
            _assembled("stops", "checklist", _keys(*"xyabcdefgh")),
            _assembled("itinerary", "spot_explorer", [{"name": n} for n in "xy"], "spots"),
        ]
        dropped: list[dict] = []

        _dedupe_tabs(tabs, dropped, required={"spot_explorer"})

        assert [t["id"] for t in tabs] == ["stops", "itinerary"]

    def test_should_drop_the_later_twin_when_both_are_required(self) -> None:
        tabs = [
            _assembled("steps", "step_player", _keys("x", "y"), "steps"),
            _assembled("flow", "step_flow_canvas", _keys("x", "y"), "steps"),
        ]
        dropped: list[dict] = []

        _dedupe_tabs(tabs, dropped, required={"step_player", "step_flow_canvas"})

        assert [t["id"] for t in tabs] == ["steps"]


# ─── Through the assembler ───


def _twin_canvases(*, deck_first: bool = False) -> dict:
    tabs = [
        _tab("core_ideas", "concept_canvas", "learning.concepts"),
        _tab("thinkers", "flash_deck", "learning.concepts"),
    ]
    triage = _learning_triage(*(reversed(tabs) if deck_first else tabs))
    return assemble_response(triage, {"learning": {"concepts": _CONCEPTS}}, None, None)


class TestAssembledDedupe:
    @pytest.mark.parametrize("deck_first", [False, True])
    def test_should_ship_one_concept_canvas_when_a_flash_deck_promotes_onto_it(
        self, deck_first: bool
    ) -> None:
        result = _twin_canvases(deck_first=deck_first)

        assert [t["component"] for t in result["tabs"]].count("concept_canvas") == 1

    def test_should_drop_the_later_twin(self) -> None:
        result = _twin_canvases()

        assert {d["id"] for d in result["dropped"] if d["reason"] == DUPLICATE_TAB} == {"thinkers"}

    def test_should_record_the_dropped_twins_planned_data_source(self) -> None:
        result = _twin_canvases()

        assert [d["dataSource"] for d in result["dropped"] if d["id"] == "thinkers"] == [
            "learning.concepts"
        ]

    def test_should_never_link_a_surviving_tab_to_a_dropped_twin(self) -> None:
        result = _twin_canvases()

        targets = {link["targetTab"] for t in result["tabs"] for link in t["crossTabLinks"]}
        assert "thinkers" not in targets

    def test_should_still_drop_a_twin_with_no_item_of_its_own(self) -> None:
        """S03: a flash_deck promoted onto the planned canvas repeats every concept."""
        assert [d["reason"] for d in _twin_canvases()["dropped"]] == [DUPLICATE_TAB]

    def test_should_keep_the_backfilled_required_tab_over_its_planned_twin(self) -> None:
        spots = [
            {"name": n, "description": f"About {n}"} for n in ("Old Quarter", "Halong", "Sapa")
        ]
        triage = {
            "contentTags": ["travel"],
            "primaryTag": "travel",
            "tabs": [_tab("stops", "checklist", "travel.itinerary")],
        }

        result = assemble_response(triage, {"travel": {"itinerary": spots}}, None, None)

        assert "spot_explorer" in [t["component"] for t in result["tabs"]]

    def test_should_refuse_a_sibling_fallback_another_tab_already_shows(self) -> None:
        snippets = [
            {"filename": f"{i}.py", "language": "python", "code": f"x = {i}", "explanation": "e"}
            for i in range(2)
        ]
        triage = {
            "contentTags": ["tech"],
            "primaryTag": "tech",
            "tabs": [
                _tab("code", "code_playground", "tech.snippets"),
                _tab("patterns", "code_playground", "tech.patterns"),
            ],
        }

        result = assemble_response(
            triage, {"tech": {"snippets": snippets, "patterns": []}}, None, None
        )

        assert [t["id"] for t in result["tabs"] if t["component"] == "code_playground"] == ["code"]

    def test_should_recover_a_sibling_whose_claiming_tab_dropped(self) -> None:
        snippets = [
            {"filename": f"{i}.py", "language": "python", "code": f"x = {i}", "explanation": "e"}
            for i in range(2)
        ]
        triage = {
            "contentTags": ["tech"],
            "primaryTag": "tech",
            "tabs": [
                _tab("drill", "quiz_arena", "tech.snippets"),
                _tab("patterns", "code_playground", "tech.patterns"),
            ],
        }

        result = assemble_response(
            triage, {"tech": {"snippets": snippets, "patterns": []}}, None, None
        )

        assert [t["id"] for t in result["tabs"] if t["component"] == "code_playground"] == [
            "patterns"
        ]

    def test_should_still_recover_a_sibling_no_other_tab_reads(self) -> None:
        snippets = [
            {"filename": f"{i}.py", "language": "python", "code": f"x = {i}", "explanation": "e"}
            for i in range(2)
        ]
        triage = {
            "contentTags": ["tech"],
            "primaryTag": "tech",
            "tabs": [_tab("patterns", "code_playground", "tech.patterns")],
        }

        result = assemble_response(
            triage, {"tech": {"snippets": snippets, "patterns": []}}, None, None
        )

        assert "patterns" in [t["id"] for t in result["tabs"]]


# ─── Cross-tab links (0a.9 follow-up) ───


class TestLinkTabs:
    @staticmethod
    def _tabs() -> list[dict]:
        return [
            {"id": "overview", "label": "Overview", "component": "overview", "props": {}},
            {"id": "concepts", "label": "Concepts", "component": "flash_deck", "props": {}},
            {"id": "code", "label": "Code", "component": "code_playground", "props": {}},
            {"id": "quiz", "label": "Quiz", "component": "quiz_arena", "props": {}},
        ]

    def test_should_let_several_tabs_claim_one_target(self) -> None:
        tabs = self._tabs()

        _link_tabs(tabs, "tech")

        linked_to_quiz = [
            t["id"] for t in tabs if any(link["targetTab"] == "quiz" for link in t["crossTabLinks"])
        ]
        assert linked_to_quiz == ["concepts", "code"]

    def test_should_give_the_overview_no_links(self) -> None:
        tabs = self._tabs()

        _link_tabs(tabs, "tech")

        assert tabs[0]["crossTabLinks"] == []


# ─── Response-level attachments ───


class TestResponseAttachments:
    def test_should_mark_the_first_content_tab_as_the_answer_tab(self) -> None:
        triage = _learning_triage(
            _tab("ideas", "concept_canvas", "learning.concepts"),
            _tab("quiz", "quiz_arena", "enrichment.quiz"),
        )
        dense = [
            {"name": f"Concept {i}", "definition": f"d{i}", "connections": []} for i in range(12)
        ]
        extraction = {"learning": {"concepts": dense, "summary": "The lecture's one sentence."}}

        result = assemble_response(triage, extraction, {"quiz": _QUIZ}, None)

        ideas = next(t for t in result["tabs"] if t["id"] == "ideas")
        headers = [a for a in ideas.get("attachments", []) if a["component"] == "summary_header"]
        assert headers and headers[0]["props"]["summary"] == "The lecture's one sentence."

    def test_should_never_decorate_the_overview(self) -> None:
        triage = _learning_triage(_tab("ideas", "concept_canvas", "learning.concepts"))

        result = assemble_response(
            triage,
            {"learning": {"concepts": _CONCEPTS}},
            None,
            {"tldr": "x", "keyTakeaways": ["a"]},
        )

        assert "attachments" not in result["tabs"][0]
