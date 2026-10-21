"""p1.2 — learning moments say what was established; concepts say where (R7, R2; L12, L13).

``learning.timestamps`` items gain ``description`` (required — a label-only
moment is a chapter jump and validation drops it, counted) and ``quote`` (the
speaker's line, passed through to the track and counted by salience);
``learning.concepts`` asks for the ``timestamp`` its model always had. A Moments
tab planned on a label-only list ships the described registry sibling the same
run extracted (S01: 46 labels vs ``narrative.keyMoments`` 20 / 20 described),
unless another tab ships it.
"""

from __future__ import annotations

import pytest

from src.models.domain_types import (
    LearningConcept,
    LearningTimestamp,
    validate_domain_output_with_drops,
)
from src.services.pipeline.assembly import assemble_response
from src.services.pipeline.assembly.moment_normalizers import normalize_moment_items
from src.services.pipeline.assembly.moment_sources import described_moment_source
from src.services.pipeline.assembly.sampling import moment_salience
from src.services.pipeline.schema_fields import slice_skeleton
from src.shared_config.domain_config import data_source

_LABELS = [{"time": f"{m}:00", "seconds": 60 * m, "label": f"Chapter {m}"} for m in range(1, 41)]
# narrative.keyMoments as S01 extracted them: no label, a description longer than the
# 60-character label the normalizer cuts from it.
_DESCRIBED = [
    {"timestamp": 1262, "description": "37signals goes pencils down on hand-written code; "
     "writing by hand is now an exceptional state."},
    {"timestamp": 2440, "description": "He announces he has retired from being a professional "
     "programmer and calls hand-chiseling code a hobby."},
    {"timestamp": 2715, "description": "Bring your own butler: every app must ship a CLI by "
     "next Friday so agents can drive it."},
    {"timestamp": 3625, "description": "P-bloom over P-doom: security problems get solved "
     "faster than they appear, so optimism is the only rational play."},
]  # fmt: skip


def _moments_tab(source: str = "learning.timestamps", tab_id: str = "timeline") -> dict:
    return {"id": tab_id, "label": "Key Moments", "emoji": "", "component": "moment_track",
            "dataSource": source, "goal": "You know what each beat established."}  # fmt: skip


def _assemble(tabs: list[dict], extraction: dict) -> dict:
    triage = {
        "primaryTag": "learning",
        "contentTags": ["learning"],
        "modifiers": ["narrative"],
        "tabs": tabs,
    }
    meta = {"duration": 3780, "outline": [], "chapters": []}
    return assemble_response(triage, extraction, None, None, video_meta=meta)


def _tab(result: dict, tab_id: str) -> dict:
    return next(t for t in result["tabs"] if t["id"] == tab_id)


# ─── Model + validation ───


class TestLearningTimestampModel:
    def test_should_keep_the_description_and_quote_when_the_moment_has_them(self) -> None:
        moment = LearningTimestamp.model_validate(
            {"seconds": 312, "label": "Debt first", "description": " It pays 24%. ", "quote": "q"}
        )

        assert (moment.description, moment.quote) == ("It pays 24%.", "q")

    @pytest.mark.parametrize("key", ["summary", "detail"])
    def test_should_take_the_description_when_the_model_names_it_otherwise(self, key: str) -> None:
        moment = LearningTimestamp.model_validate({"seconds": 1, "label": "L", key: "What it set"})

        assert moment.description == "What it set"

    def test_should_clear_a_blank_quote_when_given(self) -> None:
        moment = LearningTimestamp.model_validate({"label": "L", "description": "d", "quote": " "})

        assert moment.quote is None


class TestLabelOnlyMomentsAreDropped:
    def _validate(self, timestamps: list[dict]) -> tuple[list[dict], dict]:
        data, report = validate_domain_output_with_drops(
            ["learning"], [], {"learning": {"timestamps": timestamps}}
        )
        return data["learning"]["timestamps"], dict(report.get("dropped", {}))

    def test_should_drop_a_moment_when_it_has_no_description(self) -> None:
        moments, dropped = self._validate(
            [{"seconds": 60, "label": "Intro"}, {"seconds": 90, "label": "B", "description": "d"}]
        )

        assert [m["label"] for m in moments] == ["B"]
        assert dropped["learning.timestamps"] == {"dropped": 1, "total": 2}

    def test_should_drop_a_moment_when_its_description_is_blank(self) -> None:
        moments, _ = self._validate([{"seconds": 60, "label": "Intro", "description": "   "}])

        assert moments == []


class TestConceptTimestamp:
    @pytest.mark.parametrize(
        ("raw", "expected"), [(95, 95), ("95", 95), (-1, None), ("1:35", None)]
    )
    def test_should_keep_a_concept_timestamp_only_when_it_is_whole_seconds(
        self, raw: object, expected: int | None
    ) -> None:
        concept = LearningConcept.model_validate({"name": "C", "timestamp": raw})

        assert concept.timestamp == expected


# ─── Schema + registry ───


class TestSchemaAsksForTheFields:
    def test_should_ask_every_moment_for_description_and_quote(self) -> None:
        item = slice_skeleton("learning", ["timestamps"])["timestamps"][0]

        assert {"description", "quote"} <= set(item)

    def test_should_ask_every_concept_for_its_timestamp(self) -> None:
        item = slice_skeleton("learning", ["concepts"])["concepts"][0]

        assert "timestamp" in item

    def test_should_weigh_a_described_moment_above_a_label_when_the_registry_is_read(
        self,
    ) -> None:
        spec = data_source("learning.timestamps")

        # 48 tokens a label-only moment weighed before the description joined it.
        assert spec is not None and spec["outputWeight"] > 48 * 2


# ─── Assembly ───


class TestQuoteOnTheTrack:
    def test_should_pass_the_quote_through_when_a_moment_carries_one(self) -> None:
        items = normalize_moment_items(
            [{"seconds": 2715, "label": "CLI", "description": "d", "quote": "By next Friday."}]
        )

        assert items[0]["quote"] == "By next Friday."

    def test_should_rank_a_quoted_moment_above_an_unquoted_one(self) -> None:
        plain = {"seconds": 10, "label": "a", "description": "d"}

        assert moment_salience({**plain, "quote": "q"}) == moment_salience(plain) + 1


class TestDescribedSiblingSource:
    def test_should_ship_the_described_sibling_when_the_planned_list_is_label_only(self) -> None:
        extraction = {"learning": {"timestamps": _LABELS}, "narrative": {"keyMoments": _DESCRIBED}}

        items = _tab(_assemble([_moments_tab()], extraction), "timeline")["props"]["items"]

        assert [i["seconds"] for i in items] == [1262, 2440, 2715, 3625]
        assert all(i.get("description") for i in items)

    def test_should_keep_the_planned_list_when_it_is_described(self) -> None:
        planned = [{**m, "description": f"What {m['label']} set."} for m in _LABELS[:6]]

        data = described_moment_source(
            "learning.timestamps", planned, {"narrative": {"keyMoments": _DESCRIBED}}
        )

        assert data is planned

    def test_should_keep_the_planned_list_when_another_tab_ships_the_sibling(self) -> None:
        extraction = {"narrative": {"keyMoments": _DESCRIBED}}

        data = described_moment_source(
            "learning.timestamps", _LABELS, extraction, claimed={"narrative.keyMoments"}
        )

        assert data is _LABELS

    def test_should_keep_the_planned_list_when_the_sibling_is_label_only_too(self) -> None:
        extraction = {"narrative": {"keyMoments": [{"timestamp": 5, "label": "x"}]}}

        assert described_moment_source("learning.timestamps", _LABELS, extraction) is _LABELS
