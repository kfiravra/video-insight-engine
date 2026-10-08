"""Tests for promotion-aware golden assertions in ``scripts/_eval_assertions.py``.

Assembly promotes some planned components to a richer sibling
(``COMPONENT_PROMOTIONS`` in ``assembly/promotion.py``). The eval reads that
map from source and credits the promoted tab for ``requiredComponents`` and
``minItems``; ``forbiddenComponents`` stays an exact match.
"""

from __future__ import annotations

import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import yaml

from src.services.pipeline.assembly.promotion import COMPONENT_PROMOTIONS

_REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_REPO_ROOT / "scripts"))

import _eval_assertions  # noqa: E402
from _eval_assertions import (  # noqa: E402
    PROMOTION_SOURCE,
    AssertionResult,
    TraceSignals,
    evaluate_assertions,
    promotion_targets,
    read_promotion_map,
)
from _eval_schema import parse_assertions  # noqa: E402

_DATASET = _REPO_ROOT / "dev" / "golden-dataset" / "videos.yaml"


def _check(assertion: dict[str, Any], actual: dict[str, Any]) -> AssertionResult:
    record = {
        "id": "vid",
        "url": "https://www.youtube.com/watch?v=abcdefghijk",
        "domain": "food",
        "format": "tutorial",
        "language": "en",
        "expectedTabs": [],
        "requiredComponents": [],
        "keyContent": [],
        "assertions": [assertion],
    }
    [result] = evaluate_assertions(parse_assertions(record), actual, TraceSignals())
    return result


def _tab(component: str, **props: Any) -> dict[str, Any]:
    return {"id": component, "component": component, "props": props}


def _steps(n: int) -> list[dict[str, Any]]:
    return [{"number": i + 1, "instruction": f"Step {i}"} for i in range(n)]


_REQUIRE_STEPS = {"type": "requiredComponents", "components": ["step_player", "checklist"]}
_MIN_STEPS = {"type": "minItems", "component": "step_player", "field": "steps", "min": 4}


@pytest.fixture
def fresh_promotion_cache() -> Iterator[None]:
    promotion_targets.cache_clear()
    yield
    promotion_targets.cache_clear()


# ─── The promotion map ─────────────────────────────────────────────────
class TestPromotionMap:
    def test_should_parse_the_map_the_assembler_uses_when_reading_promotion_py(self) -> None:
        assert read_promotion_map(PROMOTION_SOURCE) == COMPONENT_PROMOTIONS

    def test_should_raise_when_the_source_has_no_promotion_map(self, tmp_path: Path) -> None:
        source = tmp_path / "promotion.py"
        source.write_text("OTHER: dict[str, str] = {}\n", encoding="utf-8")
        with pytest.raises(LookupError):
            read_promotion_map(source)

    @pytest.mark.usefixtures("fresh_promotion_cache")
    def test_should_match_exactly_when_the_promotion_map_is_unreadable(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setattr(_eval_assertions, "PROMOTION_SOURCE", tmp_path / "missing.py")
        actual = {"tabs": [_tab("checklist"), _tab("step_flow_canvas", steps=_steps(9))]}
        assert _check(_REQUIRE_STEPS, actual).passed is False


# ─── requiredComponents ────────────────────────────────────────────────
class TestRequiredComponents:
    def test_should_pass_when_the_required_component_was_promoted(self) -> None:
        actual = {"tabs": [_tab("checklist"), _tab("step_flow_canvas")]}
        assert _check(_REQUIRE_STEPS, actual).passed is True

    def test_should_name_the_promotion_when_a_promoted_tab_satisfies_the_check(self) -> None:
        actual = {"tabs": [_tab("checklist"), _tab("step_flow_canvas")]}
        detail = _check(_REQUIRE_STEPS, actual).detail
        assert detail == "all present; step_player (as step_flow_canvas)"

    def test_should_fail_when_only_an_unrelated_promotion_target_is_present(self) -> None:
        actual = {"tabs": [_tab("checklist"), _tab("concept_canvas")]}
        result = _check(_REQUIRE_STEPS, actual)
        assert (result.passed, result.detail) == (False, "missing: ['step_player']")

    def test_should_fail_when_the_target_is_required_and_only_the_source_rendered(self) -> None:
        check = {"type": "requiredComponents", "components": ["step_flow_canvas"]}
        assert _check(check, {"tabs": [_tab("step_player")]}).passed is False


# ─── minItems ──────────────────────────────────────────────────────────
class TestMinItems:
    def test_should_pass_when_the_promoted_tab_holds_enough_items(self) -> None:
        actual = {"tabs": [_tab("step_flow_canvas", steps=_steps(9))]}
        result = _check(_MIN_STEPS, actual)
        assert (result.passed, result.detail) == (
            True,
            "step_player (as step_flow_canvas) items=[9] min=4",
        )

    def test_should_fail_when_only_an_unrelated_component_is_present(self) -> None:
        actual = {"tabs": [_tab("checklist", steps=_steps(9))]}
        result = _check(_MIN_STEPS, actual)
        assert (result.passed, result.detail) == (False, "no step_player tab")

    def test_should_count_the_renamed_list_when_the_promotion_reshaped_props(self) -> None:
        check = {"type": "minItems", "component": "info_grid", "field": "items", "min": 3}
        spots = [{"name": f"Spot {i}", "description": "long"} for i in range(4)]
        assert _check(check, {"tabs": [_tab("spot_explorer", spots=spots)]}).passed is True


# ─── forbiddenComponents ───────────────────────────────────────────────
class TestForbiddenComponents:
    def test_should_ignore_a_promotion_target_when_its_source_is_forbidden(self) -> None:
        check = {"type": "forbiddenComponents", "components": ["step_player"]}
        assert _check(check, {"tabs": [_tab("step_flow_canvas")]}).passed is True


# ─── Regression: food-recipe-story-intro (quick golden 2026-10-08) ─────
def test_should_pass_the_story_intro_step_checks_when_step_player_renders_as_canvas() -> None:
    """The plan's 9-step step_player tab renders as step_flow_canvas (>= 8 steps)."""
    videos = yaml.safe_load(_DATASET.read_text(encoding="utf-8"))["videos"]
    record = next(v for v in videos if v["id"] == "food-recipe-story-intro")
    checks = [a for a in parse_assertions(record) if a.type in {"requiredComponents", "minItems"}]
    actual = {
        "tabs": [
            _tab("overview"),
            _tab("checklist", items=[{"text": f"Ingredient {i}"} for i in range(10)]),
            _tab("step_flow_canvas", steps=_steps(9)),
        ]
    }
    results = evaluate_assertions(checks, actual, TraceSignals())
    assert [r.passed for r in results] == [True, True, True]
