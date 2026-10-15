"""Post-promotion dedupe, sibling-fallback refusal, cross-tab links claimable by
several tabs, response-level attachments (output-quality 0b.5 — R5, L26, audit §2 #7/#8).

Reconcile dedupes planned tabs by (dataSource, component) before extraction; promotion
(flash_deck → concept_canvas) and the sibling fallback still made two tabs ship the
same items (S03's two canvases). Assembly now hashes every tab's items and drops a tab
whose items overlap an earlier tab's by ≥ 50 %, refuses a sibling another tab reads,
lets several tabs link one target (never the overview), and decorates the response
through ``attach_response_secondaries`` (answer tab = first content tab).
"""

from __future__ import annotations

from src.services.pipeline.assembly import assemble_response
from src.services.pipeline.assembly.core import (
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


# ─── Fingerprints and the overlap rule ───


class TestTabFingerprints:
    def test_should_take_every_list_items_first_text_field(self) -> None:
        tab = _assembled("t", "spot_explorer", [{"name": "A", "description": "x"}, "B"], "spots")

        assert tab_fingerprints(tab) == frozenset({"a", "b"})

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

    def test_should_drop_at_half_overlap(self) -> None:
        tabs = [
            _assembled("a", "info_grid", [{"key": "x"}, {"key": "y"}, {"key": "z"}, {"key": "w"}]),
            _assembled("b", "info_grid", [{"key": "x"}, {"key": "y"}, {"key": "p"}, {"key": "q"}]),
        ]
        dropped: list[dict] = []

        _dedupe_tabs(tabs, dropped)

        assert [t["id"] for t in tabs] == ["a"]

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


# ─── Through the assembler ───


class TestAssembledDedupe:
    def test_should_ship_one_concept_canvas_when_a_flash_deck_promotes_onto_it(self) -> None:
        triage = _learning_triage(
            _tab("core_ideas", "concept_canvas", "learning.concepts"),
            _tab("thinkers", "flash_deck", "learning.concepts"),
        )

        result = assemble_response(triage, {"learning": {"concepts": _CONCEPTS}}, None, None)

        assert [t["component"] for t in result["tabs"] if t["component"] == "concept_canvas"] == [
            "concept_canvas"
        ]
        assert {d["id"] for d in result["dropped"] if d["reason"] == DUPLICATE_TAB} == {"thinkers"}

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
