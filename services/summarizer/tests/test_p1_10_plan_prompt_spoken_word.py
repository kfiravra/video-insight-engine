"""Plan prompt for spoken word (output-quality 1.10).

A talk, a podcast and an interview example open on a key-ideas list
(spot_explorer on learning.keyIdeas), never a canvas; a lecture opens on its
Concepts (concept_canvas, read as a list first — the lecture playbook,
content-model §3) with the key ideas after it; every goal names
what the viewer gets (the viewer-outcome rule, plan Q12); no label carries a
digit; counts follow content-model §9 by duration; Moments say what was
established, never chapter jumps.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from unittest.mock import patch

import pytest

from src.services.pipeline import plan_prompt
from src.services.pipeline.plan_prompt import PlanVideo, goal_rule_violation, render_plan_parts

_NEVER_FIRST = frozenset(
    {"concept_canvas", "step_flow_canvas", "video_filmstrip", "info_grid", "overview"}
)
_SPOKEN_WORD_KINDS = ("talk", "lecture", "podcast", "interview")
_KEY_IDEAS_FIRST_KINDS = ("talk", "podcast", "interview")


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


_VIDEO = PlanVideo(
    title="Rails World 2026 Opening Keynote - DHH",
    channel="Ruby on Rails",
    description="The opening keynote.",
    duration=3787,
    category_hint="learning",
    content_format="talk",
    transcript="[0:00] Welcome.\n[1:50] My great-great-grandfather painted portraits.",
)


def _render() -> str:
    parts = render_plan_parts(_VIDEO)
    return parts.static + parts.video


def _block(prompt: str, tag: str) -> str:
    """The body of the LAST ``<tag>`` block — the tag on its own line, not a prose mention."""
    start = prompt.rindex(f"<{tag}>\n") + len(tag) + 3
    return prompt[start : prompt.index(f"</{tag}>", start)]


def _examples() -> dict[str, dict]:
    """The worked examples keyed by their ``Video: …`` header line."""
    text = plan_prompt.PROMPT_PATH.read_text(encoding="utf-8")
    pairs = re.findall(r"<example>\n([^\n]*)\n(.*?)\n</example>", text, flags=re.DOTALL)
    return {header: json.loads(body) for header, body in pairs}


def _header(header: str) -> re.Match[str]:
    """The ``(63 min, conference talk — …)`` parenthesis of an example header."""
    match = re.search(r"\((\d+) min, ([^—)]+)", header)
    assert match, header
    return match


def _kind(header: str) -> str:
    return _header(header).group(2).strip()


def _minutes(header: str) -> int:
    return int(_header(header).group(1))


_EXAMPLES = _examples()
_SPOKEN = {
    kind: next((header, example) for header, example in _EXAMPLES.items() if kind in _kind(header))
    for kind in _SPOKEN_WORD_KINDS
}


def _tabs(example: dict, component: str) -> list[dict]:
    return [tab for tab in example["tabs"] if tab["component"] == component]


def _key_ideas_tab(example: dict) -> dict:
    return next(tab for tab in example["tabs"] if tab["dataSource"] == "learning.keyIdeas")


def _idea_band(minutes: int) -> range:
    """content-model §9: key ideas / positions by duration."""
    if minutes <= 15:
        return range(5, 9)
    if minutes <= 45:
        return range(8, 13)
    if minutes <= 90:
        return range(12, 17)
    return range(16, 21)


def _moment_band(minutes: int) -> range:
    """content-model §9: moments by duration."""
    if minutes <= 15:
        return range(4, 7)
    if minutes <= 45:
        return range(8, 13)
    if minutes <= 90:
        return range(12, 19)
    return range(minutes // 6 - 2, minutes // 6 + 3)


# ─── The four spoken-word examples ──────────────────────────────────────


class TestSpokenWordExamples:
    def test_should_ship_a_talk_a_lecture_a_podcast_and_an_interview_when_prompt_is_read(
        self,
    ) -> None:
        kinds = {_kind(header) for header in _EXAMPLES}

        assert all(any(kind in k for k in kinds) for kind in _SPOKEN_WORD_KINDS)

    @pytest.mark.parametrize("kind", _KEY_IDEAS_FIRST_KINDS)
    def test_should_open_on_the_key_ideas_list_when_showing_a_spoken_word_plan(
        self, kind: str
    ) -> None:
        first = _SPOKEN[kind][1]["tabs"][0]

        assert (first["component"], first["dataSource"]) == ("spot_explorer", "learning.keyIdeas")

    def test_should_open_on_concepts_then_key_ideas_when_showing_the_lecture_plan(self) -> None:
        """The lecture playbook (content-model §3, review-C-prompts W2): Concepts first."""
        first, second = _SPOKEN["lecture"][1]["tabs"][:2]

        assert [(t["component"], t["dataSource"]) for t in (first, second)] == [
            ("concept_canvas", "learning.concepts"),
            ("spot_explorer", "learning.keyIdeas"),
        ]

    @pytest.mark.parametrize("kind", _SPOKEN_WORD_KINDS)
    def test_should_size_ideas_and_moments_to_length_when_showing_a_spoken_word_plan(
        self, kind: str
    ) -> None:
        header, example = _SPOKEN[kind]
        minutes = _minutes(header)
        ideas = _key_ideas_tab(example)["brief"]["expect"]
        moments = [tab["brief"]["expect"] for tab in _tabs(example, "moment_track")]

        assert ideas in _idea_band(minutes) and all(m in _moment_band(minutes) for m in moments)

    @pytest.mark.parametrize("kind", _SPOKEN_WORD_KINDS)
    def test_should_brief_moments_as_what_was_established_when_showing_a_spoken_word_plan(
        self, kind: str
    ) -> None:
        briefs = [tab["brief"]["what"].lower() for tab in _tabs(_SPOKEN[kind][1], "moment_track")]

        assert briefs and all(
            any(word in what for word in ("established", "concluded", "answer")) for what in briefs
        )

    def test_should_route_segments_and_qa_to_moment_track_when_showing_podcast_plans(
        self,
    ) -> None:
        sources = {
            kind: {t["dataSource"] for t in _tabs(_SPOKEN[kind][1], "moment_track")}
            for kind in ("podcast", "interview")
        }

        assert "podcast.segments" in sources["podcast"] and sources["interview"] >= {
            "podcast.segments",
            "podcast.qa",
        }

    def test_should_size_the_quiz_to_length_when_the_spoken_word_plan_is_a_lecture(self) -> None:
        _, lecture = _SPOKEN["lecture"]

        assert [t["brief"]["expect"] for t in _tabs(lecture, "quiz_arena")] == [8]

    # D9 revised (output-quality 4.6e): the quiz ships only on a lecture or a tutorial.
    @pytest.mark.parametrize("kind", ("talk", "podcast", "interview"))
    def test_should_plan_no_quiz_when_the_spoken_word_plan_is_not_instructional(
        self, kind: str
    ) -> None:
        assert _tabs(_SPOKEN[kind][1], "quiz_arena") == []


class TestEveryExample:
    @pytest.mark.parametrize("header", list(_EXAMPLES), ids=_kind)
    def test_should_not_open_on_a_canvas_or_a_grid_when_showing_a_plan(self, header: str) -> None:
        lecture = "lecture" in _kind(header)
        never_first = _NEVER_FIRST - {"concept_canvas"} if lecture else _NEVER_FIRST

        assert _EXAMPLES[header]["tabs"][0]["component"] not in never_first

    @pytest.mark.parametrize("header", list(_EXAMPLES), ids=_kind)
    def test_should_never_brief_moments_as_chapter_jumps_when_showing_a_plan(
        self, header: str
    ) -> None:
        briefs = [t["brief"]["what"].lower() for t in _tabs(_EXAMPLES[header], "moment_track")]

        assert all("chapter jump" not in what for what in briefs)

    @pytest.mark.parametrize("header", list(_EXAMPLES), ids=_kind)
    def test_should_carry_no_digit_in_any_label_when_showing_a_plan(self, header: str) -> None:
        labels = [tab["label"] for tab in _EXAMPLES[header]["tabs"]]

        assert [label for label in labels if re.search(r"\d", label)] == []

    @pytest.mark.parametrize("header", list(_EXAMPLES), ids=_kind)
    def test_should_name_a_viewer_outcome_in_every_goal_when_showing_a_plan(
        self, header: str
    ) -> None:
        failing = {tab["id"]: goal_rule_violation(tab["goal"]) for tab in _EXAMPLES[header]["tabs"]}

        assert {tab: why for tab, why in failing.items() if why} == {}

    def test_should_not_plan_moments_when_the_steps_are_the_timeline(self) -> None:
        """D6: a recipe's steps are its timeline — no moment_track beside step_player."""
        with_steps = [ex for ex in _EXAMPLES.values() if _tabs(ex, "step_player")]

        assert with_steps and all(not _tabs(ex, "moment_track") for ex in with_steps)


# ─── The goal rule ──────────────────────────────────────────────────────


class TestGoalRule:
    def test_should_state_the_viewer_outcome_rule_when_rendering(self) -> None:
        block = " ".join(_block(_render(), "goal_rule").split())

        assert "After this you know" in block and "Never the component" in block

    def test_should_drop_the_retired_goal_guidance_when_rendering(self) -> None:
        prompt = _render()

        assert "<tab_goal_guidance>" not in prompt and "~10 things" not in prompt

    @pytest.mark.parametrize(
        ("goal", "reason"),
        [
            ("A timeline of DHH's keynote with the key moments", "component"),
            ("Browsable day-by-day plan with spots, costs, and map links", "component"),
            ("Drag concepts around to see how they connect.", "component"),
            ("moment_track of the beats, with the quotes you can hear", "component"),
            ("Key points about React Server Components", "topic"),
            ("Everything about the H2 chip, in order", "topic"),
            ("Jump to any concept demonstration in the video.", "viewer"),
            ("The argument in order, each claim with its reasoning.", "viewer"),
            ("   ", "empty"),
        ],
    )
    def test_should_fail_a_goal_that_names_the_component_or_topic(
        self, goal: str, reason: str
    ) -> None:
        assert reason in (goal_rule_violation(goal) or "")

    @pytest.mark.parametrize(
        "goal",
        [
            "After this you know what DHH claims, why, and where he says it.",
            "After this you can cook the steak Gordon's way, timers included.",
            "After this you know the box odds and the chase cards before you buy.",
            "You'll know which of Tegmark's positions you can restate.",
        ],
    )
    def test_should_pass_a_goal_that_names_what_the_viewer_gets(self, goal: str) -> None:
        assert goal_rule_violation(goal) is None

    def test_should_pass_the_output_schema_goal_when_prompt_is_read(self) -> None:
        prompt = _render()
        start = prompt.index("<output_schema>\n") + len("<output_schema>\n")
        schema = json.loads(prompt[start : prompt.index("</output_schema>", start)])

        assert [goal_rule_violation(tab["goal"]) for tab in schema["tabs"]] == [None]


# ─── Sizing, labels, vocabulary ─────────────────────────────────────────


class TestSizing:
    def test_should_render_counts_by_duration_when_rendering(self) -> None:
        rows = {
            line.split("|")[1].strip(): line
            for line in _block(_render(), "sizing").splitlines()
            if line.startswith("| ")
        }

        assert "12–16" in rows["45–90 min"] and "12–18" in rows["45–90 min"]
        assert rows["≤ 15 min"].rstrip("| ").endswith("3")

    def test_should_keep_the_registry_caps_after_the_sizing_table_when_rendering(self) -> None:
        prompt = _render()

        assert prompt.index("<sizing>\n") < prompt.index("<extraction_caps>\n")
        assert "ingredients ≤" in _block(prompt, "extraction_caps")


class TestLabelsAndVocabulary:
    def test_should_tell_the_planner_labels_carry_no_count_when_rendering(self) -> None:
        prompt = _render()

        assert "never a count" in prompt and "labels carry no digits" in prompt

    def test_should_name_the_key_ideas_shape_when_rendering(self) -> None:
        block = " ".join(_block(_render(), "spoken_word").split())

        assert "learning.keyIdeas" in block
        assert "thesis | argument | announcement | number | story | ask" in block

    def test_should_say_moments_are_what_was_established_when_rendering(self) -> None:
        block = " ".join(_block(_render(), "spoken_word").split())

        assert "what was established at each beat" in block and "never chapter jumps" in block

    def test_should_forbid_a_canvas_as_the_first_tab_when_rendering(self) -> None:
        prompt = _render()

        assert "Never a canvas as the first tab" in prompt
        assert "info_grid as the first tab" in _block(prompt, "component_toolkit")

    def test_should_route_quotes_onto_items_when_rendering(self) -> None:
        toolkit = _block(_render(), "component_toolkit")

        assert "learning.keyIdeas" in toolkit and "no quote deck" in toolkit

    def test_should_list_podcast_sources_for_moment_track_when_rendering(self) -> None:
        toolkit = " ".join(_block(_render(), "component_toolkit").split())

        assert "podcast.segments (speaker + what was concluded)" in toolkit
        assert "podcast.qa" in toolkit
