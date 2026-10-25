"""Where host and guest disagree (output-quality 4.1, C10, content-model §4).

Lane P4 adds the key-idea kind ``disagreement`` (the one who pushes back as the
speaker, both sides as the why, the pushback as the quote). On Positions two or more
gather in a trailing "Disagreements" section; a lone one stays in its outline section,
badged "Disagreement".
"""

from __future__ import annotations

from typing import Any

from src.services.pipeline.assembly.key_ideas import key_idea_props


def _idea(claim: str, section: str, kind: str = "argument") -> dict[str, Any]:
    return {"claim": claim, "why": "w", "kind": kind, "section": section, "timestamp": 60}


def _sections(props: dict) -> dict[str, list[int]]:
    return {s["label"]: s["spotIndices"] for s in props["sections"]}


class TestTwoOrMoreDisagreements:
    def _props(self) -> dict:
        return key_idea_props(
            [
                _idea("AI is near", "Timelines"),
                _idea("Not that near", "Timelines", "disagreement"),
                _idea("Alignment is solvable", "Safety"),
                _idea("It is not, yet", "Safety", "disagreement"),
            ]
        )

    def test_should_gather_them_in_a_disagreements_section(self):
        assert _sections(self._props())["Disagreements"] == [1, 3]

    def test_should_put_the_disagreements_section_last(self):
        assert self._props()["sections"][-1]["label"] == "Disagreements"

    def test_should_take_them_out_of_their_outline_sections(self):
        assert _sections(self._props())["Timelines"] == [0]

    def test_should_keep_the_cards_in_source_order(self):
        names = [spot["name"] for spot in self._props()["spots"]]

        assert names == ["AI is near", "Not that near", "Alignment is solvable", "It is not, yet"]

    def test_should_badge_each_card_disagreement(self):
        assert self._props()["spots"][1]["category"] == "Disagreement"


class TestALoneDisagreement:
    def _props(self) -> dict:
        return key_idea_props(
            [
                _idea("AI is near", "Timelines"),
                _idea("Not that near", "Timelines", "disagreement"),
                _idea("Alignment is solvable", "Safety"),
            ]
        )

    def test_should_keep_a_lone_disagreement_in_its_outline_section(self):
        assert _sections(self._props())["Timelines"] == [0, 1]

    def test_should_open_no_disagreements_section_for_one(self):
        assert "Disagreements" not in _sections(self._props())

    def test_should_still_badge_a_lone_disagreement(self):
        assert self._props()["spots"][1]["category"] == "Disagreement"


class TestDisagreementsWithoutOutlineSections:
    """The section rule stays one rule: no nav below two named sections — a list with
    no outline sections keeps its disagreements in place, each with its badge."""

    def test_should_open_no_sections_when_the_ideas_name_none(self):
        props = key_idea_props(
            [
                _idea("AI is near", ""),
                _idea("Not that near", "", "disagreement"),
                _idea("It is not, yet", "", "disagreement"),
            ]
        )

        assert "sections" not in props

    def test_should_section_disagreements_beside_a_guest_section(self):
        ideas = [
            _idea("AI is near", ""),
            _idea("Not that near", "", "disagreement"),
            _idea("It is not, yet", "", "disagreement"),
        ]

        props = key_idea_props(ideas, [{"name": "Max Tegmark", "role": "physicist"}])

        assert [s["label"] for s in props["sections"]] == ["Guest", "Other", "Disagreements"]
