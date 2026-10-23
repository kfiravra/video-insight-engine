"""Phase-C review fixes, lane FP — prompts + registry (review-C-prompts, review-C-assembly).

Each test pins one finding against what the planner or extraction actually reads:
a lecture may open on its Concepts everywhere the plan prompt speaks of the first
tab (the lecture playbook, content-model §3); the quiz is offered only on the formats
quizPolicy ships it on (D9 revised); a talk's asks are briefed as key ideas of kind
ask, the list ``talk_asks`` builds the asks tab from; an interview's Q&A brief names
who answered, never the asker; every key-idea kind the model accepts is one the
prompts name; a review plans a Pros & cons comparison with its verdict in the hero;
an unboxing's rarity is a reason, never a tier, and no info_grid is preferred (D13,
R6); a podcast plans no guests tab; a loadout item has a timestamp slot of its own
so no stamp lands in its note. The LLM is never called.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from unittest.mock import patch

import pytest

from src.models.idea_types import KEY_IDEA_KINDS
from src.services.pipeline import plan_prompt
from src.services.pipeline.assembly.talk_asks import ASK_KIND, ASKS_SOURCE
from src.services.pipeline.extraction_prompt import TEMPLATE_PATH, load_schema_text
from src.services.pipeline.plan_prompt import PlanVideo, render_plan_parts
from src.services.pipeline.schema_fields import slice_skeleton
from src.shared_config.domain_config import get_config, get_playbook


@pytest.fixture(autouse=True)
def disk_prompts() -> Iterator[None]:
    """Render from the files on disk, never a registry version."""
    with (
        patch.object(plan_prompt, "_load_plan_prompt", lambda: plan_prompt.PROMPT_PATH.read_text()),
        patch.object(
            plan_prompt,
            "_load_component_toolkit",
            lambda: plan_prompt.COMPONENT_TOOLKIT_PATH.read_text(),
        ),
    ):
        yield


def _render(category: str, content_format: str) -> str:
    video = PlanVideo(
        title="A video",
        channel="A channel",
        description="",
        duration=3400,
        category_hint=category,
        content_format=content_format,
        transcript="[0:00] Hello.",
    )
    parts = render_plan_parts(video)
    return " ".join((parts.static + parts.video).split())


def _block(prompt: str, tag: str) -> str:
    """The body of the LAST ``<tag>`` block of a whitespace-normalized prompt."""
    start = prompt.rindex(f"<{tag}> ") + len(tag) + 3
    return prompt[start : prompt.index(f"</{tag}>", start)]


def _examples() -> dict[str, dict]:
    text = plan_prompt.PROMPT_PATH.read_text(encoding="utf-8")
    pairs = re.findall(r"<example>\n([^\n]*)\n(.*?)\n</example>", text, flags=re.DOTALL)
    return {header: json.loads(body) for header, body in pairs}


def _briefs(source: str) -> list[str]:
    return [
        tab["brief"]["what"]
        for example in _examples().values()
        for tab in example["tabs"]
        if tab["dataSource"] == source
    ]


# ─── W2: a lecture opens on Concepts ─────────────────────────────────────────

_LECTURE = _render("learning", "lecture")


class TestLectureOpensOnConcepts:
    def test_should_follow_the_playbook_when_the_lecture_playbook_opens_on_concepts(self) -> None:
        assert "open on Concepts — concept_canvas on learning.concepts" in _LECTURE

    @pytest.mark.parametrize(
        "rule",
        [
            "Never concept_canvas, video_filmstrip or info_grid first",
            "concept_canvas only after the list tab",
            "concept_canvas as a later tab, never the first",
            "NEVER the first tab of a talk, lecture",
            "concept_canvas only as a later tab",
        ],
    )
    def test_should_not_forbid_a_lecture_canvas_first_when_rendering_a_lecture_plan(
        self, rule: str
    ) -> None:
        assert rule not in _LECTURE

    def test_should_name_the_lecture_exception_where_spoken_word_forbids_a_canvas_first(
        self,
    ) -> None:
        assert "except a lecture, which opens on Concepts" in _block(_LECTURE, "spoken_word")


# ─── W3: the quiz is offered only where quizPolicy ships it ──────────────────


class TestQuizOnInstructionalFormats:
    def test_should_offer_the_quiz_on_the_quiz_policy_formats_when_the_toolkit_is_read(
        self,
    ) -> None:
        toolkit = _block(_LECTURE, "component_toolkit")
        pick = toolkit[toolkit.index("quiz_arena PICK WHEN") : toolkit.index("SHIPS: Single")]

        assert all(fmt in pick for fmt in get_config()["quizPolicy"]["formats"])

    def test_should_not_offer_the_quiz_on_an_argument_when_the_toolkit_is_read(self) -> None:
        assert "a lesson or an argument" not in _LECTURE

    def test_should_point_the_registry_note_at_quiz_policy_formats(self) -> None:
        note = get_config()["domainRequirementsNote"]

        assert "quizPolicy.formats" in note and "a podcast or news explainer" not in note


# ─── W4: a talk's asks are key ideas of kind ask ─────────────────────────────


class TestTalkAsks:
    def test_should_brief_asks_as_the_kind_talk_asks_reads_when_the_talk_playbook_renders(
        self,
    ) -> None:
        guidance = get_playbook("learning", "talk")["planGuidance"]

        assert f"key ideas of kind {ASK_KIND}" in guidance and ASKS_SOURCE in guidance

    def test_should_route_asks_to_key_ideas_when_spoken_word_is_rendered(self) -> None:
        assert f"Asks and calls to action → key ideas of kind {ASK_KIND}" in _LECTURE

    def test_should_brief_the_example_asks_tab_as_key_ideas_of_kind_ask(self) -> None:
        assert all(f"kind {ASK_KIND}" in what for what in _briefs(ASKS_SOURCE))


# ─── W5: a Q&A brief names who answered ──────────────────────────────────────


class TestQaBrief:
    def test_should_name_who_answered_when_an_example_briefs_podcast_qa(self) -> None:
        briefs = _briefs("podcast.qa")

        assert briefs and all("who answered" in w and "who asked" not in w for w in briefs)


# ─── W6: every accepted kind is named ────────────────────────────────────────


class TestKeyIdeaKinds:
    def test_should_list_every_accepted_kind_when_the_learning_schema_is_read(self) -> None:
        kinds = slice_skeleton("learning", ["keyIdeas"])["keyIdeas"][0]["kind"]

        assert [k.strip() for k in kinds.split("|")] == list(KEY_IDEA_KINDS)

    def test_should_list_every_accepted_kind_when_spoken_word_is_rendered(self) -> None:
        listed = " | ".join(KEY_IDEA_KINDS)

        assert f"kind one of {listed}" in _block(_LECTURE, "spoken_word")

    def test_should_brief_disagreements_when_an_interview_example_plans_positions(self) -> None:
        interviews = [
            tab["brief"]["what"]
            for header, example in _examples().items()
            if "interview" in header or "podcast —" in header
            for tab in example["tabs"]
            if tab["dataSource"] == "learning.keyIdeas"
        ]

        assert interviews and all("disagreement" in what for what in interviews)


# ─── W7: a review's Pros & cons, verdict in the hero ─────────────────────────

_REVIEW = _render("review", "review")


class TestReviewGuidance:
    @pytest.mark.parametrize(
        "stale",
        [
            "comparison carries the verdict",
            "Reviews where only one product is examined — fold the conclusion",
            "- Pros without a counterpart (use spot_explorer",
            "specs folded into comparison",
            "folded into comparison's ReviewSummary header. Cached",
        ],
    )
    def test_should_drop_the_pre_4_3_review_steering_when_rendering_a_review_plan(
        self, stale: str
    ) -> None:
        assert stale not in _REVIEW

    def test_should_plan_pros_and_cons_without_a_competitor_when_rendering_a_review_plan(
        self,
    ) -> None:
        assert "comparison on review.pros ships the reviewer's pros and cons" in _REVIEW

    def test_should_send_a_review_verdict_to_the_hero_when_the_base_map_is_read(self) -> None:
        base = " ".join(TEMPLATE_PATH.read_text(encoding="utf-8").split())

        assert "a review's goes to the hero" in base and "the verdict is its header" not in base


# ─── W8: an unboxing's rarity is a reason, never a tier ──────────────────────


class TestUnboxingTiers:
    def test_should_prefer_no_info_grid_when_the_unboxing_playbook_is_read(self) -> None:
        assert "info_grid" not in get_playbook("gaming", "unboxing")["preferred"]

    def test_should_never_tier_a_rarity_when_the_unboxing_playbook_is_read(self) -> None:
        guidance = get_playbook("gaming", "unboxing")["planGuidance"]

        assert "bracket as tier" not in guidance and "never a tier" in guidance

    def test_should_never_tier_a_rarity_when_an_example_briefs_gaming_rankings(self) -> None:
        briefs = _briefs("gaming.rankings")

        assert briefs and all("bracket as tier" not in what for what in briefs)


# ─── W9: no guests tab ───────────────────────────────────────────────────────


class TestNoGuestsTab:
    def test_should_ride_guests_in_positions_when_rendering_a_podcast_plan(self) -> None:
        prompt = _render("podcast", "podcast")

        assert "first section of Positions — no guests tab" in prompt

    def test_should_not_route_a_podcast_to_a_guests_tab_when_rendering(self) -> None:
        prompt = _render("podcast", "podcast")

        assert "podcast.guests only with 2+" not in prompt and "(guests)" not in prompt


# ─── review-C-assembly "marker text": a loadout stamp has its own slot ───────


class TestLoadoutTimestamp:
    def test_should_give_a_loadout_item_a_timestamp_when_the_gaming_schema_is_read(self) -> None:
        item = slice_skeleton("gaming", ["loadout"])["loadout"][0]

        assert "timestamp" in item

    def test_should_keep_the_stamp_out_of_the_note_when_the_gaming_schema_is_read(self) -> None:
        assert "never written into note" in load_schema_text("gaming")
