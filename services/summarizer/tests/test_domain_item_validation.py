"""Per-item extraction validation (hotfix 2.1 + phase-1 close): repair, drop, never pass through."""

from __future__ import annotations

from pydantic import BaseModel

from src.models.domain_types import validate_domain_output_with_drops
from src.models.domain_validation import (
    extraction_with_drops,
    mis_targeted_fields,
    validate_block_items,
)


def _cheat(title: str | None, code: str | None = "x = 1") -> dict:
    return {"title": title, "code": code, "description": f"what {title} does"}


def _t1dq_tech_block() -> dict:
    """T1dQhQAm8Tc v4 (prod 2026-10-08): every cheatSheet item came back with code=null."""
    return {
        "languages": ["TypeScript", "Python", "Bash"],
        "topics": ["agent harnesses", "MCP servers"],
        "cheatSheet": [_cheat(f"tool {i}", code=None) for i in range(22)],
    }


def _t1dq_patterns_block() -> dict:
    """T1dQhQAm8Tc v7 (prod 2026-10-09): step-shaped patterns with ``name``, no ``title``."""
    return {
        "patterns": [
            {
                "name": f"step {i}",
                "description": "d",
                "step": str(i),
                "detail": "x",
                "timestamp": "1040",
            }
            for i in range(7)
        ]
    }


class TestNullOnDefaultedField:
    def test_should_keep_items_whose_defaulted_field_is_null(self) -> None:
        validated, _ = validate_domain_output_with_drops(["tech"], [], _t1dq_tech_block())

        assert len(validated["tech"]["cheatSheet"]) == 22

    def test_should_apply_the_default_to_the_null_field(self) -> None:
        validated, _ = validate_domain_output_with_drops(["tech"], [], _t1dq_tech_block())

        assert {item["code"] for item in validated["tech"]["cheatSheet"]} == {""}

    def test_should_report_no_drops_for_the_t1dq_shape(self) -> None:
        _, report = validate_domain_output_with_drops(["tech"], [], _t1dq_tech_block())

        assert report["dropped"] == {}

    def test_should_count_each_defaulted_field(self) -> None:
        _, report = validate_domain_output_with_drops(["tech"], [], _t1dq_tech_block())

        assert report["defaulted"] == 22


class TestNullOnRequiredField:
    def test_should_drop_an_item_whose_required_field_is_null(self) -> None:
        block = {"cheatSheet": [_cheat("a"), _cheat(None), _cheat("c")]}

        validated, report = validate_domain_output_with_drops(["tech"], [], block)

        assert ([i["title"] for i in validated["tech"]["cheatSheet"]], report["dropped"]) == (
            ["a", "c"],
            {"tech.cheatSheet": {"dropped": 1, "total": 3}},
        )


def _exercise(**overrides: object) -> dict:
    item: dict = {
        "name": "Jumping jacks",
        "sets": 1,
        "reps": "30 seconds",
        "duration": "30",
        "timestamp": 20,
    }
    return {**item, **overrides}


class TestFixableValues:
    def test_should_keep_items_with_a_number_in_a_string_field(self) -> None:
        """Dev golden 2026-10-12: 12/12 fitness exercises came back with duration=30 (int)."""
        block = {"exercises": [_exercise(duration=30) for _ in range(12)]}

        validated, report = validate_domain_output_with_drops(["fitness"], [], block)

        assert (len(validated["fitness"]["exercises"]), report["dropped"]) == (12, {})

    def test_should_keep_the_number_as_text(self) -> None:
        validated, _ = validate_domain_output_with_drops(
            ["fitness"], [], {"exercises": [_exercise(duration=30)]}
        )

        assert validated["fitness"]["exercises"][0]["duration"] == "30"

    def test_should_reset_a_bad_optional_field_and_keep_the_item(self) -> None:
        block = {"exercises": [_exercise(rest=["10s"]), _exercise()]}

        validated, report = validate_domain_output_with_drops(["fitness"], [], block)

        assert (
            [e["rest"] for e in validated["fitness"]["exercises"]],
            report["dropped"],
            report["defaulted"],
        ) == ([None, None], {}, 1)

    def test_should_drop_an_item_whose_required_field_has_a_bad_value(self) -> None:
        block = {"exercises": [_exercise(name={"en": "x"}), _exercise()]}

        validated, report = validate_domain_output_with_drops(["fitness"], [], block)

        assert (len(validated["fitness"]["exercises"]), report["dropped"]) == (
            1,
            {"fitness.exercises": {"dropped": 1, "total": 2}},
        )


class TestTitleNameAliases:
    def test_should_take_title_from_name_when_title_is_missing(self) -> None:
        validated, report = validate_domain_output_with_drops(["tech"], [], _t1dq_patterns_block())

        assert ([p["title"] for p in validated["tech"]["patterns"]], report["dropped"]) == (
            [f"step {i}" for i in range(7)],
            {},
        )

    def test_should_take_name_from_title_when_name_is_missing(self) -> None:
        block = {"concepts": [{"title": "Photosynthesis", "definition": "light to sugar"}]}

        validated, _ = validate_domain_output_with_drops(["learning"], [], block)

        assert validated["learning"]["concepts"][0]["name"] == "Photosynthesis"

    def test_should_drop_an_item_with_neither_title_nor_name(self) -> None:
        block = {"patterns": [{"title": "keep"}, {"description": "no title, no name"}]}

        validated, report = validate_domain_output_with_drops(["tech"], [], block)

        assert ([p["title"] for p in validated["tech"]["patterns"]], report["dropped"]) == (
            ["keep"],
            {"tech.patterns": {"dropped": 1, "total": 2}},
        )


class TestItemLevelPruning:
    def test_should_drop_an_invalid_item_inside_a_nested_list(self) -> None:
        block = {"setup": {"commands": ["npm i", {"cmd": "bad"}, "npm test"]}}

        validated, report = validate_domain_output_with_drops(["tech"], [], block)

        assert (validated["tech"]["setup"]["commands"], report["dropped"]) == (
            ["npm i", "npm test"],
            {"tech.setup.commands": {"dropped": 1, "total": 3}},
        )

    def test_should_reset_a_wrong_type_field_to_its_default(self) -> None:
        block = {"topics": 5, "languages": ["Go"]}

        validated, report = validate_domain_output_with_drops(["tech"], [], block)

        assert (validated["tech"]["topics"], validated["tech"]["languages"], report["dropped"]) == (
            [],
            ["Go"],
            {},
        )

    def test_should_drop_invalid_food_steps_instead_of_passing_them_through(self) -> None:
        block = {"steps": [{"number": "not_a_number", "instruction": 123}]}

        validated, _ = validate_domain_output_with_drops(["food"], [], block)

        assert validated["food"]["steps"] == []


class TestMultiTag:
    def test_should_prune_only_the_tag_with_invalid_items(self) -> None:
        data = {
            "learningData": {"takeaways": ["one", "two"]},
            "techData": {"cheatSheet": [_cheat("a"), _cheat(None)]},
        }

        validated, report = validate_domain_output_with_drops(["learning", "tech"], [], data)

        assert (
            validated["learning"]["takeaways"],
            [i["title"] for i in validated["tech"]["cheatSheet"]],
            report["dropped"],
        ) == (["one", "two"], ["a"], {"tech.cheatSheet": {"dropped": 1, "total": 2}})


class TestMisTargetedFields:
    def test_should_flag_a_planned_field_that_lost_over_half_its_items(self) -> None:
        dropped = {"tech.patterns": {"dropped": 7, "total": 7}}

        assert mis_targeted_fields(dropped, ["tech.patterns", "tech.topics"]) == ["tech.patterns"]

    def test_should_not_flag_a_field_that_lost_exactly_half(self) -> None:
        dropped = {"tech.patterns": {"dropped": 2, "total": 4}}

        assert mis_targeted_fields(dropped, ["tech.patterns"]) == []

    def test_should_not_flag_a_field_no_tab_planned(self) -> None:
        dropped = {"tech.cheatSheet": {"dropped": 5, "total": 5}}

        assert mis_targeted_fields(dropped, ["tech.patterns"]) == []


class _Inner(BaseModel):
    value: int


class _Outer(BaseModel):
    title: str
    inner: list[_Inner] = []


class _Doc(BaseModel):
    items: list[_Outer] = []


class TestValidateBlockItems:
    def test_should_drop_the_deepest_failing_item_not_its_parent(self) -> None:
        block = {"items": [{"title": "keep", "inner": [{"value": 1}, {"value": "x"}]}]}

        validated, dropped, _ = validate_block_items("doc", _Doc, block)

        assert (validated["items"][0]["inner"], dropped) == (
            [{"value": 1}],
            {"doc.items.inner": {"dropped": 1, "total": 2}},
        )

    def test_should_drop_a_parent_item_whose_own_field_fails(self) -> None:
        block = {"items": [{"inner": []}, {"title": "ok"}]}

        validated, _, _ = validate_block_items("doc", _Doc, block)

        assert [i["title"] for i in validated["items"]] == ["ok"]

    def test_should_return_defaults_when_the_block_is_not_an_object(self) -> None:
        validated, dropped, _ = validate_block_items("doc", _Doc, ["not", "a", "dict"])  # type: ignore[arg-type]

        assert (validated, dropped) == ({"items": []}, {"doc.*": {"dropped": 1, "total": 1}})

    def test_should_not_mutate_the_input_block(self) -> None:
        block = {"items": [{"name": "a"}, {"inner": []}]}

        validate_block_items("doc", _Doc, block)

        assert block == {"items": [{"name": "a"}, {"inner": []}]}


class TestExtractionWithDrops:
    def test_should_add_dropped_counts_when_validation_removed_items(self) -> None:
        counts = {"tech.cheatSheet": {"dropped": 3, "total": 22}}

        record = extraction_with_drops({"tech": {}}, counts)

        assert record == {"tech": {}, "_defaulted": 0, "_dropped": counts}

    def test_should_add_mis_targeted_fields_when_set(self) -> None:
        counts = {"tech.patterns": {"dropped": 7, "total": 7}}

        record = extraction_with_drops({"tech": {}}, counts, ["tech.patterns"])

        assert record and record["_misTargeted"] == ["tech.patterns"]

    def test_should_write_the_defaulted_count_on_every_run(self) -> None:
        record = extraction_with_drops({"tech": {"topics": ["a"]}}, {}, None, 3)

        assert record == {"tech": {"topics": ["a"]}, "_defaulted": 3}
