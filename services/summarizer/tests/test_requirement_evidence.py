"""Domain requirements conditional on evidence (pipeline-1min D15 / C19).

A domain's ``required`` component is required only when the plan's evidence
for its ``requirementEvidence`` key is not false — a food vlog with no recipe
needs no checklist or step_player. The same rule drives the plan prompt's
rendered requirements and ``ruled_out_requirements``, the one helper assembly's
backfill and demotion guard use.
"""

from __future__ import annotations

import pytest

from src.shared_config.domain_config import (
    domain_requirements,
    effective_requirements,
    render_domain_requirements,
    render_extraction_caps,
    render_requirement,
    requirement_evidence,
    ruled_out_requirements,
)


class TestRuledOutRequirements:
    def test_should_rule_out_requirement_when_its_evidence_is_false(self) -> None:
        evidence = {"has_ingredients": False, "has_steps": True}

        assert ruled_out_requirements("food", evidence) == {"checklist": "has_ingredients"}

    def test_should_rule_out_every_requirement_when_food_vlog_has_no_recipe(self) -> None:
        ruled_out = ruled_out_requirements("food", {"has_ingredients": False, "has_steps": False})

        assert set(effective_requirements("food", "vlog")["required"]) <= set(ruled_out)

    def test_should_rule_out_nothing_when_evidence_key_is_missing(self) -> None:
        assert ruled_out_requirements("food", {"has_steps": True}) == {}

    def test_should_rule_out_nothing_when_no_evidence_is_given(self) -> None:
        assert ruled_out_requirements("food", None) == {}

    def test_should_keep_ungated_requirement_when_all_evidence_is_false(self) -> None:
        evidence: dict[str, bool] = dict.fromkeys(
            ("has_steps", "has_claims", "is_learnable"), False
        )

        assert ruled_out_requirements("podcast", evidence) == {}

    def test_should_gate_playbook_requirement_when_its_evidence_is_false(self) -> None:
        ruled_out = ruled_out_requirements("gaming", {"has_ranking": False})

        assert set(effective_requirements("gaming", "unboxing")["required"]) <= set(ruled_out)

    def test_should_keep_every_requirement_in_the_unconditional_policy(self) -> None:
        assert (
            effective_requirements("food")["required"] == domain_requirements()["food"]["required"]
        )


class TestRenderRequirements:
    @pytest.mark.parametrize(
        ("domain", "component", "expected"),
        [
            ("food", "checklist", "`checklist` only when has_ingredients is true"),
            ("tech", "code_playground", "`code_playground` only when has_code is true"),
            ("language", "spot_explorer", "`spot_explorer` always"),
        ],
    )
    def test_should_state_the_condition_when_rendering_one_requirement(
        self, domain: str, component: str, expected: str
    ) -> None:
        assert render_requirement(domain, component) == expected

    def test_should_render_a_line_per_domain_with_requirements_when_rendering(self) -> None:
        rendered = {line.split(":", 1)[0] for line in render_domain_requirements().splitlines()}

        expected = {f"- {d}" for d, rules in domain_requirements().items() if rules.get("required")}
        assert rendered == expected

    def test_should_gate_every_registry_condition_when_rendering(self) -> None:
        rendered = render_domain_requirements()

        missing = [
            f"{domain}.{component}"
            for domain, gates in requirement_evidence().items()
            for component, key in gates.items()
            if component in domain_requirements()[domain].get("required", [])
            and f"`{component}` only when {key} is true" not in rendered
        ]
        assert missing == []


class TestRenderExtractionCaps:
    def test_should_group_caps_by_domain_when_rendering(self) -> None:
        food = next(
            line for line in render_extraction_caps().splitlines() if line.startswith("- food:")
        )

        assert food.startswith("- food: ingredients ≤ 30, steps ≤ 25")

    def test_should_not_number_object_paths_when_rendering(self) -> None:
        sport = next(
            line for line in render_extraction_caps().splitlines() if line.startswith("- sport:")
        )

        assert "formation (one object)" in sport
