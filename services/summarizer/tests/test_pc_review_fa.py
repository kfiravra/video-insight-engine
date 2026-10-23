"""pC review, lane FA — the assembly findings.

- R6: a spot_explorer tab over ``food.tips`` / ``fitness.tips`` ({type, text}) or
  ``gaming.loadout`` ({item, category, note}) refused every card and shipped as the
  standalone info_grid R6 rules out (S06, S09, S10). The spot card now reads such an
  item as its info_grid row; the method / workout still links to the tips.
- A trailing "[timestamp ~Ns]" in an item's text (S09's loadout notes) leaves the text
  and becomes the item's seek point.
- A folded grid repeated its spot host under another name (S04: 15 of 20 rows at a key
  idea's second) and ignored its brief's ``where``; its section kept its emoji.
- A review's Pros & cons tab (pros and cons, no rows) counted 0 items and read as sparse.
- A quote deck on a learning run promoted to a second concepts canvas, its quotes lost.
"""

from __future__ import annotations

from typing import Any

from src.services.pipeline.assembly import assemble_response, promote_component
from src.services.pipeline.assembly.assemblers_primary import (
    assemble_info_grid,
    assemble_spot_explorer,
)
from src.services.pipeline.assembly.attachments import attach_secondaries
from src.services.pipeline.assembly.cross_tab import resolve_cross_tab_links
from src.services.pipeline.assembly.info_grid_fold import apply_folds
from src.services.pipeline.assembly.stamp_marker import split_stamp_marker
from src.services.pipeline.assembly.tip_spots import spot_card

_TIPS = [
    {"type": "chef_tip", "text": "Brown the meat as balls, not crumbles. Balls leave gaps "
     "so steam escapes and the meat browns deeply without cooking through."},
    {"type": "chef_tip", "text": "Don't force the browning. Wait for the sizzle and let "
     "the color come on its own, because scorching ruins the fond."},
    {"type": "storage", "text": "The ragù freezes for months."},
]  # fmt: skip
_PICK = {"item": "Stussy", "category": "meta take", "note": "One of the most busted cards."}


def _card(item: dict[str, Any]) -> dict[str, Any]:
    spot = spot_card(item)
    assert spot is not None
    return spot


# ─── R6: tips and picks ship as cards ───


class TestTipSpots:
    def test_should_ship_spots_not_an_info_grid_when_food_tips_are_planned_as_cards(self) -> None:
        triage = {"primaryTag": "food", "contentTags": ["food"],
                  "tabs": [{"id": "tips", "label": "Chef Tips", "component": "spot_explorer",
                            "dataSource": "food.tips"}]}  # fmt: skip

        result = assemble_response(triage, {"food": {"tips": _TIPS}}, None, None)

        tips = next(tab for tab in result["tabs"] if tab["id"] == "tips")
        assert (tips["component"], tips.get("degradedFrom")) == ("spot_explorer", None)

    def test_should_head_a_tip_card_with_its_first_sentence_when_the_tip_has_a_body(
        self,
    ) -> None:
        spot = _card(_TIPS[0])

        assert spot["name"] == "Brown the meat as balls, not crumbles"

    def test_should_badge_a_tip_card_with_its_type_when_the_tip_is_typed(self) -> None:
        assert _card(_TIPS[0])["category"] == "Chef tip"

    def test_should_head_a_one_sentence_tip_with_its_own_text_when_it_has_no_body(self) -> None:
        assert _card(_TIPS[2])["name"] == "The ragù freezes for months."

    def test_should_card_a_pick_with_its_note_when_the_pick_has_no_description(self) -> None:
        assert _card(_PICK)["description"] == _PICK["note"]

    def test_should_keep_the_note_when_a_pick_carries_its_own_timestamp(self) -> None:
        spot = _card({**_PICK, "timestamp": 200})

        assert (spot["description"], spot["timestamp"]) == (_PICK["note"], 200)

    def test_should_ship_every_pick_when_a_loadout_list_is_planned_as_cards(self) -> None:
        picks = [_PICK, {**_PICK, "item": "Charlotte Pudding"}]

        props = assemble_spot_explorer({}, picks, {}, None)
        assert props is not None

        assert [spot["name"] for spot in props["spots"]] == ["Stussy", "Charlotte Pudding"]

    def test_should_refuse_a_bare_headline_when_it_has_no_value_or_badge(self) -> None:
        assert spot_card({"key": "Lone key"}) is None

    def test_should_keep_the_guest_name_when_a_guest_has_a_role_and_no_description(
        self,
    ) -> None:
        assert _card({"name": "Dr. Cho", "role": "Engineer"})["name"] == "Dr. Cho"

    def test_should_link_the_method_to_the_tips_when_the_tips_ship_as_cards(self) -> None:
        tabs = [
            {"id": "steps", "label": "Method", "component": "step_player"},
            {"id": "tips", "label": "Chef Tips", "component": "spot_explorer"},
        ]

        links = resolve_cross_tab_links("steps", {"steps", "tips"}, "step_player", tabs, "food")

        assert [link["targetTab"] for link in links] == ["tips"]


# ─── A seek point written into the text ───


_MARKED = "One of the most busted cards in OP7. [timestamp ~200s]"


class TestStampMarker:
    def test_should_strip_the_marker_from_the_card_when_the_note_ends_in_one(self) -> None:
        assert _card({**_PICK, "note": _MARKED})["description"] == (
            "One of the most busted cards in OP7."
        )

    def test_should_seek_to_the_marker_second_when_the_item_has_no_stamp(self) -> None:
        assert _card({**_PICK, "note": _MARKED})["timestamp"] == 200

    def test_should_keep_the_item_stamp_when_it_has_one_and_a_marker(self) -> None:
        assert _card({**_PICK, "note": _MARKED, "timestamp": 195})["timestamp"] == 195

    def test_should_strip_the_marker_from_the_grid_row_when_the_note_ends_in_one(self) -> None:
        props = assemble_info_grid({}, [{**_PICK, "note": _MARKED}], {}, None)
        assert props is not None

        assert (props["items"][0]["value"], props["items"][0]["timestamp"]) == (
            "One of the most busted cards in OP7.",
            200,
        )

    def test_should_leave_the_text_whole_when_it_carries_no_marker(self) -> None:
        assert split_stamp_marker("A plain note [with brackets]") == (
            "A plain note [with brackets]",
            None,
        )


# ─── A folded grid joins its spot host ───


def _idea(name: str, seconds: int) -> dict[str, Any]:
    return {"name": name, "description": f"{name} is what he uses.", "timestamp": seconds}


def _host(*spots: dict[str, Any]) -> dict[str, Any]:
    return {"id": "stack", "label": "The Stack", "component": "spot_explorer",
            "props": {"spots": list(spots)}}  # fmt: skip


def _fold(where: list[str] | None = None) -> dict[str, Any]:
    fold: dict[str, Any] = {"id": "workflow", "label": "🏭 Agent Workflow", "emoji": "🏭",
                            "dataSource": "tech.topics", "component": "info_grid"}  # fmt: skip
    if where is not None:
        fold["brief"] = {"what": "", "where": where, "expect": 0}
    return fold


def _join(host: dict[str, Any], fold: dict[str, Any], rows: list[dict[str, Any]]) -> None:
    apply_folds(host, [fold], {"tech": {"topics": rows}}, None, lambda *_a: rows)


class TestFoldRows:
    def test_should_ship_the_key_ideas_alone_when_a_fold_restates_them_at_the_same_seconds(
        self,
    ) -> None:
        host = _host(_idea("T3 Code", 61), _idea("Claude Code", 372))

        _join(host, _fold(), [_idea("T3 Code (harness)", 60), _idea("Claude Code CLI", 372)])

        assert [spot["name"] for spot in host["props"]["spots"]] == ["T3 Code", "Claude Code"]

    def test_should_keep_only_the_rows_inside_the_brief_window_when_the_fold_has_one(
        self,
    ) -> None:
        host = _host(_idea("T3 Code", 61), _idea("Claude Code", 372))
        rows = [_idea("Cursor", 402), _idea("Skills repo", 1021)]

        _join(host, _fold(["17:00-18:40"]), rows)

        assert [spot["name"] for spot in host["props"]["spots"]][2:] == ["Skills repo"]

    def test_should_keep_an_unstamped_row_when_the_fold_has_a_window(self) -> None:
        host = _host(_idea("T3 Code", 61), _idea("Claude Code", 372))
        row = {"name": "Mobbin", "description": "His design inspiration."}

        _join(host, _fold(["17:00-18:40"]), [row])

        assert host["props"]["spots"][-1]["name"] == "Mobbin"

    def test_should_label_the_fold_section_without_its_emoji_when_rows_join(self) -> None:
        host = _host(_idea("T3 Code", 61), _idea("Claude Code", 372))

        _join(host, _fold(), [_idea("Skills repo", 1021)])

        assert host["props"]["sections"][-1]["label"] == "Agent Workflow"


# ─── A Pros & cons tab counts its pros and cons ───


def _frames(count: int) -> list[dict[str, Any]]:
    return [{"thumbnailUrl": f"https://cdn/{i}.jpg", "timestamp": i * 10} for i in range(count)]


def _pros_cons_tab() -> dict[str, Any]:
    props = {"comparisons": [], "pros": [f"Pro number {i}" for i in range(10)],
             "cons": [f"Con number {i}" for i in range(7)]}  # fmt: skip
    return {"id": "pros_cons", "component": "comparison", "goal": "Weigh it", "props": props}


class TestAttachmentCount:
    def test_should_head_a_pros_cons_tab_with_its_first_item_when_it_ships_17(self) -> None:
        out = attach_secondaries(_pros_cons_tab(), {}, None, _frames(5), "review")

        assert [(a["component"], a["props"].get("title")) for a in out] == [
            ("summary_header", "First up")
        ]

    def test_should_not_take_the_frame_strip_when_a_pros_cons_tab_ships_17(self) -> None:
        out = attach_secondaries(_pros_cons_tab(), {}, None, _frames(5), "review")

        assert "frame_strip" not in {a["component"] for a in out}


# ─── A quote deck stays a deck ───


def _concepts() -> list[dict[str, Any]]:
    names = ["Inflation", "Money", "Gold", "Trust"]
    return [
        {"name": name, "definition": f"{name} defined", "connections": [names[(i + 1) % 4]]}
        for i, name in enumerate(names)
    ]


_QUOTES = [{"quote": f"Line {i} of the talk", "speaker": "Host", "timestamp": i * 60}
           for i in range(5)]  # fmt: skip


class TestQuoteDeck:
    def test_should_keep_a_quote_deck_when_a_learning_run_has_connected_concepts(self) -> None:
        extraction = {"learning": {"concepts": _concepts()}, "podcast": {"quotes": _QUOTES}}

        component, _ = promote_component("flash_deck", {"cards": []}, _QUOTES, extraction,
                                         "learning")  # fmt: skip

        assert component == "flash_deck"

    def test_should_promote_a_deck_when_its_data_is_the_connected_concepts(self) -> None:
        extraction = {"learning": {"concepts": _concepts()}}
        concepts = extraction["learning"]["concepts"]

        component, _ = promote_component("flash_deck", {"cards": []}, concepts, extraction,
                                         "learning")  # fmt: skip

        assert component == "concept_canvas"
