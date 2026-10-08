"""Per-item extraction validation (hotfix 2.1): invalid items are dropped, never passed through."""

from __future__ import annotations

from pydantic import BaseModel

from src.models.domain_types import validate_domain_output_with_drops
from src.models.domain_validation import extraction_with_drops, validate_block_items


def _cheat(title: str, code: str | None = "x = 1") -> dict:
    return {"title": title, "code": code, "description": f"what {title} does"}


def _t1dq_tech_block() -> dict:
    """T1dQhQAm8Tc v4 (prod 2026-10-08): every cheatSheet item came back with code=null."""
    return {
        "languages": ["TypeScript", "Python", "Bash"],
        "topics": ["agent harnesses", "MCP servers"],
        "cheatSheet": [_cheat(f"tool {i}", code=None) for i in range(22)],
    }


class TestSingleTagT1dQShape:
    def test_should_drop_every_cheatsheet_item_with_null_code(self) -> None:
        validated, _ = validate_domain_output_with_drops(["tech"], [], _t1dq_tech_block())

        assert validated["tech"]["cheatSheet"] == []

    def test_should_keep_the_valid_fields_next_to_the_dropped_items(self) -> None:
        validated, _ = validate_domain_output_with_drops(["tech"], [], _t1dq_tech_block())

        assert validated["tech"]["topics"] == ["agent harnesses", "MCP servers"]

    def test_should_count_the_dropped_items_per_field(self) -> None:
        _, dropped = validate_domain_output_with_drops(["tech"], [], _t1dq_tech_block())

        assert dropped == {"tech.cheatSheet": 22}


class TestItemLevelPruning:
    def test_should_keep_valid_items_in_order_when_some_fail(self) -> None:
        block = {
            "cheatSheet": [
                _cheat("a"),
                _cheat("b", code=None),
                _cheat("c"),
                {"code": "y = 2"},  # no title
                _cheat("d"),
            ]
        }

        validated, dropped = validate_domain_output_with_drops(["tech"], [], block)

        assert ([i["title"] for i in validated["tech"]["cheatSheet"]], dropped) == (
            ["a", "c", "d"],
            {"tech.cheatSheet": 2},
        )

    def test_should_drop_an_invalid_item_inside_a_nested_list(self) -> None:
        block = {"setup": {"commands": ["npm i", {"cmd": "bad"}, "npm test"]}}

        validated, dropped = validate_domain_output_with_drops(["tech"], [], block)

        assert (validated["tech"]["setup"]["commands"], dropped) == (
            ["npm i", "npm test"],
            {"tech.setup.commands": 1},
        )

    def test_should_reset_a_wrong_type_field_to_its_default(self) -> None:
        block = {"topics": 5, "languages": ["Go"]}

        validated, dropped = validate_domain_output_with_drops(["tech"], [], block)

        assert (validated["tech"]["topics"], validated["tech"]["languages"], dropped) == (
            [],
            ["Go"],
            {"tech.topics": 1},
        )

    def test_should_report_no_drops_when_the_block_is_valid(self) -> None:
        _, dropped = validate_domain_output_with_drops(["tech"], [], {"cheatSheet": [_cheat("a")]})

        assert dropped == {}


class TestMultiTag:
    def test_should_prune_only_the_tag_with_invalid_items(self) -> None:
        data = {
            "learningData": {"takeaways": ["one", "two"]},
            "techData": {"cheatSheet": [_cheat("a"), _cheat("b", code=None)]},
        }

        validated, dropped = validate_domain_output_with_drops(["learning", "tech"], [], data)

        assert (
            validated["learning"]["takeaways"],
            [i["title"] for i in validated["tech"]["cheatSheet"]],
            dropped,
        ) == (["one", "two"], ["a"], {"tech.cheatSheet": 1})


class _Inner(BaseModel):
    value: int


class _Outer(BaseModel):
    name: str
    inner: list[_Inner] = []


class _Doc(BaseModel):
    items: list[_Outer] = []


class TestValidateBlockItems:
    def test_should_drop_the_deepest_failing_item_not_its_parent(self) -> None:
        block = {"items": [{"name": "keep", "inner": [{"value": 1}, {"value": "x"}]}]}

        validated, dropped = validate_block_items("doc", _Doc, block)

        assert (validated["items"][0]["inner"], dropped) == (
            [{"value": 1}],
            {"doc.items.inner": 1},
        )

    def test_should_drop_a_parent_item_whose_own_field_fails(self) -> None:
        block = {"items": [{"inner": []}, {"name": "ok"}]}

        validated, _ = validate_block_items("doc", _Doc, block)

        assert [i["name"] for i in validated["items"]] == ["ok"]

    def test_should_return_defaults_when_the_block_is_not_an_object(self) -> None:
        validated, dropped = validate_block_items("doc", _Doc, ["not", "a", "dict"])  # type: ignore[arg-type]

        assert (validated, dropped) == ({"items": []}, {"doc.*": 1})

    def test_should_not_mutate_the_input_block(self) -> None:
        block = {"items": [{"name": "a"}, {"inner": []}]}

        validate_block_items("doc", _Doc, block)

        assert len(block["items"]) == 2


class TestExtractionWithDrops:
    def test_should_add_dropped_counts_when_validation_removed_items(self) -> None:
        record = extraction_with_drops({"tech": {}}, {"tech.cheatSheet": 3})

        assert record == {"tech": {}, "_dropped": {"tech.cheatSheet": 3}}

    def test_should_leave_the_extraction_unchanged_when_nothing_was_dropped(self) -> None:
        extraction = {"tech": {"topics": ["a"]}}

        assert extraction_with_drops(extraction, {}) is extraction
