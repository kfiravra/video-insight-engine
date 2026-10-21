"""p1.1 — ``learning.keyIdeas`` (D10): the argued idea of a talk, lecture or podcast.

Item shape ``{claim, why, quote, timestamp, section, kind}``, kind one of
thesis | argument | announcement | number | story | ask; registry cap 20 and
components ``[spot_explorer, stat_banner]``. Assembly ships the ideas as spot
cards — claim → name, why → description, quote, timestamp, section → the spot
sections, kind → the category badge — and the answer tab's numbers strip reads
their stamped figures. keyPoints stays for explainers.
"""

from __future__ import annotations

import pytest

from src.models.domain_types import LearningData, validate_domain_output_with_drops
from src.models.domain_validation import ValidationReport
from src.models.idea_types import KEY_IDEA_KINDS, LearningKeyIdea
from src.services.pipeline.assembly import assemble_response
from src.services.pipeline.assembly.assemblers_primary import assemble_spot_explorer
from src.services.pipeline.schema_fields import load_domain_schema, slice_skeleton
from src.shared_config.domain_config import data_source, render_extraction_caps

_IDEAS = [
    {"claim": "Hand-written code stops being economically productive this year",
     "why": "Agents now write 150,000 lines in a month where a team wrote 30,000 a year.",
     "quote": "Pencils down.", "timestamp": 1245, "section": "The inflection", "kind": "thesis"},
    {"claim": "English is a better programming language than Ruby",
     "why": "It is more expressive and vaguer, and that vagueness is a joy.",
     "quote": "It's called English.", "timestamp": 2295, "section": "Languages",
     "kind": "argument"},
    {"claim": "Every app needs a CLI by next Friday",
     "why": "Agents drive tools through the command line, not the GUI.",
     "quote": None, "timestamp": 2715, "section": "The inflection", "kind": "ask"},
]  # fmt: skip


def _validated(ideas: list[dict]) -> list[dict]:
    """The ideas as validation hands them to assembly (``model_dump(by_alias=True)``)."""
    return LearningData.model_validate({"keyIdeas": ideas}).model_dump(by_alias=True)["keyIdeas"]


def _key_ideas_tab() -> dict:
    return {"id": "key_ideas", "label": "12 Key Ideas", "emoji": "💡",
            "component": "spot_explorer", "dataSource": "learning.keyIdeas",
            "goal": "After this you know what he argues."}  # fmt: skip


def _assemble(ideas: list[dict]) -> dict:
    triage = {"primaryTag": "learning", "contentTags": ["learning"], "tabs": [_key_ideas_tab()]}
    extraction = {"learning": {"keyIdeas": _validated(ideas)}}
    return assemble_response(triage, extraction, None, None, video_meta={"duration": 3780})


def _tab(result: dict, tab_id: str) -> dict:
    return next(t for t in result["tabs"] if t["id"] == tab_id)


# ─── Model + validation ───


class TestKeyIdeaModel:
    def test_should_keep_every_field_when_the_idea_is_complete(self) -> None:
        idea = LearningKeyIdea.model_validate(_IDEAS[0]).model_dump()

        assert idea == {**_IDEAS[0], "speaker": None}

    @pytest.mark.parametrize("kind", ["Thesis", " ASK ", "number"])
    def test_should_lower_case_the_kind_when_it_is_in_the_vocabulary(self, kind: str) -> None:
        idea = LearningKeyIdea.model_validate({"claim": "c", "kind": kind})

        assert idea.kind == kind.strip().lower()

    @pytest.mark.parametrize("kind", ["claim", "insight", "", 3, None])
    def test_should_drop_the_kind_when_it_is_outside_the_vocabulary(self, kind: object) -> None:
        assert LearningKeyIdea.model_validate({"claim": "c", "kind": kind}).kind is None

    @pytest.mark.parametrize("stamp", [-5, "soon", True, float("inf"), None])
    def test_should_clear_the_timestamp_when_it_cannot_seek(self, stamp: object) -> None:
        assert LearningKeyIdea.model_validate({"claim": "c", "timestamp": stamp}).timestamp is None

    def test_should_clear_a_blank_quote_and_section_when_given(self) -> None:
        idea = LearningKeyIdea.model_validate({"claim": "c", "quote": "  ", "section": ""})

        assert (idea.quote, idea.section) == (None, None)

    def test_should_name_six_kinds_when_the_vocabulary_is_read(self) -> None:
        assert KEY_IDEA_KINDS == ("thesis", "argument", "announcement", "number", "story", "ask")


class TestKeyIdeaValidation:
    def _validate(self, ideas: list[dict]) -> tuple[list[dict], ValidationReport]:
        data, report = validate_domain_output_with_drops(
            ["learning"], [], {"learning": {"keyIdeas": ideas}}
        )
        return data["learning"]["keyIdeas"], report

    def test_should_keep_the_ideas_when_the_learning_block_validates(self) -> None:
        ideas, _ = self._validate(_IDEAS)

        assert [i["claim"] for i in ideas] == [i["claim"] for i in _IDEAS]

    def test_should_take_the_claim_from_the_title_when_the_model_wrote_one(self) -> None:
        ideas, _ = self._validate([{"title": "Optimism is the only rational play", "why": "w"}])

        assert ideas[0]["claim"] == "Optimism is the only rational play"

    def test_should_drop_an_idea_when_it_has_no_claim(self) -> None:
        ideas, report = self._validate([{"why": "no claim"}, _IDEAS[1]])

        assert [i["claim"] for i in ideas] == [_IDEAS[1]["claim"]]
        assert report.get("dropped", {})["learning.keyIdeas"]["dropped"] == 1


# ─── Registry + schema ───


class TestKeyIdeaRegistry:
    def test_should_render_with_spot_explorer_and_the_stat_banner_when_registered(self) -> None:
        spec = data_source("learning.keyIdeas")

        assert spec is not None
        assert (spec["components"], spec["cap"], spec["kind"]) == (
            ["spot_explorer", "stat_banner"],
            20,
            "list",
        )

    def test_should_ask_for_the_six_d10_fields_when_the_schema_is_sliced(self) -> None:
        sliced = slice_skeleton("learning", ["keyIdeas"])

        assert {"claim", "why", "quote", "timestamp", "section", "kind"} <= set(
            sliced["keyIdeas"][0]
        )

    def test_should_keep_key_points_when_key_ideas_join_the_schema(self) -> None:
        skeleton = load_domain_schema("learning").skeleton

        assert {"keyPoints", "keyIdeas"} <= set(skeleton)

    def test_should_cap_key_ideas_at_twenty_when_the_plan_reads_the_caps(self) -> None:
        learning = next(
            ln for ln in render_extraction_caps().splitlines() if ln.startswith("- learning:")
        )

        assert "keyIdeas ≤ 20" in learning


# ─── Assembly ───


class TestKeyIdeaSpots:
    def test_should_map_claim_why_quote_timestamp_and_kind_when_assembled(self) -> None:
        props = assemble_spot_explorer({}, _validated(_IDEAS), {}, None)

        assert props is not None
        assert props["spots"][0] == {
            "name": _IDEAS[0]["claim"],
            "description": _IDEAS[0]["why"],
            "quote": "Pencils down.",
            "timestamp": 1245,
            "category": "Thesis",
        }

    def test_should_ship_no_quote_when_the_idea_has_none(self) -> None:
        props = assemble_spot_explorer({}, _validated(_IDEAS), {}, None)

        assert props is not None and "quote" not in props["spots"][2]

    def test_should_group_ideas_by_section_in_first_appearance_order(self) -> None:
        props = assemble_spot_explorer({}, _validated(_IDEAS), {}, None)

        assert props is not None
        assert props["sections"] == [
            {"label": "The inflection", "spotIndices": [0, 2]},
            {"label": "Languages", "spotIndices": [1]},
        ]

    def test_should_ship_no_sections_when_every_idea_shares_one(self) -> None:
        ideas = [{**idea, "section": "All"} for idea in _IDEAS]

        props = assemble_spot_explorer({}, _validated(ideas), {}, None)

        assert props is not None and "sections" not in props

    def test_should_ship_no_badge_when_the_kind_is_unstated(self) -> None:
        props = assemble_spot_explorer(
            {}, _validated([{**i, "kind": None} for i in _IDEAS]), {}, None
        )

        assert props is not None and all("category" not in spot for spot in props["spots"])

    def test_should_drop_the_tab_when_fewer_than_two_ideas_ship(self) -> None:
        assert assemble_spot_explorer({}, _validated(_IDEAS[:1]), {}, None) is None

    def test_should_not_read_news_claims_as_key_ideas_when_they_reach_spot_explorer(self) -> None:
        claims = [{"claim": "c1", "source": "s", "status": "context"},
                  {"claim": "c2", "source": "s", "status": "context"}]  # fmt: skip

        assert assemble_spot_explorer({}, claims, {}, None) is None


class TestKeyIdeasInTheResponse:
    def test_should_ship_the_key_ideas_tab_with_its_true_count_when_planned(self) -> None:
        tab = _tab(_assemble(_IDEAS), "key_ideas")

        assert (tab["component"], len(tab["props"]["spots"]), tab["label"]) == (
            "spot_explorer",
            3,
            "3 Key Ideas",
        )

    def test_should_attach_the_numbers_strip_when_key_ideas_state_five_stamped_figures(
        self,
    ) -> None:
        figures = ["150,000 lines", "99% less CPU", "9 seconds", "$20 million", "40,000 tellers"]
        ideas = [{"claim": f"It took {f}", "why": "w", "timestamp": 60 * (i + 1), "kind": "number"}
                 for i, f in enumerate(figures)]  # fmt: skip

        tab = _tab(_assemble(ideas), "key_ideas")
        banner = next(a for a in tab.get("attachments", []) if a["component"] == "stat_banner")

        assert [s["timestamp"] for s in banner["props"]["stats"]] == [60, 120, 180, 240, 300]
