"""Phase 3.5: per-group validation without defaults, the key filter, the C12 merge, quality."""

from __future__ import annotations

import logging
from typing import Any
from unittest.mock import patch

import pytest

from src.models.domain_validation import ValidationReport
from src.services.pipeline import group_merge
from src.services.pipeline.extraction_groups import GroupOutcome, GroupStatus
from src.services.pipeline.field_groups import ExtractionGroup
from src.services.pipeline.field_requests import FieldKind, FieldRequest
from src.services.pipeline.group_merge import (
    merge_groups,
    merge_order,
    requested_quality,
    validate_group,
)
from src.services.pipeline.schema_fields import DomainSchema


def _field(path: str, kind: FieldKind = "list") -> FieldRequest:
    domain, _, field = path.partition(".")
    return FieldRequest(domain, field, kind, "planned", (), None, 1, 40, False)


def _group(group_id: str, *paths: str) -> ExtractionGroup:
    fields = tuple(_field(p) for p in paths)
    return ExtractionGroup(group_id, fields, group_id == "visual", 40 * len(fields), ())


def _outcome(
    group_id: str,
    data: dict[str, Any] | None,
    *,
    chunk: int = 0,
    status: GroupStatus = "ok",
    validation: ValidationReport | None = None,
) -> GroupOutcome:
    report = validation or {"dropped": {}, "defaulted": 0}
    return GroupOutcome(_group(group_id, "food.tips"), chunk, data, report, status, 1, 0)


def _food(raw: dict[str, Any], *paths: str) -> dict[str, Any]:
    validated, _ = validate_group(raw, _group("text1", *paths), ["food"], [])
    return validated


# ─── validate_group ───


class TestValidateGroup:
    def test_should_keep_only_the_groups_own_keys_when_the_model_emits_extra_domains(
        self,
    ) -> None:
        raw = {"food": {"ingredients": [{"name": "eggs"}]}, "travel": {"itinerary": []}}

        validated, _ = validate_group(
            raw, _group("text1", "food.ingredients"), ["food", "travel"], []
        )

        assert list(validated) == ["food"]

    def test_should_drop_unrequested_keys_of_its_own_domain(self) -> None:
        raw = {"food": {"ingredients": [{"name": "eggs"}], "steps": [{"number": 1}]}}

        assert list(_food(raw, "food.ingredients")["food"]) == ["ingredients"]

    def test_should_not_materialize_unrequested_fields(self) -> None:
        validated = _food({"ingredients": [{"name": "eggs"}]}, "food.ingredients")

        assert set(validated["food"]) == {"ingredients"}

    def test_should_leave_a_requested_field_the_model_omitted_absent(self) -> None:
        validated = _food({"ingredients": [{"name": "eggs"}]}, "food.ingredients", "food.meta")

        assert "meta" not in validated["food"]

    @pytest.mark.parametrize("value", [None, {}])
    def test_should_leave_a_null_or_empty_object_absent(self, value: object) -> None:
        validated = _food({"ingredients": [], "meta": value}, "food.ingredients", "food.meta")

        assert "meta" not in validated["food"]

    def test_should_keep_an_empty_list_the_model_returned(self) -> None:
        assert _food({"ingredients": []}, "food.ingredients") == {"food": {"ingredients": []}}

    def test_should_keep_item_level_defaults(self) -> None:
        [eggs] = _food({"ingredients": [{"name": "eggs"}]}, "food.ingredients")["food"][
            "ingredients"
        ]

        assert (eggs["amount"], eggs["displayAmount"], eggs["unit"]) == (None, "", None)

    def test_should_drop_an_invalid_item_and_report_it(self) -> None:
        raw = {"ingredients": [{"name": "eggs"}, {"amount": 2}]}

        validated, report = validate_group(raw, _group("text1", "food.ingredients"), ["food"], [])

        assert (len(validated["food"]["ingredients"]), report.get("dropped")) == (
            1,
            {"food.ingredients": {"dropped": 1, "total": 2}},
        )

    def test_should_count_drops_on_the_groups_own_keys_only(self) -> None:
        raw = {"ingredients": [{"name": "eggs"}], "steps": [{"title": "no number"}]}

        _, report = validate_group(raw, _group("text1", "food.ingredients"), ["food"], [])

        assert report.get("dropped") == {}

    def test_should_leave_extraction_data_domain_keyed_when_the_answer_is_flat(self) -> None:
        assert list(_food({"ingredients": []}, "food.ingredients")) == ["food"]

    def test_should_read_a_domain_data_wrapper(self) -> None:
        validated = _food({"foodData": {"ingredients": [{"name": "eggs"}]}}, "food.ingredients")

        assert validated["food"]["ingredients"][0]["name"] == "eggs"

    def test_should_attribute_flat_keys_to_the_one_domain_requesting_them(self) -> None:
        raw = {"concepts": [{"name": "agent"}], "tech": {"topics": ["svelte"]}}
        group = _group("text1", "learning.concepts", "tech.topics")

        validated, _ = validate_group(raw, group, ["learning", "tech"], [])

        topics = [topic["name"] for topic in validated["tech"]["topics"]]
        assert (validated["learning"]["concepts"][0]["name"], topics) == ("agent", ["svelte"])

    def test_should_keep_a_dotted_path_with_its_parents_only(self) -> None:
        raw = {"tech": {"setup": {"commands": ["npm i"], "dependencies": [{"name": "x"}]}}}

        validated, _ = validate_group(raw, _group("visual", "tech.setup.commands"), ["tech"], [])

        assert validated == {"tech": {"setup": {"commands": ["npm i"]}}}

    def test_should_read_a_field_name_spelling_onto_the_registry_key(self) -> None:
        raw = {"tech": {"cheat_sheet": [{"title": "list", "code": "ls -la"}]}}

        validated, _ = validate_group(raw, _group("visual", "tech.cheatSheet"), ["tech"], [])

        assert validated["tech"]["cheatSheet"][0]["code"] == "ls -la"

    def test_should_prefer_the_registry_spelling_when_both_spellings_appear(self) -> None:
        raw = {"tech": {"cheat_sheet": [{"title": "old"}], "cheatSheet": [{"title": "new"}]}}

        validated, _ = validate_group(raw, _group("visual", "tech.cheatSheet"), ["tech"], [])

        assert [item["title"] for item in validated["tech"]["cheatSheet"]] == ["new"]

    def test_should_log_the_keys_outside_the_request(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level(logging.INFO, logger=group_merge.__name__):
            _food({"ingredients": [], "steps": []}, "food.ingredients")

        assert any("discarded: ['steps']" in r.getMessage() for r in caplog.records)

    def test_should_validate_a_modifier_with_its_modifier_model(self) -> None:
        raw = {"narrative": {"keyMoments": [{"description": "the reveal"}]}}
        group = _group("text2", "narrative.keyMoments")

        validated, _ = validate_group(raw, group, ["food"], ["narrative"])

        assert validated["narrative"]["keyMoments"][0]["endTimestamp"] is None

    def test_should_discard_a_domain_that_is_not_active(self) -> None:
        raw = {"food": {"ingredients": [{"name": "eggs"}]}}

        validated, _ = validate_group(raw, _group("text1", "food.ingredients"), ["tech"], [])

        assert validated == {}

    def test_should_keep_nothing_of_a_block_validation_reset_to_defaults(self) -> None:
        dropped = {"food.*": {"dropped": 1, "total": 1}}
        reset = ({"meta": {"servings": None}, "ingredients": []}, dropped, 0)

        with patch.object(group_merge, "validate_block_items", return_value=reset):
            validated = _food({"meta": {"servings": "x"}}, "food.meta")

        assert validated == {}

    def test_should_validate_the_domain_unfiltered_when_its_schema_is_broken(self) -> None:
        raw = {"ingredients": [{"name": "eggs"}], "steps": [{"number": 1, "instruction": "go"}]}
        broken = DomainSchema("food", {}, broken=True)

        with patch.object(group_merge, "load_domain_schema", return_value=broken):
            validated = _food(raw, "food.ingredients")

        assert set(validated["food"]) == {"ingredients", "steps"}

    def test_should_log_an_error_when_the_schema_is_broken(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        broken = DomainSchema("food", {}, broken=True)

        with (
            patch.object(group_merge, "load_domain_schema", return_value=broken),
            caplog.at_level(logging.ERROR, logger=group_merge.__name__),
        ):
            _food({"ingredients": []}, "food.ingredients")

        assert any("does not parse" in r.getMessage() for r in caplog.records)


# ─── merge_groups ───


def _tips(*names: str) -> dict[str, Any]:
    return {"food": {"tips": [{"tip": n} for n in names]}}


class TestMergeGroups:
    def test_should_merge_chunk_major_then_group_order(self) -> None:
        outcomes = [
            _outcome("visual", _tips("v0")),
            _outcome("text1", _tips("t1c1"), chunk=1),
            _outcome("text2", _tips("t2c0")),
            _outcome("text1", _tips("t1c0")),
        ]

        merged, _ = merge_groups(outcomes)

        assert [t["tip"] for t in merged["food"]["tips"]] == ["t1c0", "t2c0", "v0", "t1c1"]

    def test_should_order_text10_after_text2(self) -> None:
        outcomes = [_outcome("text10", {}), _outcome("text2", {}), _outcome("text1", {})]

        assert [o.group.group_id for o in merge_order(outcomes)] == ["text1", "text2", "text10"]

    def test_should_keep_the_first_non_empty_scalar(self) -> None:
        outcomes = [
            _outcome("text1", {"learning": {"summary": ""}}),
            _outcome("text1", {"learning": {"summary": "Short"}}, chunk=1),
            _outcome("text1", {"learning": {"summary": "A much longer summary"}}, chunk=2),
        ]

        merged, _ = merge_groups(outcomes)

        assert merged["learning"]["summary"] == "Short"

    def test_should_merge_objects_key_by_key(self) -> None:
        outcomes = [
            _outcome("text1", {"food": {"meta": {"servings": 4, "cuisine": None}}}),
            _outcome("text1", {"food": {"meta": {"servings": 2, "cuisine": "thai"}}}, chunk=1),
        ]

        merged, _ = merge_groups(outcomes)

        assert merged["food"]["meta"] == {"servings": 4, "cuisine": "thai"}

    def test_should_drop_a_later_contributions_item_with_the_same_identity(self) -> None:
        a = {"food": {"ingredients": [{"name": "Eggs", "amount": 2}]}}
        b = {"food": {"ingredients": [{"name": " eggs ", "amount": 3}, {"name": "salt"}]}}

        merged, _ = merge_groups([_outcome("text1", a), _outcome("text1", b, chunk=1)])

        assert [(i["name"], i.get("amount")) for i in merged["food"]["ingredients"]] == [
            ("Eggs", 2),
            ("salt", None),
        ]

    def test_should_keep_repeats_within_one_contribution(self) -> None:
        moments = [{"label": "Demo", "seconds": 10}, {"label": "Demo", "seconds": 90}]
        later = {"learning": {"timestamps": [{"label": "Outro", "seconds": 900}]}}
        outcomes = [
            _outcome("text1", {"learning": {"timestamps": moments}}),
            _outcome("text1", later, chunk=1),
        ]

        merged, _ = merge_groups(outcomes)

        assert [t["seconds"] for t in merged["learning"]["timestamps"]] == [10, 90, 900]

    def test_should_dedupe_by_a_fixed_identity_key_order_regardless_of_hash_seed(self) -> None:
        # name precedes title: different names keep both, though the titles collide
        first = {"food": {"steps": [{"name": "boil", "title": "Water"}]}}
        second = {"food": {"steps": [{"name": "salt", "title": "water"}]}}

        merged, _ = merge_groups([_outcome("text1", first), _outcome("text1", second, chunk=1)])

        assert [s["name"] for s in merged["food"]["steps"]] == ["boil", "salt"]

    def test_should_renumber_steps_that_ran_one_to_n_in_every_list(self) -> None:
        def steps(*names: str) -> dict[str, Any]:
            items = [{"number": n, "instruction": s} for n, s in enumerate(names, start=1)]
            return {"food": {"steps": items}}

        outcomes = [_outcome("text1", steps("a", "b")), _outcome("text1", steps("c"), chunk=1)]

        merged, _ = merge_groups(outcomes)

        assert [s["number"] for s in merged["food"]["steps"]] == [1, 2, 3]

    def test_should_not_renumber_shirt_numbers(self) -> None:
        def formation(*players: tuple[str, int]) -> dict[str, Any]:
            positions = [{"player": p, "number": n} for p, n in players]
            return {"sport": {"formation": {"name": "4-3-3", "positions": positions}}}

        outcomes = [
            _outcome("text1", formation(("Messi", 10), ("Xavi", 6))),
            _outcome("text1", formation(("Puyol", 5)), chunk=1),
        ]

        merged, _ = merge_groups(outcomes)

        assert [p["number"] for p in merged["sport"]["formation"]["positions"]] == [10, 6, 5]

    def test_should_skip_failed_groups(self) -> None:
        outcomes = [_outcome("text1", _tips("a")), _outcome("text2", None, status="failed")]

        merged, _ = merge_groups(outcomes)

        assert merged == _tips("a")

    def test_should_sum_the_groups_validation_reports(self) -> None:
        def report(dropped: int, defaulted: int) -> ValidationReport:
            counts = {"food.tips": {"dropped": dropped, "total": 4}}
            return {"dropped": counts, "defaulted": defaulted}

        outcomes = [
            _outcome("text1", _tips("a"), validation=report(1, 2)),
            _outcome("text1", _tips("b"), chunk=1, validation=report(2, 1)),
        ]

        _, merged_report = merge_groups(outcomes)

        assert merged_report == {
            "dropped": {"food.tips": {"dropped": 3, "total": 8}},
            "defaulted": 3,
        }

    def test_should_leave_extraction_data_domain_keyed(self) -> None:
        outcomes = [
            _outcome("text1", {"learning": {"concepts": []}}),
            _outcome("visual", {"tech": {"snippets": []}}),
        ]

        merged, _ = merge_groups(outcomes)

        assert list(merged) == ["learning", "tech"]

    def test_should_not_touch_the_outcomes_data_when_it_renumbers(self) -> None:
        first = {"food": {"steps": [{"number": 1, "instruction": "a"}]}}
        second = {"food": {"steps": [{"number": 1, "instruction": "b"}]}}

        merge_groups([_outcome("text1", first), _outcome("text1", second, chunk=1)])

        assert second["food"]["steps"][0]["number"] == 1


# ─── requested_quality ───


class TestRequestedQuality:
    def test_should_count_coverage_on_requested_fields_only(self) -> None:
        extraction = {"food": {"ingredients": [{"name": "eggs"}], "steps": [], "tips": [{}]}}
        fields = [_field("food.ingredients"), _field("food.steps")]

        quality = requested_quality(fields, extraction)

        assert (quality.populated, quality.total, quality.empty_fields) == (1, 2, ["food.steps"])

    def test_should_count_an_empty_scalar_as_empty(self) -> None:
        quality = requested_quality([_field("learning.summary", "scalar")], {"learning": {}})

        assert quality.score == 0.0

    def test_should_count_a_field_two_groups_requested_once(self) -> None:
        fields = [_field("food.tips"), _field("food.tips")]

        assert requested_quality(fields, _tips("a")).total == 1
