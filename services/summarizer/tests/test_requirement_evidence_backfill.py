"""A required component the plan's evidence rules out is never backfilled or demoted into.

pipeline-1min 1d.7 (D15/C19): a food vlog with no recipe must not get a
backfilled ingredient checklist or step player, nor have a spot list degrade
into a checklist. The skip is recorded in droppedTabs but not counted as a drop.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from src.services.pipeline.assembly import assemble_response
from src.services.pipeline.assembly import core as assembly_core
from src.services.pipeline.assembly.core import (
    REQUIREMENT_EVIDENCE_FALSE,
    _validate_domain_requirements,
    count_dropped_tabs,
    demotion_blocked,
)
from src.services.pipeline.assembly.promotion import demote_component
from src.services.pipeline.assembly.registry import ASSEMBLER_REGISTRY
from src.shared_config.domain_config import requirement_evidence

_GATES = [
    (domain, component, key)
    for domain, gates in sorted(requirement_evidence().items())
    for component, key in sorted(gates.items())
]
_FOOD = json.loads(
    (Path(__file__).parent / "fixtures" / "llm_responses" / "food.json").read_text()
)["extraction_response"]
_NO_RECIPE = {"has_ingredients": False, "has_steps": False}


def _backfill_attempts(
    domain: str, evidence: dict[str, bool] | None, tabs: list[dict] | None = None
) -> tuple[list[frozenset[str]], list[dict]]:
    """(accepted-component sets the backfill was asked for, droppedTabs entries)."""
    dropped: list[dict] = []
    with patch.object(assembly_core, "_backfill_required_component", return_value=False) as spy:
        _validate_domain_requirements(tabs or [], domain, dropped_sink=dropped, evidence=evidence)
    return [call.args[2] for call in spy.call_args_list], dropped


def _food_vlog(evidence: dict[str, bool] | None) -> dict[str, Any]:
    """A tasting vlog's plan (one tips grid) over an extraction that does hold a recipe."""
    triage = {
        "contentTags": ["food"],
        "primaryTag": "food",
        "contentFormat": "vlog",
        "evidence": evidence,
        "tabs": [
            {"id": "must_order", "label": "Must order", "component": "info_grid",
             "dataSource": "food.tips", "goal": "What to order"},
        ],
    }  # fmt: skip
    extraction = {
        "food": {
            "ingredients": _FOOD["ingredients"],
            "steps": _FOOD["steps"],
            "tips": _FOOD["tips"][:2],
        }
    }
    return assemble_response(triage, extraction, None, None)


class TestBackfillGate:
    @pytest.mark.parametrize(("domain", "component", "key"), _GATES)
    def test_should_not_backfill_a_requirement_when_its_evidence_is_false(
        self, domain: str, component: str, key: str
    ) -> None:
        attempts, dropped = _backfill_attempts(domain, {key: False})

        assert not any(component in accepted for accepted in attempts)
        assert {
            "id": "",
            "component": component,
            "dataSource": "",
            "reason": REQUIREMENT_EVIDENCE_FALSE,
            "detail": f"{key}=false",
        } in dropped

    @pytest.mark.parametrize(("domain", "component", "key"), _GATES)
    def test_should_backfill_a_requirement_when_its_evidence_is_true(
        self, domain: str, component: str, key: str
    ) -> None:
        attempts, dropped = _backfill_attempts(domain, {key: True})

        assert any(component in accepted for accepted in attempts)
        assert dropped == []

    @pytest.mark.parametrize(("domain", "component", "key"), _GATES)
    def test_should_backfill_a_requirement_when_the_plan_gave_no_evidence(
        self, domain: str, component: str, key: str
    ) -> None:
        attempts, _ = _backfill_attempts(domain, None)

        assert any(component in accepted for accepted in attempts)

    def test_should_keep_a_planned_tab_and_record_nothing_when_evidence_is_false(self) -> None:
        planned = [{"id": "ingredients", "component": "checklist", "goal": "g", "label": "l"}]

        _, dropped = _backfill_attempts("food", {"has_ingredients": False}, tabs=planned)

        assert [t["component"] for t in planned] == ["checklist"]
        assert all(entry["component"] != "checklist" for entry in dropped)


class TestDemotionBlock:
    def test_should_block_the_ruled_out_component_and_its_promotion_twin(self) -> None:
        assert demotion_blocked("food", _NO_RECIPE) == {
            "checklist",
            "step_player",
            "step_flow_canvas",
        }

    def test_should_block_nothing_without_evidence(self) -> None:
        assert demotion_blocked("food", None) == frozenset()

    def test_should_skip_a_blocked_rung_when_demoting(self) -> None:
        data = ["Order the poutine", "Get the hot dog all-dressed"]

        unblocked = demote_component("info_grid", {"id": "t"}, data, {}, None)
        blocked = demote_component(
            "info_grid", {"id": "t"}, data, {}, None, blocked=frozenset({"checklist"})
        )

        assert unblocked is not None and unblocked[0] == "checklist"
        assert blocked is not None and blocked[0] == "display_section"


class TestFoodVlogAssembly:
    def test_should_ship_no_recipe_component_when_the_plan_says_there_is_no_recipe(self) -> None:
        result = _food_vlog(_NO_RECIPE)

        components = {t["component"] for t in result["tabs"]}
        assert not components & {"checklist", "step_player", "step_flow_canvas"}

    def test_should_record_the_skips_without_counting_them_as_drops(self) -> None:
        result = _food_vlog(_NO_RECIPE)

        skipped = [d for d in result["dropped"] if d["reason"] == REQUIREMENT_EVIDENCE_FALSE]
        assert {d["component"] for d in skipped} == {"checklist", "step_player"}
        assert count_dropped_tabs(result["dropped"]) == len(result["dropped"]) - len(skipped)

    def test_should_still_add_recipe_components_when_the_plan_gave_no_evidence(self) -> None:
        components = {t["component"] for t in _food_vlog(None)["tabs"]}

        assert "checklist" in components
        assert components & {"step_player", "step_flow_canvas"}

    @pytest.mark.parametrize(
        ("evidence", "expected"),
        [(None, "checklist"), (_NO_RECIPE, "display_section")],
    )
    def test_should_degrade_a_failing_tab_past_a_ruled_out_checklist(
        self, evidence: dict[str, bool] | None, expected: str
    ) -> None:
        # The tips grid fails to assemble (its assembler stubbed to None), so it
        # walks the demote ladder info_grid → checklist → display_section.
        with patch.dict(ASSEMBLER_REGISTRY, {"info_grid": lambda *_args: None}):
            tabs = _food_vlog(evidence)["tabs"]

        must_order = next(t for t in tabs if t["id"] == "must_order")
        assert (must_order["component"], must_order["degradedFrom"]) == (expected, "info_grid")


class TestCountDroppedTabs:
    def test_should_count_every_drop_except_evidence_skips(self) -> None:
        dropped = [
            {"reason": "domain_forbidden"},
            {"reason": REQUIREMENT_EVIDENCE_FALSE},
            {"reason": "assembler_returned_none"},
        ]

        assert count_dropped_tabs(dropped) == 2
