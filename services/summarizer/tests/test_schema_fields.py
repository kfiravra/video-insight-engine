"""Field-aware schema renderer (pipeline-1min 3.1): skeletons, scalars, slices, key filter, emit line."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import pytest

from src.services.pipeline import schema_fields
from src.services.pipeline.extraction_prompt import SCHEMAS_DIR
from src.services.pipeline.schema_fields import (
    SchemaParseError,
    keep_requested,
    load_domain_schema,
    render_emit_only,
    scalar_fields,
    slice_skeleton,
)
from src.shared_config.domain_config import data_sources

# Design §3.1: each schema's top-level non-list keys that no registry dataSource reads.
_SCALARS: dict[str, tuple[str, ...]] = {
    "finance": (),
    "fitness": ("meta",),
    "food": ("meta",),
    "gaming": ("title",),
    "language": ("targetLanguage", "nativeLanguage", "level"),
    "learning": ("keyQuestion", "summary"),
    "music": ("title", "artist"),
    "narrative": (),
    "news": ("headline",),
    "podcast": ("show", "host"),
    "project": ("projectName", "difficulty", "estimatedTime", "estimatedCost"),
    "review": ("product", "price", "rating"),
    "science": ("field", "level"),
    "sport": ("title",),
    "tech": (),
    "travel": ("bestSeason",),
}
_SCHEMA_NAMES = sorted(path.stem for path in SCHEMAS_DIR.glob("*.txt"))

_FOOD_CAPS: dict[str, int | None] = {"food.ingredients": 30, "food.steps": 25, "food.meta": None}
_BROKEN_TEXT = 'X schema\n\n{\n  "a": [\n}\n'


def _requestable_fields(domain: str) -> list[str]:
    """The domain's registry fields; a modifier with none (finance) is requested by its own keys."""
    fields = [spec["field"] for spec in data_sources().values() if spec["domain"] == domain]
    return fields or list(load_domain_schema(domain).skeleton)


def _value_at(skeleton: dict[str, Any], field: str) -> Any:
    node: Any = skeleton
    for key in field.split("."):
        node = node[key]
    return node


def _nested(field: str, leaf: Any) -> dict[str, Any]:
    """``setup.commands`` + leaf → ``{"setup": {"commands": leaf}}``."""
    tree: Any = leaf
    for key in reversed(field.split(".")):
        tree = {key: tree}
    return tree


@pytest.fixture
def schema_text(monkeypatch: pytest.MonkeyPatch) -> Callable[[str], None]:
    """Serve a fixed text as every schema; parses are keyed by text, so no cache reset."""

    def _serve(text: str) -> None:
        monkeypatch.setattr(schema_fields, "load_schema_text", lambda _name: text)

    return _serve


# ─── load_domain_schema ───


class TestLoadDomainSchema:
    @pytest.mark.parametrize("name", _SCHEMA_NAMES)
    def test_should_parse_every_schema_when_directive_lines_are_stripped(self, name: str) -> None:
        schema = load_domain_schema(name)

        assert schema.skeleton

    def test_should_drop_directive_lines_when_they_sit_inside_the_object(
        self, schema_text: Callable[[str], None]
    ) -> None:
        schema_text(
            'X schema\n\n{\n  "a": [],\n> UI: a → list\n  "b": "x"\n}\n\n// Ex:\n{"a": [1]}'
        )

        assert load_domain_schema("synthetic").skeleton == {"a": [], "b": "x"}

    def test_should_follow_the_served_text_when_it_changes(
        self, schema_text: Callable[[str], None]
    ) -> None:
        schema_text('X schema\n\n{\n  "a": []\n}\n')
        load_domain_schema("synthetic")
        schema_text('X schema\n\n{\n  "b": []\n}\n')

        assert load_domain_schema("synthetic").skeleton == {"b": []}

    def test_should_flag_the_schema_broken_when_its_text_is_not_json(
        self, schema_text: Callable[[str], None]
    ) -> None:
        schema_text(_BROKEN_TEXT)

        assert load_domain_schema("synthetic") == schema_fields.DomainSchema(
            "synthetic", {}, broken=True
        )

    def test_should_not_flag_the_schema_broken_when_the_name_is_unknown(self) -> None:
        assert load_domain_schema("no_such_domain").broken is False

    def test_should_return_an_empty_skeleton_when_the_name_is_unsafe(self) -> None:
        assert load_domain_schema("../tech").skeleton == {}

    def test_should_return_an_empty_skeleton_when_no_schema_file_exists(self) -> None:
        assert load_domain_schema("no_such_domain").skeleton == {}

    def test_should_hand_out_a_copy_when_the_caller_edits_the_skeleton(self) -> None:
        load_domain_schema("food").skeleton["meta"] = "edited"

        assert isinstance(load_domain_schema("food").skeleton["meta"], dict)


# ─── scalar_fields ───


class TestScalarFields:
    @pytest.mark.parametrize(("domain", "expected"), sorted(_SCALARS.items()))
    def test_should_list_the_scalar_fields_when_a_domain_has_non_list_keys(
        self, domain: str, expected: tuple[str, ...]
    ) -> None:
        assert scalar_fields(domain) == expected

    def test_should_have_a_scalar_row_for_every_schema_file(self) -> None:
        assert sorted(_SCALARS) == _SCHEMA_NAMES

    def test_should_list_no_scalars_when_the_domain_is_unknown(self) -> None:
        assert scalar_fields("no_such_domain") == ()


# ─── slice_skeleton ───


class TestSliceSkeleton:
    @pytest.mark.parametrize("domain", _SCHEMA_NAMES)
    def test_should_slice_only_the_requested_keys_and_their_parents_when_given_registry_fields(
        self, domain: str
    ) -> None:
        skeleton = load_domain_schema(domain).skeleton
        fields = _requestable_fields(domain)

        slices = {field: slice_skeleton(domain, [field]) for field in fields}

        assert slices == {field: _nested(field, _value_at(skeleton, field)) for field in fields}

    def test_should_round_trip_a_slice_through_json_dumps_loads_when_nested_paths_are_requested(
        self,
    ) -> None:
        sliced = slice_skeleton("tech", ["setup.commands"])

        assert json.loads(json.dumps(sliced)) == {
            "setup": {"commands": ["Exact CLI commands shown"]}
        }

    def test_should_keep_the_request_order_when_several_fields_are_requested(self) -> None:
        sliced = slice_skeleton("food", ["steps", "meta", "ingredients"])

        assert list(sliced) == ["steps", "meta", "ingredients"]

    def test_should_share_one_parent_when_sibling_paths_are_requested(self) -> None:
        sliced = slice_skeleton("tech", ["setup.commands", "setup.envVars"])

        assert list(sliced["setup"]) == ["commands", "envVars"]

    def test_should_keep_the_whole_parent_when_it_is_requested_with_a_child(self) -> None:
        sliced = slice_skeleton("tech", ["setup.commands", "setup"])

        assert sliced == {"setup": load_domain_schema("tech").skeleton["setup"]}

    def test_should_leave_out_a_field_when_the_schema_lacks_it(self) -> None:
        sliced = slice_skeleton("food", ["ingredients", "calories"])

        assert list(sliced) == ["ingredients"]

    def test_should_leave_out_a_path_when_it_runs_through_a_list(self) -> None:
        assert slice_skeleton("tech", ["snippets.code"]) == {}

    def test_should_return_a_copy_when_the_caller_edits_the_slice(self) -> None:
        slice_skeleton("food", ["meta"])["meta"]["cuisine"] = "edited"

        assert slice_skeleton("food", ["meta"])["meta"]["cuisine"] != "edited"

    def test_should_raise_when_the_schema_is_broken(
        self, schema_text: Callable[[str], None]
    ) -> None:
        schema_text(_BROKEN_TEXT)

        with pytest.raises(SchemaParseError):
            slice_skeleton("synthetic", ["a"])


# ─── keep_requested ───


class TestKeepRequested:
    def test_should_keep_only_the_requested_paths_when_the_data_has_extra_keys(self) -> None:
        data = {
            "setup": {"commands": ["npm i"], "envVars": [{"name": "PORT"}]},
            "topics": ["hooks"],
            "snippets": [{"code": "x"}],
        }

        kept = keep_requested(data, ["setup.commands", "topics"])

        assert kept == {"setup": {"commands": ["npm i"]}, "topics": ["hooks"]}

    def test_should_keep_a_requested_object_whole(self) -> None:
        data = {"meta": {"prepTime": "5 min", "servings": "4"}, "steps": []}

        assert keep_requested(data, ["meta"]) == {"meta": {"prepTime": "5 min", "servings": "4"}}

    def test_should_leave_a_requested_field_absent_when_the_data_lacks_it(self) -> None:
        assert keep_requested({"ingredients": []}, ["ingredients", "steps"]) == {"ingredients": []}

    def test_should_skip_a_path_when_the_data_holds_a_non_object_on_the_way(self) -> None:
        assert keep_requested({"setup": "npm i"}, ["setup.commands"]) == {}

    def test_should_return_a_copy_when_the_caller_edits_the_result(self) -> None:
        data = {"steps": [{"number": 1}]}

        keep_requested(data, ["steps"])["steps"][0]["number"] = 9

        assert data == {"steps": [{"number": 1}]}


# ─── render_emit_only ───


class TestRenderEmitOnly:
    def test_should_render_the_emit_only_line_with_caps_when_a_group_has_scalars(self) -> None:
        slices = {"food": slice_skeleton("food", ["ingredients", "steps", "meta"])}

        rendered = render_emit_only(slices, _FOOD_CAPS)

        assert rendered == (
            "Emit ONLY these keys (anything else is discarded): "
            "food.ingredients (cap 30), food.steps (cap 25), food.meta\n"
            'Output shape: {"food":{"ingredients":[…],"steps":[…],"meta":{…}}}'
        )

    def test_should_name_the_full_path_when_a_nested_field_is_requested(self) -> None:
        slices = {"tech": slice_skeleton("tech", ["setup.commands"])}

        rendered = render_emit_only(slices, {"tech.setup.commands": 25})

        assert rendered == (
            "Emit ONLY these keys (anything else is discarded): tech.setup.commands (cap 25)\n"
            'Output shape: {"tech":{"setup":{"commands":[…]}}}'
        )

    def test_should_render_a_string_scalar_as_a_quoted_ellipsis(self) -> None:
        slices = {"gaming": slice_skeleton("gaming", ["rankings", "title"])}

        rendered = render_emit_only(slices, {"gaming.rankings": 20, "gaming.title": None})

        assert rendered.endswith('Output shape: {"gaming":{"rankings":[…],"title":"…"}}')

    def test_should_omit_the_cap_when_the_field_is_an_object(self) -> None:
        slices = {"travel": slice_skeleton("travel", ["budget"])}

        rendered = render_emit_only(slices, {"travel.budget": 1})

        assert rendered.startswith(
            "Emit ONLY these keys (anything else is discarded): travel.budget\n"
        )

    def test_should_render_every_domain_when_a_group_spans_a_modifier(self) -> None:
        slices = {
            "travel": slice_skeleton("travel", ["itinerary"]),
            "finance": slice_skeleton("finance", ["costs"]),
        }

        rendered = render_emit_only(slices, {"travel.itinerary": 25, "finance.costs": None})

        assert rendered.endswith(
            'Output shape: {"travel":{"itinerary":[…]},"finance":{"costs":[…]}}'
        )

    def test_should_raise_when_caps_leave_a_requested_object_out(self) -> None:
        slices = {"travel": slice_skeleton("travel", ["itinerary", "budget"])}

        with pytest.raises(ValueError, match="travel.budget"):
            render_emit_only(slices, {"travel.itinerary": 25})

    def test_should_raise_when_caps_leave_a_requested_scalar_out(self) -> None:
        slices = {"gaming": slice_skeleton("gaming", ["rankings", "title"])}

        with pytest.raises(ValueError, match="gaming.title"):
            render_emit_only(slices, {"gaming.rankings": 20})

    def test_should_raise_instead_of_rendering_an_empty_line_when_the_schema_is_broken(
        self, schema_text: Callable[[str], None]
    ) -> None:
        schema_text(_BROKEN_TEXT)

        with pytest.raises(SchemaParseError):
            render_emit_only({"food": slice_skeleton("food", ["ingredients"])}, _FOOD_CAPS)

    def test_should_render_nothing_when_no_slice_has_keys(self) -> None:
        assert render_emit_only({"food": {}}, {}) == ""
