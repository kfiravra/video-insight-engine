"""Extraction schema + example files after the 1c.4 cleanup (pipeline-1min Appendix D).

Counts come from the plan's briefs, so the schemas carry no duration-scaled
SCALING tables or invention floors any more — but every file must still parse
(JSON skeleton + inline example) and keep its rules. Domains without an example
file get no example block instead of learning's.
"""

from __future__ import annotations

import json

import pytest
from pydantic import BaseModel

from src.models.domain_types import DOMAIN_MODELS, MODIFIER_MODELS
from src.services.pipeline.extraction_prompt import (
    EXAMPLES_DIR,
    SCHEMAS_DIR,
    ExtractionPromptInput,
    _load_domain_example,
    build_extraction_template,
)
from src.services.pipeline.prompt_registry import declared_placeholders

_SCHEMA_NAMES = sorted(p.stem for p in SCHEMAS_DIR.glob("*.txt"))
_EXAMPLE_DOMAINS = sorted(p.stem for p in EXAMPLES_DIR.glob("*.txt"))
_NO_EXAMPLE_DOMAINS = ["gaming", "language", "news", "podcast", "science", "sport"]
_DECODER = json.JSONDecoder()


def _schema_text(name: str) -> str:
    return (SCHEMAS_DIR / f"{name}.txt").read_text()


def _without_ui_lines(text: str) -> str:
    """``> UI:`` / ``> GROUPING:`` lines sit inside the JSON skeleton — drop them to parse."""
    return "\n".join(ln for ln in text.splitlines() if not ln.lstrip().startswith(">"))


def _skeleton_and_rest(name: str) -> tuple[dict, str]:
    body = _without_ui_lines(_schema_text(name))
    start = body.index("\n{") + 1
    skeleton, end = _DECODER.raw_decode(body[start:])
    return skeleton, body[start + end :]


def _inline_example(name: str) -> dict:
    _, rest = _skeleton_and_rest(name)
    marker = rest.index("// Example")
    example, _ = _DECODER.raw_decode(rest[rest.index("{", marker) :])
    return example


def _field_names(model: type[BaseModel]) -> set[str]:
    names: set[str] = set()
    for name, info in model.model_fields.items():
        names.add(name)
        if info.alias:
            names.add(info.alias)
    return names


def _model_for(name: str) -> type[BaseModel]:
    return DOMAIN_MODELS.get(name) or MODIFIER_MODELS[name]


def _key_paths(value: object, prefix: str = "") -> set[str]:
    """Every nested key as a dotted path (``[]`` for list items)."""
    paths: set[str] = set()
    if isinstance(value, dict):
        for key, child in value.items():
            path = f"{prefix}.{key}"
            paths |= {path} | _key_paths(child, path)
    elif isinstance(value, list):
        for child in value:
            paths |= _key_paths(child, f"{prefix}[]")
    return paths


def _dropped_paths(name: str, data: dict) -> set[str]:
    """Keys the model silently drops — what assembly would never see."""
    dumped = _model_for(name).model_validate(data).model_dump(by_alias=True)
    return _key_paths(data) - _key_paths(dumped)


def _example_file_json(domain: str) -> dict:
    text = (EXAMPLES_DIR / f"{domain}.txt").read_text()
    example, _ = _DECODER.raw_decode(text[text.index("\n{") + 1 :])
    return example


class TestSchemaFilesParse:
    @pytest.mark.parametrize("name", _SCHEMA_NAMES)
    def test_should_parse_json_skeleton_into_model_fields_when_ui_lines_dropped(self, name):
        skeleton, _ = _skeleton_and_rest(name)

        assert set(skeleton) <= _field_names(_model_for(name))

    @pytest.mark.parametrize("name", _SCHEMA_NAMES)
    def test_should_parse_inline_example_into_model_fields(self, name):
        example = _inline_example(name)

        assert set(example) <= _field_names(_model_for(name))

    @pytest.mark.parametrize("name", _SCHEMA_NAMES)
    def test_should_keep_every_nested_skeleton_field_when_validated(self, name):
        skeleton, _ = _skeleton_and_rest(name)

        assert _dropped_paths(name, skeleton) == set()

    @pytest.mark.parametrize("name", _SCHEMA_NAMES)
    def test_should_keep_every_nested_inline_example_field_when_validated(self, name):
        assert _dropped_paths(name, _inline_example(name)) == set()

    @pytest.mark.parametrize("domain", _EXAMPLE_DOMAINS)
    def test_should_keep_every_nested_example_file_field_when_validated(self, domain):
        assert _dropped_paths(domain, _example_file_json(domain)) == set()

    @pytest.mark.parametrize("name", _SCHEMA_NAMES)
    def test_should_keep_rules_section(self, name):
        assert "\nRULES:\n" in _schema_text(name)


class TestSchemaFloorsRemoved:
    @pytest.mark.parametrize("name", _SCHEMA_NAMES)
    def test_should_declare_no_placeholders_when_duration_scaling_removed(self, name):
        assert declared_placeholders(_schema_text(name)) == frozenset()

    @pytest.mark.parametrize("name", _SCHEMA_NAMES)
    def test_should_carry_no_scaling_table(self, name):
        assert "SCALING" not in _schema_text(name)

    @pytest.mark.parametrize("name", _SCHEMA_NAMES)
    def test_should_carry_no_minimum_counts(self, name):
        assert "Minimum:" not in _schema_text(name)

    def test_should_not_ask_for_estimated_packing_weight_when_unstated(self):
        text = _schema_text("travel")

        assert "Estimate when the video doesn't state weight" not in text
        assert "Omit `weight` when not stated" in text

    def test_should_keep_the_weight_rule_when_ui_lines_are_stripped(self):
        rules = _without_ui_lines(_schema_text("travel")).split("\nRULES:\n")[1]

        assert "Omit `weight` when not stated" in rules

    def test_should_show_no_sample_weight_in_the_travel_skeleton(self):
        skeleton, _ = _skeleton_and_rest("travel")

        assert skeleton["packingList"][0]["weight"] is None

    @pytest.mark.parametrize(
        ("name", "pressure"),
        [
            ("learning", "MUST list"),
            ("learning", "≤16"),
            ("science", "≤16"),
            ("gaming", "6+ stages"),
        ],
    )
    def test_should_carry_no_item_count_pressure(self, name, pressure):
        assert pressure not in _schema_text(name)

    def test_should_ask_only_for_connections_the_speaker_draws(self):
        assert "0-3 `connections` the speaker draws" in _schema_text("learning")

    def test_should_keep_only_chronological_coverage_rule_for_learning(self):
        text = _schema_text("learning")

        assert "Cover the full video chronologically" in text
        assert "at least one keyPoint" not in text

    def test_should_keep_code_completeness_as_a_rule_for_tech(self):
        assert "every function, command, and example" in _schema_text("tech")

    @pytest.mark.parametrize("name", ["gaming", "news", "podcast", "sport"])
    def test_should_keep_common_mistakes_section(self, name):
        assert "\nCOMMON MISTAKES:\n" in _schema_text(name)

    def test_should_keep_unboxing_note_for_gaming(self):
        assert "UNBOXING / box-break videos" in _schema_text("gaming")


class TestModifierSchemasUseOwnKey:
    @pytest.mark.parametrize("name", sorted(MODIFIER_MODELS))
    def test_should_tell_model_to_return_modifier_under_its_own_key(self, name):
        assert f'under its own "{name}" key' in _schema_text(name)

    def test_should_not_tell_model_to_merge_narrative_into_domain(self):
        assert "Merge this INTO" not in _schema_text("narrative")


class TestExampleFiles:
    @pytest.mark.parametrize("domain", _EXAMPLE_DOMAINS)
    def test_should_take_counts_from_brief_in_header_line(self, domain):
        header = (EXAMPLES_DIR / f"{domain}.txt").read_text().splitlines()[0]

        assert header.endswith("counts come from your brief.")
        assert "density" not in header

    @pytest.mark.parametrize("domain", _EXAMPLE_DOMAINS)
    def test_should_load_domains_own_example_when_file_exists(self, domain):
        example = _load_domain_example(domain)

        assert f"example of {domain} extraction output" in example

    @pytest.mark.parametrize("domain", _NO_EXAMPLE_DOMAINS)
    def test_should_load_no_example_when_domain_has_no_file(self, domain):
        assert _load_domain_example(domain) == ""

    def test_should_load_no_example_when_tag_is_unsafe(self):
        assert _load_domain_example("../learning") == ""

    @pytest.mark.parametrize("domain", _NO_EXAMPLE_DOMAINS)
    def test_should_omit_example_block_when_primary_domain_has_no_example(self, domain):
        prompt = build_extraction_template(ExtractionPromptInput([domain], [], "rules"))

        assert "<extraction_example" not in prompt
        assert "example of learning extraction output" not in prompt

    def test_should_include_example_block_when_primary_domain_has_example(self):
        prompt = build_extraction_template(ExtractionPromptInput(["food"], [], "rules"))

        assert '<extraction_example domain="food">' in prompt
        assert "example of food extraction output" in prompt


def test_examples_dir_holds_only_registered_domains():
    """An example for an unknown domain would never be selected."""
    assert set(_EXAMPLE_DOMAINS) <= set(DOMAIN_MODELS)


def test_schema_dir_matches_models():
    assert set(_SCHEMA_NAMES) == set(DOMAIN_MODELS) | set(MODIFIER_MODELS)
