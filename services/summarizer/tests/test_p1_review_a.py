"""Phase-1 review fixes, lane A — the prompts (review-p1 R1/R3/R4/R5/R6/R8/R9/R11/R13).

Each test pins one review finding against the prompt text on disk: the tier
probe's podcast rule names the domain; the plan prompt's sizing row for key
ideas is a floor, step_flow_canvas may open when the steps are the answer, one
compact fitness example is back, no example plans a standalone info_grid, the
routing matrix and comparison routing send text to spot_explorer, the
concept_canvas threshold is stated once (the registry's density gate); the
learning extraction example's moments are claims; the group rules carry the
cap tie-break and one D13 wording shared with the quality rules; the quiz
prompt copies a stamp only from a sentence-level item; memory's example
carries the takeaway count its length sets; news, food and fitness skeletons
hold no placeholder a model could echo. The LLM is never called.
"""

from __future__ import annotations

import json
import re

import pytest

from src.services.pipeline import enrichment as enrichment_mod
from src.services.pipeline import tier_probe
from src.services.pipeline.extraction_group_prompt import GROUP_RULES_PATH
from src.services.pipeline.extraction_prompt import load_schema_text
from src.services.pipeline.memory import PROMPT_PATH as MEMORY_PROMPT_PATH
from src.services.pipeline.memory import takeaway_count
from src.services.pipeline.plan_prompt import (
    COMPONENT_TOOLKIT_PATH,
    PROMPT_PATH,
    PlanVideo,
    goal_rule_violation,
    render_plan_parts,
)
from src.services.pipeline.prompt_builder import PROMPTS_DIR, load_prompt_text
from src.services.pipeline.schema_fields import slice_skeleton
from src.shared_config.domain_config import density_gates

_PLAN = PROMPT_PATH.read_text(encoding="utf-8")
_TOOLKIT = COMPONENT_TOOLKIT_PATH.read_text(encoding="utf-8")
_GROUP_RULES = load_prompt_text(GROUP_RULES_PATH)
_QUALITY_RULES = load_prompt_text(PROMPTS_DIR / "quality_rules.txt")
_QUIZ = load_prompt_text(enrichment_mod.PROMPT_PATH)
_MEMORY = MEMORY_PROMPT_PATH.read_text(encoding="utf-8")
_LEARNING_EXAMPLE = (PROMPTS_DIR / "examples" / "learning.txt").read_text(encoding="utf-8")
_D13_RULE = (
    "Never invent a number: a duration, rest time, quantity, count or score the speaker did "
    'not state stays null; a text field keeps the words as said ("give it a sec" stays '
    '"give it a sec", never "rest 10 minutes").'
)


def _block(text: str, tag: str) -> str:
    """The body of the ``<tag>`` block — the tag on its own line, not a prose mention."""
    start = text.rindex(f"<{tag}>\n") + len(tag) + 3
    return text[start : text.index(f"</{tag}>", start)]


def _examples() -> dict[str, dict]:
    pairs = re.findall(r"<example>\n([^\n]*)\n(.*?)\n</example>", _PLAN, flags=re.DOTALL)
    return {header: json.loads(body) for header, body in pairs}


def _rendered_plan() -> str:
    """The static half of the plan prompt: the template, the toolkit and the registry tables."""
    return render_plan_parts(PlanVideo("t", "c", "d", 600, "learning", None, "[0:00] hi")).static


def _json_after(text: str, marker: str) -> dict:
    """The JSON object that follows ``marker`` in a prompt file."""
    start = text.index("{", text.index(marker))
    depth = 0
    for index, char in enumerate(text[start:], start):
        depth += (char == "{") - (char == "}")
        if depth == 0:
            return json.loads(text[start : index + 1])
    raise AssertionError(f"unterminated JSON after {marker!r}")


_EXAMPLES = _examples()
_FITNESS = next(ex for ex in _EXAMPLES.values() if ex["primaryTag"] == "fitness")


# ─── R1: tier probe ──────────────────────────────────────────────────────


class TestTierProbePodcastRule:
    def test_should_state_the_podcast_rule_as_a_domain_rule_when_the_prompt_is_read(
        self,
    ) -> None:
        guidance = _block(
            tier_probe.PROMPT_PATH.read_text(encoding="utf-8"), "classification_guidance"
        )

        assert 'The "podcast" DOMAIN (formats interview or podcast)' in guidance
        assert '"podcast" is a conversation/interview' not in guidance

    def test_should_not_repeat_the_solo_monologue_sentence_beside_the_format_lines(self) -> None:
        guidance = _block(
            tier_probe.PROMPT_PATH.read_text(encoding="utf-8"), "classification_guidance"
        )

        assert "Solo monologue" not in guidance


# ─── R3: plan prompt ─────────────────────────────────────────────────────


class TestPlanSizingFloor:
    def test_should_make_the_key_ideas_row_a_floor_when_sizing(self) -> None:
        sizing = _block(_PLAN, "sizing")

        assert "the table is a floor" in sizing and "every distinct claim you" in sizing

    def test_should_exempt_key_ideas_from_how_to_choose_when_a_list_exceeds_its_cap(self) -> None:
        sizing = _block(_PLAN, "sizing")

        assert "other than key ideas" in sizing and "never sampled" in sizing


class TestNeverFirst:
    def test_should_allow_step_flow_canvas_first_when_the_steps_are_the_answer(self) -> None:
        instructions = " ".join(_block(_PLAN, "instructions").split())
        never_first = instructions[instructions.index("Never video_filmstrip") :]
        never_first = never_first[: never_first.index("never an overview")]

        assert "step_flow_canvas when the steps are the answer" in instructions
        assert "step_flow_canvas" not in never_first

    def test_should_say_the_same_in_the_toolkit_anti_patterns(self) -> None:
        anti = " ".join(_TOOLKIT[_TOOLKIT.index("ANTI-PATTERNS") :].split())

        assert "step_flow_canvas opens first only when the steps are the answer" in anti
        assert "info_grid as the first tab" in anti


class TestFitnessExample:
    def test_should_ship_a_fitness_example_opening_on_workout_room(self) -> None:
        first = _FITNESS["tabs"][0]

        assert (first["component"], first["dataSource"]) == ("workout_room", "fitness.exercises")

    def test_should_plan_no_info_grid_and_no_moments_for_the_workout(self) -> None:
        components = {tab["component"] for tab in _FITNESS["tabs"]}

        assert 3 <= len(_FITNESS["tabs"]) <= 6
        assert not components & {"info_grid", "moment_track"}

    def test_should_name_a_viewer_outcome_in_every_fitness_goal(self) -> None:
        assert [goal_rule_violation(tab["goal"]) for tab in _FITNESS["tabs"]] == [None] * len(
            _FITNESS["tabs"]
        )


class TestNoStandaloneInfoGrid:
    @pytest.mark.parametrize("header", list(_EXAMPLES))
    def test_should_plan_no_info_grid_tab_in_any_example(self, header: str) -> None:
        assert [t["id"] for t in _EXAMPLES[header]["tabs"] if t["component"] == "info_grid"] == []

    def test_should_send_the_gaming_set_facts_to_spot_explorer(self) -> None:
        gaming = next(ex for ex in _EXAMPLES.values() if ex["primaryTag"] == "gaming")
        facts = next(tab for tab in gaming["tabs"] if tab["dataSource"] == "gaming.loadout")

        assert facts["component"] == "spot_explorer"


class TestTextRouting:
    def test_should_route_no_learn_cell_to_info_grid_in_the_matrix(self) -> None:
        rows = [
            line for line in _block(_PLAN, "routing_matrix").splitlines() if line.startswith("│ ")
        ]
        learn = [line.split("│")[2] for line in rows[1:]]

        assert learn and all("info_grid" not in cell for cell in learn)

    def test_should_send_unweighed_concepts_to_spot_explorer_when_routing_comparisons(self) -> None:
        routing = _block(_PLAN, "comparison_routing")

        assert "info_grid (browsable cards)" not in routing
        assert "spot_explorer (a claim, its why and a timestamp per item)" in routing

    def test_should_keep_lookup_cells_inside_the_tab_that_answers(self) -> None:
        routing = _block(_PLAN, "comparison_routing")

        assert "inside the tab that answers them" in routing
        assert "never in a standalone info_grid" in routing


class TestConceptCanvasThreshold:
    def test_should_state_the_concept_minimum_once_from_the_registry_when_rendered(self) -> None:
        rendered = _rendered_plan()
        minimum = density_gates()["concept_canvas"]["min"]

        assert re.search(r"\d\+ concepts", rendered) is None
        assert f"| concept_canvas | {minimum} |" in rendered

    def test_should_point_the_spoken_word_rule_at_the_density_table(self) -> None:
        assert "the toolkit's density table sets the minimum" in _block(_PLAN, "spoken_word")


class TestMomentPacing:
    def test_should_pace_the_rsc_example_one_beat_per_four_minutes(self) -> None:
        rsc = next(ex for ex in _EXAMPLES.values() if ex["primaryTag"] == "tech")
        moments = next(tab for tab in rsc["tabs"] if tab["component"] == "moment_track")

        assert "one beat per four minutes" in moments["brief"]["what"]


# ─── R4: the learning extraction example ─────────────────────────────────


class TestLearningExampleMoments:
    def _moments(self) -> list[dict]:
        return _json_after(_LEARNING_EXAMPLE, 'Video: "How Memory')["timestamps"]

    def test_should_shape_every_example_moment_as_a_claim_with_a_description(self) -> None:
        moments = self._moments()

        assert all(m["description"] and "quote" in m for m in moments)
        assert not any(m["label"].startswith(("Introduction", "Summary")) for m in moments)

    def test_should_carry_one_verbatim_quote_on_an_example_moment(self) -> None:
        quotes = [m["quote"] for m in self._moments() if m["quote"]]

        assert quotes == [
            "A fact with one hook can fall off the wall — a fact with five hooks stays forever."
        ]


# ─── R5: group rules and quality rules ───────────────────────────────────


class TestGroupRules:
    def test_should_break_the_tie_by_the_speakers_weight_when_claims_exceed_the_cap(self) -> None:
        argument_line = _GROUP_RULES.splitlines()[1]

        assert "keep the ones the speaker weighs most, the close included" in argument_line

    def test_should_share_one_d13_wording_between_the_group_and_quality_rules(self) -> None:
        # By content, not index: p4.6d put the why rule before it.
        d13_line = next(ln for ln in _GROUP_RULES.splitlines() if ln.startswith("Never invent"))

        assert d13_line.startswith(_D13_RULE)
        assert f"- {_D13_RULE}" in _QUALITY_RULES

    def test_should_keep_the_talk_example_why_inside_its_excerpt(self) -> None:
        talk = _GROUP_RULES.split('primary="learning">')[1].split("</argument_example>")[0]
        why = json.loads(next(ln for ln in talk.splitlines() if ln.startswith('{"claim"')))["why"]

        assert "retired" not in why and "quarter century" not in why
        # p4.6d: the why is the evidence said at 37:24 — it sits in the excerpt too.
        assert "60 times" in why
        assert "about 60 times more code than my long-term running average" in talk.split("\n{")[0]


# ─── R6: the quiz prompt ─────────────────────────────────────────────────


class TestQuizTimestampSource:
    def test_should_copy_a_stamp_only_from_a_sentence_level_item(self) -> None:
        # Copied as written, never converted (p2 review D8).
        assert "a key idea's, claim's or quote's own \"timestamp\" as it stands" in _QUIZ
        assert "Copy the extracted item's own" not in _QUIZ

    def test_should_pick_the_sentence_inside_a_moment_spot_or_step(self) -> None:
        assert "a moment, spot or step is stamped at its start" in _QUIZ

    def test_should_leave_the_range_clause_to_the_renderer(self) -> None:
        assert "{timestamp_range}" in _QUIZ and "(0:00–{video_length})" not in _QUIZ


# ─── R8: memory ──────────────────────────────────────────────────────────


class TestMemoryExample:
    def test_should_give_the_example_the_takeaway_count_its_length_sets(self) -> None:
        header = re.search(r"Example for a (\d+)-minute", _MEMORY)
        assert header is not None
        minutes = int(header.group(1))
        example = _json_after(_MEMORY, "Example for a")

        assert len(example["takeaways"]) == takeaway_count(minutes * 60)

    def test_should_ask_a_recipe_claim_in_the_speakers_own_terms(self) -> None:
        rules = _block(_MEMORY, "summary_rules")

        assert "in the speaker's own terms" in rules and "usual way does not" not in rules

    def test_should_state_a_claim_the_example_video_makes_in_its_tldr(self) -> None:
        example = _json_after(_MEMORY, "Example for a")

        assert "skip the oven" not in example["tldr"]
        assert "consommé" in example["tldr"] and len(example["tldr"]) <= 150


# ─── R9 / R11 / R13: schema skeletons ────────────────────────────────────


class TestSchemaSkeletons:
    def test_should_leave_the_news_claim_stamp_null_in_the_skeleton(self) -> None:
        claim = slice_skeleton("news", ["claims"])["claims"][0]

        assert claim["timestamp"] is None

    def test_should_keep_the_stated_range_in_the_food_example_step(self) -> None:
        step = _json_after(load_schema_text("food"), "// Example for")["steps"][0]

        assert step["duration"] == "8-10 min" and "8-10 minutes" in step["instruction"]

    def test_should_ask_ingredient_amounts_only_as_stated(self) -> None:
        food = load_schema_text("food")

        assert "> AMOUNTS:" in food and "amount: null" in food

    @pytest.mark.parametrize("field", ["warmup", "exercises"])
    def test_should_hold_no_sentence_placeholder_in_the_fitness_numbers(self, field: str) -> None:
        item = slice_skeleton("fitness", [field])[field][0]

        assert (item["reps"], item["duration"], item["rest"]) == (None, None, None)

    def test_should_keep_the_only_if_stated_rule_on_the_numbers_line(self) -> None:
        numbers = next(ln for ln in load_schema_text("fitness").splitlines() if "> NUMBERS:" in ln)

        assert "only if stated, else null" in numbers
