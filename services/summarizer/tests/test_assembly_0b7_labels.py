"""Labels carry the shipped count (output-quality 0b.7 — R3, D7).

The planner writes "9 Core Arguments" before extraction, so the number was a guess
(13 of 16 stored labels lied). Post-processing now strips the planner's digits and
prefixes the count that ships, mirroring the FE rule in ``domain-meta.ts``
(``LEADING_COUNT``: 1–3 leading digits + whitespace + a letter; hyphenated and
4-digit leaders are names). Labels without a count claim are left verbatim.
"""

from __future__ import annotations

import pytest

from src.services.pipeline.assembly.core import (
    _post_process_tabs,
    shipped_count,
    true_count_label,
)


def _tab(label: str, component: str, props: dict) -> dict:
    return {"id": "t", "label": label, "emoji": "", "component": component, "props": props}


class TestTrueCountLabel:
    def test_should_replace_the_planners_count_with_the_shipped_one(self) -> None:
        assert true_count_label("9 Core Arguments", 24) == "24 Core Arguments"

    def test_should_drop_the_count_when_it_is_unknown(self) -> None:
        assert true_count_label("9 Core Arguments", None) == "Core Arguments"

    @pytest.mark.parametrize(
        "label",
        [
            "Key Moments",
            "7-Minute Workout",
            "2024 Review",
            "Test Yourself",
            "6-Step Software Factory",
        ],
    )
    def test_should_leave_a_label_without_a_count_claim_verbatim(self, label: str) -> None:
        assert true_count_label(label, 12) == label

    def test_should_count_a_label_in_another_script(self) -> None:
        assert true_count_label("5 נקודות", 7) == "7 נקודות"


class TestShippedCount:
    def test_should_count_the_components_list(self) -> None:
        assert shipped_count("spot_explorer", {"spots": [{}, {}, {}]}) == 3

    def test_should_sum_rows_pros_and_cons_for_a_comparison(self) -> None:
        props = {"comparisons": [{}, {}], "pros": ["a"], "cons": ["b", "c"]}

        assert shipped_count("comparison_radar", props) == 5

    def test_should_be_unknown_for_a_component_without_a_counted_list(self) -> None:
        assert shipped_count("display_section", {"content": "x"}) is None

    def test_should_fall_back_to_the_older_moment_track_shape(self) -> None:
        assert shipped_count("moment_track", {"entries": [{}, {}]}) == 2


class TestPostProcessedLabels:
    def test_should_write_the_count_that_ships_after_the_cap(self) -> None:
        spots = [{"name": f"S{i}", "description": "d"} for i in range(40)]
        tab = _tab("10 Numbers That Matter", "spot_explorer", {"spots": spots})

        _post_process_tabs([tab])

        assert tab["label"] == "25 Numbers That Matter"

    def test_should_strip_the_emoji_before_the_count(self) -> None:
        tab = _tab("🧠 9 Core Arguments", "concept_canvas", {"concepts": [{}, {}]})
        tab["emoji"] = "🧠"

        _post_process_tabs([tab])

        assert tab["label"] == "2 Core Arguments"

    def test_should_never_add_a_count_to_a_label_without_one(self) -> None:
        tab = _tab("Key Moments", "moment_track", {"items": [{"label": "a", "seconds": 1}]})

        _post_process_tabs([tab])

        assert tab["label"] == "Key Moments"

    def test_should_keep_a_hyphenated_leader(self) -> None:
        tab = _tab("6-Step Software Factory", "step_flow_canvas", {"steps": [{}] * 10})

        _post_process_tabs([tab])

        assert tab["label"] == "6-Step Software Factory"

    def test_should_be_idempotent(self) -> None:
        tab = _tab("7 Takeaways", "spot_explorer", {"spots": [{}] * 18})

        _post_process_tabs([tab])
        _post_process_tabs([tab])

        assert tab["label"] == "18 Takeaways"
