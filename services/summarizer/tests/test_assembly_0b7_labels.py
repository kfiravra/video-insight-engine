"""Labels carry the shipped count (output-quality 0b.7 — R3, D7).

The planner writes "9 Core Arguments" before extraction, so the number was a guess
(13 of 16 stored labels lied). Post-processing now strips the planner's digits and
prefixes the count that ships, mirroring the FE rule in ``domain-meta.ts``
(``LEADING_COUNT``: 1–3 leading digits + whitespace + a letter; hyphenated and
4-digit leaders are names; a unit after the number makes it a measure — phase-0
review W-A1). Labels without a count claim are left verbatim.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from src.services.pipeline.assembly.core import (
    _post_process_tabs,
    shipped_count,
    true_count_label,
)
from src.services.pipeline.assembly.labels import _LABEL_COUNT_KEYS, _MEASURE_UNITS
from src.shared_config.assembly_policy import measure_units

_DOMAIN_META = (
    Path(__file__).resolve().parent.parent.parent.parent
    / "apps"
    / "web"
    / "src"
    / "components"
    / "vie"
    / "cards"
    / "domain-meta.ts"
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

    @pytest.mark.parametrize(
        "label",
        [
            "١٢ خطوات",  # Arabic-Indic digits: JS ``\d`` is ASCII-only
            "3 ½ Cups",  # a number sign (No) is not a letter
            "12 Ⅻ Rules",  # a letter number (Nl) is not a letter either
            "12 ",
        ],
    )
    def test_should_mirror_the_fe_rule_exactly(self, label: str) -> None:
        assert true_count_label(label, 4) == label

    def test_should_take_any_run_of_whitespace_before_the_letter(self) -> None:
        assert true_count_label("12 \u00a0Steps", 4) == "4 Steps"


class TestMeasuresAreNoCounts:
    @pytest.mark.parametrize(
        "label",
        [
            "7 Days in Tokyo",
            "10 Minute Abs",
            "3 Course Meal",
            "30 SECONDS to Plank",
            "5 km Run",
            "100 Percent Juice",
            "2 Nights, 3 Cities",
        ],
    )
    def test_should_leave_a_measure_verbatim(self, label: str) -> None:
        assert true_count_label(label, 21) == label

    def test_should_read_the_whole_next_word_not_its_prefix(self) -> None:
        assert true_count_label("5 Daily Habits", 7) == "7 Daily Habits"

    def test_should_still_rewrite_a_count_over_the_shipped_items(self) -> None:
        tab = _tab("9 Core Arguments", "concept_canvas", {"concepts": [{}] * 24})

        _post_process_tabs([tab])

        assert tab["label"] == "24 Core Arguments"

    def test_should_keep_an_itinerarys_day_count_after_the_cap(self) -> None:
        tab = _tab("7 Days in Tokyo", "spot_explorer", {"spots": [{"name": "s"}] * 21})

        _post_process_tabs([tab])

        assert tab["label"] == "7 Days in Tokyo"


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

    def test_should_be_unknown_for_a_comparison_with_no_rows_pros_or_cons(self) -> None:
        assert shipped_count("comparison", {"comparisons": [], "pros": [], "cons": []}) is None

    def test_should_count_an_empty_list_as_zero(self) -> None:
        """Pinned as intended: ``inferItemCount`` returns 0 for an empty list."""
        assert shipped_count("spot_explorer", {"spots": []}) == 0

    def test_should_never_count_an_attachment_only_component(self) -> None:
        assert shipped_count("stat_banner", {"stats": [{}, {}]}) is None


class TestFrontendParity:
    """The count rule and the counted lists are the FE's (``domain-meta.ts``)."""

    def test_should_count_the_lists_the_hero_strip_counts(self) -> None:
        source = _DOMAIN_META.read_text()
        table = source.split("const COUNT_PROP_BY_COMPONENT", 1)[1].split("};", 1)[0]

        assert dict(re.findall(r"(\w+): '(\w+)'", table)) == _LABEL_COUNT_KEYS

    def test_should_mirror_the_fe_leading_count_rule(self) -> None:
        assert "LEADING_COUNT = /^\\d{1,3}\\s+(?=(\\p{L}+))/u;" in _DOMAIN_META.read_text()

    # output-quality 1.8: one list in domains.json, read by the server and the web.
    def test_should_read_the_one_measure_unit_list_of_the_registry(self) -> None:
        assert _MEASURE_UNITS == measure_units() and "days" in _MEASURE_UNITS

    def test_should_have_the_fe_read_the_registry_list_too(self) -> None:
        source = _DOMAIN_META.read_text()

        assert "import { MEASURE_UNITS } from '@vie/shared/config';" in source
        assert "const MEASURE_UNITS" not in source


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

    def test_should_drop_the_count_of_a_comparison_that_ships_nothing_countable(self) -> None:
        props = {"comparisons": [], "pros": [], "cons": [], "verdict": {"summary": "Buy"}}
        tab = _tab("6 Pros & Cons", "comparison", props)

        _post_process_tabs([tab])

        assert tab["label"] == "Pros & Cons"

    def test_should_write_zero_for_an_empty_counted_list(self) -> None:
        tab = _tab("5 Spots", "spot_explorer", {"spots": []})

        _post_process_tabs([tab])

        assert tab["label"] == "0 Spots"

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
