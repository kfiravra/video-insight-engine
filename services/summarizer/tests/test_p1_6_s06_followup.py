"""p1.6 follow-up — the S06 iteration run (lasagna, 17 min).

An ingredient amount that is no quantity ("liberally", "a little layer", "to
taste") is how the chef uses the ingredient: the checklist row is the name and
the amount leads the note — never "liberally salt". A bare number keeps its
unit ("1 cup whole milk"). The food schema asks for a step's time only as the
chef states it for that step (the invented "rest about 10 minutes"). The tabs
assembly adds itself carry viewer-outcome goals (task 1.10's rule).
"""

from __future__ import annotations

import pytest

from src.services.pipeline.assembly import assemble_response
from src.services.pipeline.assembly.assemblers_primary import assemble_checklist
from src.services.pipeline.assembly.core import build_fallback_candidates
from src.services.pipeline.assembly.ingredients import ingredient_row
from src.services.pipeline.extraction_prompt import load_schema_text
from src.services.pipeline.plan_prompt import goal_rule_violation

# The S06 run's own ingredient rows.
_S06 = [
    {
        "name": "salt",
        "amount": 0,
        "unit": "",
        "displayAmount": "liberally",
        "notes": "used on the meat, plus extra on the vegetables",
    },
    {
        "name": "oil",
        "amount": 0,
        "unit": "",
        "displayAmount": "a little layer",
        "notes": "in the baking dish so nothing sticks",
    },
    {
        "name": "salt (for pasta water)",
        "amount": 0,
        "unit": "",
        "displayAmount": "to taste",
        "notes": "",
    },
    {"name": "whole milk", "amount": 1, "unit": "cup", "displayAmount": "1", "notes": ""},
    {
        "name": "beef and pork",
        "amount": 0,
        "unit": "",
        "displayAmount": "amount not stated",
        "notes": "browned in two batches",
    },
    {
        "name": "unsalted butter",
        "amount": 6,
        "unit": "tablespoons",
        "displayAmount": "",
        "notes": "melted",
    },
]


def _rows() -> list[dict]:
    props = assemble_checklist({}, _S06, {}, None)
    assert props is not None
    return props["items"]


class TestIngredientRows:
    @pytest.mark.parametrize(
        ("index", "label"),
        [(0, "salt"), (1, "oil"), (2, "salt (for pasta water)"), (4, "beef and pork")],
    )
    def test_should_label_the_row_by_its_name_when_the_amount_is_no_quantity(
        self, index: int, label: str
    ) -> None:
        assert _rows()[index]["label"] == label

    def test_should_lead_the_note_with_how_the_chef_uses_it(self) -> None:
        assert _rows()[0]["note"] == "liberally, used on the meat, plus extra on the vegetables"

    def test_should_keep_the_unit_beside_a_bare_number(self) -> None:
        assert _rows()[3]["label"] == "1 cup whole milk"

    @pytest.mark.parametrize(
        ("amount", "unit", "name", "label"),
        [
            ("1 1/2", "cups", "flour", "1 1/2 cups flour"),
            ("3-4", "cloves", "garlic", "3-4 cloves garlic"),
            ("1/2", "cup", "sugar", "1/2 cup sugar"),
            ("1½", "cups", "stock", "1½ cups stock"),
            ("2 to 3", "tablespoons", "butter", "2 to 3 tablespoons butter"),
        ],
    )
    def test_should_keep_the_unit_beside_a_mixed_number_range_or_fraction(
        self, amount: str, unit: str, name: str, label: str
    ) -> None:
        item = {"name": name, "amount": 0, "unit": unit, "displayAmount": amount, "notes": ""}

        assert ingredient_row(item)["label"] == label

    def test_should_build_the_amount_from_its_number_and_unit_when_none_is_displayed(self) -> None:
        assert _rows()[5]["label"] == "6 tablespoons unsalted butter"

    def test_should_never_print_amount_not_stated(self) -> None:
        assert not any("not stated" in row["label"] for row in _rows())


class TestFoodSchemaTimes:
    def test_should_ask_for_a_steps_time_only_as_the_chef_states_it(self) -> None:
        food = load_schema_text("food")

        assert "only when the chef states it FOR THAT STEP" in food
        assert "A bake time is never a rest time" in food

    def test_should_no_longer_ask_for_a_number_where_the_chef_gave_a_cue(self) -> None:
        assert 'not "until done"' not in load_schema_text("food")


class TestInjectedGoals:
    def test_should_give_the_fallback_tabs_viewer_outcome_goals(self) -> None:
        synthesis = {"masterSummary": "m", "keyTakeaways": ["a", "b"], "tldr": "t"}

        goals = [tab["goal"] for tab in build_fallback_candidates(synthesis, set(), set())]

        assert goals and all(goal_rule_violation(goal) is None for goal in goals)

    def test_should_give_the_injected_overview_a_viewer_outcome_goal(self) -> None:
        triage = {"primaryTag": "learning", "contentTags": ["learning"], "tabs": []}

        result = assemble_response(triage, {"learning": {}}, None, {"tldr": "t"})
        overview = next(t for t in result["tabs"] if t["id"] == "overview")

        assert goal_rule_violation(overview["goal"]) is None
