"""output-quality 0b.9 — food/project steps never promote to the canvas (D8: S06
ships step_player, Cooking mode); a comparison keeps one name per competitor,
derives no pros/cons from `winner`, and becomes a radar only on numeric axes
(R10, R12; component-audit §2 #3, #11)."""

from src.services.pipeline.assembly import (
    RADAR_AXIS_THRESHOLD,
    STEP_FLOW_THRESHOLD,
    assemble_comparison,
    promote_component,
)
from src.services.pipeline.assembly.promotion import STEP_LIST_DOMAINS, is_numeric_axis


def _steps(n: int) -> list[dict]:
    return [{"number": i + 1, "instruction": f"Step {i}"} for i in range(n)]


def _numeric_rows(n: int) -> list[dict]:
    return [
        {"feature": f"Spec {i}", "thisProduct": f"{10 + i} mAh", "competitor": f"{20 + i} mAh"}
        for i in range(n)
    ]


S07_ROWS = [
    {
        "feature": "Noise cancellation (ranking)",
        "thisProduct": "AirPods Pro 2 — placed on the same level",
        "competitor": "Sony WF-1000XM4 (said as 'Sony Mark 4')",
        "competitorName": "Sony WF-1000XM4",
        "winner": "tie",
    },
    {
        "feature": "Noise cancellation (ranking)",
        "thisProduct": "AirPods Pro 2 — placed right below",
        "competitor": "ranked just above the AirPods Pro 2",
        "competitorName": "Bose QuietComfort Earbuds II",
        "winner": "right",
    },
    {
        "feature": "Transparency mode",
        "thisProduct": "AirPods Pro 2 — 'on a level by itself'",
        "competitor": "Boses, Samsungs and Pixel Buds — transparency is worse",
        "competitorName": "Bose, Samsung Buds, Pixel Buds",
        "winner": "left",
    },
    {
        "feature": "Overall competition",
        "thisProduct": "AirPods Pro 2 — 'significant upgrade'",
        "competitor": "Samsung's buds are 'really good'",
        "competitorName": "Samsung Buds, Pixel Buds",
    },
]


# ─── steps: list, never canvas, for food / project (D8) ───


class TestStepsStayAListForFollowAlongDomains:
    def test_should_not_promote_a_long_food_procedure_to_the_canvas(self):
        props = {"steps": _steps(STEP_FLOW_THRESHOLD * 2)}

        component, out = promote_component("step_player", props, None, {}, "food")

        assert (component, out) == ("step_player", props)

    def test_should_not_promote_a_long_project_build_to_the_canvas(self):
        component, _ = promote_component(
            "step_player", {"steps": _steps(STEP_FLOW_THRESHOLD)}, None, {}, "project"
        )

        assert component == "step_player"

    def test_should_still_promote_a_long_procedure_outside_those_domains(self):
        component, _ = promote_component(
            "step_player", {"steps": _steps(STEP_FLOW_THRESHOLD)}, None, {}, "tech"
        )

        assert component == "step_flow_canvas"

    def test_should_turn_a_planned_food_canvas_back_into_a_step_player(self):
        # S06: the planner picked step_flow_canvas outright for 16 lasagna steps.
        props = {"steps": _steps(16)}

        component, out = promote_component("step_flow_canvas", props, None, {}, "food")

        assert (component, out) == ("step_player", props)

    def test_should_keep_a_planned_canvas_outside_those_domains(self):
        component, _ = promote_component(
            "step_flow_canvas", {"steps": _steps(16)}, None, {}, "science"
        )

        assert component == "step_flow_canvas"

    def test_should_list_exactly_the_follow_along_domains(self):
        assert STEP_LIST_DOMAINS == frozenset({"food", "project"})


# ─── comparison: one name per competitor, pros/cons only from the review ───


class TestComparisonKeepsEveryCompetitor:
    def test_should_head_the_right_column_with_every_named_competitor(self):
        out = assemble_comparison({}, S07_ROWS, {"review": {"product": "AirPods Pro 2"}}, None)

        assert out["rightLabel"] == (
            "Sony WF-1000XM4 · Bose QuietComfort Earbuds II · Bose, Samsung Buds, Pixel Buds"
            " · Samsung Buds, Pixel Buds"
        )

    def test_should_keep_the_competitor_name_on_every_row(self):
        out = assemble_comparison({}, S07_ROWS, {}, None)

        assert [r["competitorName"] for r in out["comparisons"]] == [
            r["competitorName"] for r in S07_ROWS
        ]

    def test_should_name_the_competitor_in_a_cell_that_does_not_mention_it(self):
        out = assemble_comparison({}, S07_ROWS, {}, None)

        assert out["comparisons"][1]["competitor"] == (
            "Bose QuietComfort Earbuds II — ranked just above the AirPods Pro 2"
        )

    def test_should_leave_a_cell_that_already_names_its_competitor(self):
        out = assemble_comparison({}, S07_ROWS, {}, None)

        assert out["comparisons"][2]["competitor"] == S07_ROWS[2]["competitor"]

    def test_should_not_prefix_cells_when_there_is_a_single_competitor(self):
        rows = [
            {"feature": "Battery", "thisProduct": "6 h", "competitor": "8 h"},
            {"feature": "Price", "thisProduct": "$249", "competitor": "$279"},
        ]
        for row in rows:
            row["competitorName"] = "X1"

        out = assemble_comparison({}, rows, {}, None)

        assert out["rightLabel"] == "X1"
        assert out["comparisons"][0]["competitor"] == "8 h"


class TestProsConsNeverDerivedFromWinner:
    def test_should_ship_empty_pros_cons_for_a_winner_tagged_row_list(self):
        out = assemble_comparison({}, S07_ROWS, {}, None)

        assert (out["pros"], out["cons"]) == ([], [])

    def test_should_not_derive_pros_cons_from_winners_on_the_review_dict(self):
        data = {"pros": [], "cons": [], "comparisons": _numeric_rows(3)}
        for i, row in enumerate(data["comparisons"]):
            row["winner"] = "left" if i else "right"

        out = assemble_comparison({}, data, {}, None)

        assert (out["pros"], out["cons"]) == ([], [])

    def test_should_keep_the_review_own_pros_and_cons(self):
        data = {"pros": ["Great sound"], "cons": ["No lossless"], "comparisons": _numeric_rows(2)}

        out = assemble_comparison({}, data, {}, None)

        assert (out["pros"], out["cons"]) == (["Great sound"], ["No lossless"])

    def test_should_drop_a_lone_winner_row_without_pros_cons(self):
        data = {"pros": [], "cons": [], "comparisons": _numeric_rows(1)}
        data["comparisons"][0]["winner"] = "left"

        assert assemble_comparison({}, data, {}, None) is None


# ─── radar only with numeric axes ───


class TestRadarOnlyWithNumericAxes:
    def test_should_promote_to_radar_with_enough_numeric_rows(self):
        props = {"comparisons": _numeric_rows(RADAR_AXIS_THRESHOLD), "pros": [], "cons": []}

        component, _ = promote_component("comparison", props, None, {}, "review")

        assert component == "comparison_radar"

    def test_should_not_promote_prose_rows_however_many(self):
        props = {"comparisons": S07_ROWS * 2, "pros": [], "cons": []}

        component, _ = promote_component("comparison", props, None, {}, "review")

        assert component == "comparison"

    def test_should_count_only_the_numeric_rows_toward_the_threshold(self):
        rows = _numeric_rows(RADAR_AXIS_THRESHOLD - 1) + S07_ROWS
        props = {"comparisons": rows, "pros": [], "cons": []}

        component, _ = promote_component("comparison", props, None, {}, "review")

        assert component == "comparison"

    def test_should_read_units_and_currency_as_numbers(self):
        row = {"feature": "Battery", "thisProduct": "4,422 mAh", "competitor": "$2,211.50"}

        assert is_numeric_axis(row) is True

    def test_should_read_bare_numbers_as_numbers(self):
        assert is_numeric_axis({"feature": "Camera", "thisProduct": 48, "competitor": 50.5})

    def test_should_not_read_a_chip_name_as_a_number(self):
        row = {"feature": "Chip", "thisProduct": "H2", "competitor": "H1"}

        assert is_numeric_axis(row) is False

    def test_should_not_read_a_sentence_with_a_digit_as_a_number(self):
        assert is_numeric_axis(S07_ROWS[0]) is False

    def test_should_not_read_booleans_as_numbers(self):
        row = {"feature": "HDR", "thisProduct": True, "competitor": False}

        assert is_numeric_axis(row) is False
