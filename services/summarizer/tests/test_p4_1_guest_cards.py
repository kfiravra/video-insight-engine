"""An interview's guests open its Positions (output-quality 4.1, C10, Kfir Q2).

A spot tab of a podcast-tagged run asks for ``podcast.guests`` (lane P4). Assembly
puts them first on the key-ideas tab (Positions) as a section of their own — "Guest",
"Guests" for two or more — each a spot card with the role as its badge and the second
the guest is introduced as its seek chip. A planned guests tab keeps the role as the
badge too; the dedupe sheds it when Positions already ships the same cards. The key-ideas
cap counts the guest cards on top, so a guest never pushes the last idea out.
"""

from __future__ import annotations

from typing import Any

from src.services.pipeline.assembly import assemble_response, assemble_spot_explorer
from src.services.pipeline.assembly.core import DUPLICATE_TAB, _dedupe_tabs, key_idea_caps
from src.services.pipeline.assembly.key_ideas import guest_spots, key_idea_props

_ATTIA = {
    "name": "Peter Attia",
    "role": "physician, author of Outlive",
    "description": "Longevity doctor",
    "timestamp": 42,
}
_HOST = {"name": "Talks at Google host", "role": "moderator", "timestamp": 0}


def _ideas(*sections: str) -> list[dict[str, Any]]:
    return [
        {"claim": f"Idea {i}", "why": "w", "timestamp": 100 + i, "section": section}
        for i, section in enumerate(sections)
    ]


def _extraction(guests: list[dict[str, Any]], ideas: list[dict[str, Any]]) -> dict:
    return {"learning": {"keyIdeas": ideas}, "podcast": {"guests": guests}}


def _shipped(props: dict | None) -> dict:
    assert props is not None
    return props


def _positions(guests: list[dict[str, Any]], ideas: list[dict[str, Any]]) -> dict:
    tab = {"id": "positions", "component": "spot_explorer", "dataSource": "learning.keyIdeas"}
    return _shipped(assemble_spot_explorer(tab, ideas, _extraction(guests, ideas), None))


class TestGuestSection:
    def test_should_open_positions_with_the_guest_card(self):
        props = _positions([_ATTIA], _ideas("Medicine 3.0", "Exercise"))

        assert props["spots"][0]["name"] == "Peter Attia"

    def test_should_show_the_role_as_the_guest_card_badge(self):
        props = _positions([_ATTIA], _ideas("Medicine 3.0", "Exercise"))

        assert props["spots"][0]["category"] == "physician, author of Outlive"

    def test_should_seek_to_the_second_the_guest_is_introduced(self):
        props = _positions([_ATTIA], _ideas("Medicine 3.0", "Exercise"))

        assert props["spots"][0]["timestamp"] == 42

    def test_should_open_the_sections_with_a_guest_section(self):
        props = _positions([_ATTIA], _ideas("Medicine 3.0", "Exercise"))

        assert props["sections"][0] == {"label": "Guest", "spotIndices": [0]}

    def test_should_name_the_section_guests_when_there_are_two(self):
        props = _positions([_ATTIA, _HOST], _ideas("Medicine 3.0", "Exercise"))

        assert props["sections"][0] == {"label": "Guests", "spotIndices": [0, 1]}

    def test_should_point_the_outline_sections_past_the_guest_cards(self):
        props = _positions([_ATTIA], _ideas("Medicine 3.0", "Exercise"))

        assert [s["spotIndices"] for s in props["sections"][1:]] == [[1], [2]]

    def test_should_ship_no_guest_section_when_guests_are_empty(self):
        props = _positions([], _ideas("Medicine 3.0", "Exercise"))

        assert [s["label"] for s in props["sections"]] == ["Medicine 3.0", "Exercise"]

    def test_should_ship_the_ideas_alone_when_the_run_has_no_podcast_block(self):
        ideas = _ideas("Medicine 3.0", "Exercise")
        tab = {"id": "ideas", "component": "spot_explorer", "dataSource": "learning.keyIdeas"}

        props = _shipped(
            assemble_spot_explorer(tab, ideas, {"learning": {"keyIdeas": ideas}}, None)
        )

        assert [s["name"] for s in props["spots"]] == ["Idea 0", "Idea 1"]

    def test_should_lead_the_list_without_sections_when_the_ideas_name_none(self):
        props = key_idea_props(_ideas("", ""), [_ATTIA])

        assert ("sections" not in props, props["spots"][0]["name"]) == (True, "Peter Attia")


class TestGuestCard:
    def test_should_keep_a_guest_with_only_a_name_and_a_role(self):
        assert guest_spots([{"name": "Jane Doe", "role": "economist"}]) == [
            {"name": "Jane Doe", "description": "", "category": "economist"}
        ]

    def test_should_drop_a_guest_without_a_name(self):
        assert guest_spots([{"name": "", "role": "economist"}]) == []

    def test_should_ship_no_cards_when_guests_are_not_a_list(self):
        assert guest_spots(None) == []


class TestPlannedGuestsTab:
    def test_should_keep_the_role_as_the_category_when_a_guests_tab_is_planned(self):
        tab = {"id": "guests", "component": "spot_explorer", "dataSource": "podcast.guests"}

        props = _shipped(assemble_spot_explorer(tab, [_ATTIA, _HOST], {}, None))

        assert [s["category"] for s in props["spots"]] == ["physician, author of Outlive",
                                                          "moderator"]  # fmt: skip

    def test_should_shed_a_guests_tab_when_positions_already_ships_its_cards(self):
        guests = [_ATTIA, _HOST]
        positions = {"id": "positions", "component": "spot_explorer",
                     "props": _positions(guests, _ideas("A", "B"))}  # fmt: skip
        guests_tab = {"id": "guests", "component": "spot_explorer",
                      "props": assemble_spot_explorer({}, guests, {}, None)}  # fmt: skip
        tabs, dropped = [positions, guests_tab], []

        _dedupe_tabs(tabs, dropped)

        assert [(d["id"], d["reason"]) for d in dropped] == [("guests", DUPLICATE_TAB)]


class TestPositionsCap:
    _SOURCES = {"positions": "learning.keyIdeas", "moments": "learning.timestamps"}

    def test_should_add_the_guest_cards_to_the_key_ideas_cap(self):
        caps = key_idea_caps(self._SOURCES, 3780, _extraction([_ATTIA, _HOST], []))

        assert caps == {"positions": 26}

    def test_should_keep_the_cap_when_the_run_has_no_guests(self):
        assert key_idea_caps(self._SOURCES, 3780, {"learning": {}}) == {"positions": 24}

    def test_should_ship_every_idea_and_the_guest_when_the_ideas_fill_the_cap(self):
        ideas = _ideas(*([""] * 24))
        tabs = [{"id": "positions", "component": "spot_explorer",
                 "dataSource": "learning.keyIdeas", "label": "Positions"}]  # fmt: skip
        triage = {"primaryTag": "podcast", "contentTags": ["podcast", "learning"], "tabs": tabs}

        result = assemble_response(triage, _extraction([_ATTIA], ideas), None, None,
                                   video_meta={"duration": 3780})  # fmt: skip

        spots = next(t for t in result["tabs"] if t["id"] == "positions")["props"]["spots"]
        assert (len(spots), spots[0]["name"]) == (25, "Peter Attia")
