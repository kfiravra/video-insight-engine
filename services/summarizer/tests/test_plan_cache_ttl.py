"""Plan prompt cache (pipeline-1min 2.0): a 1h breakpoint on a static half no video touches.

The plan prompt is cut at its ``<video>`` line. Everything above it — the
language directive, rules, toolkit, registry blocks, examples — is rendered
without the video, so two different videos send a byte-identical first block,
marked ``cache_control {type: ephemeral, ttl: 1h}``; the video block, the
transcript and the closing line follow uncached.
"""

from __future__ import annotations

from collections.abc import Iterator
from unittest.mock import patch

import pytest

from src.services.pipeline import plan_prompt
from src.services.pipeline.plan import plan_user_content
from src.services.pipeline.plan_prompt import PlanPrompt, PlanVideo, render_plan_parts
from src.utils.language_utils import ENGLISH_OUTPUT_DIRECTIVE

_ONE_HOUR = {"type": "ephemeral", "ttl": "1h"}

_VIDEO_A = PlanVideo(
    title="Quokka Birria Tacos 7731",
    channel="Chan-9931",
    description="desc-5521 smoky chiles",
    duration=1001,
    category_hint="gaming",
    content_format="unboxing",
    transcript="[0:00] marker-4410 toast the chiles",
    probe_hint="hint-8812 gaming unboxing",
)
_VIDEO_B = PlanVideo(
    title="Zebra Rust Lifetimes 1209",
    channel="Chan-3307",
    description="desc-6610 borrow checker",
    duration=3720,
    category_hint="tech",
    content_format="lecture",
    transcript="[0:00] marker-7702 lifetimes",
    probe_hint="hint-2290 tech lecture",
)


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


def _pre_split_render(video: PlanVideo) -> str:
    """The plan prompt as one string, rendered the way it was before the split."""
    template = plan_prompt.PROMPT_PATH.read_text()
    filled = plan_prompt._render_video(plan_prompt._render_static(template), video)
    return f"{ENGLISH_OUTPUT_DIRECTIVE}\n\n{filled}"


class TestStaticHalf:
    def test_should_render_an_identical_static_half_when_videos_differ(self) -> None:
        assert render_plan_parts(_VIDEO_A).static == render_plan_parts(_VIDEO_B).static

    @pytest.mark.parametrize(
        "video_value",
        [
            "Quokka Birria Tacos 7731",
            "Chan-9931",
            "desc-5521",
            "marker-4410",
            "hint-8812",
            "Duration: 17 minutes",
            '<playbook for="gaming:unboxing">',
        ],
    )
    def test_should_render_every_video_value_in_the_video_half_only(self, video_value: str) -> None:
        parts = render_plan_parts(_VIDEO_A)

        assert (video_value in parts.video, video_value in parts.static) == (True, False)

    def test_should_start_the_video_half_at_the_video_block(self) -> None:
        assert render_plan_parts(_VIDEO_A).video.startswith("<video>\nTitle: Quokka Birria Tacos")

    def test_should_send_the_same_text_as_the_single_string_prompt_when_split(self) -> None:
        parts = render_plan_parts(_VIDEO_A)

        assert parts.static + parts.video == _pre_split_render(_VIDEO_A)

    def test_should_render_whole_prompt_without_a_static_half_when_template_has_no_video_line(
        self,
    ) -> None:
        template = (
            "<rules>static</rules>\nTitle: {title}\n<transcript>\n{transcript}\n</transcript>"
        )
        with patch.object(plan_prompt, "_load_plan_prompt", lambda: template):
            parts = render_plan_parts(_VIDEO_A)

        assert (parts.static, "Quokka Birria Tacos 7731" in parts.video) == ("", True)


class TestPlanUserContent:
    def test_should_mark_the_static_half_with_a_one_hour_breakpoint(self) -> None:
        parts = render_plan_parts(_VIDEO_A)

        first = plan_user_content(parts)[0]

        assert first == {"type": "text", "text": parts.static, "cache_control": _ONE_HOUR}

    def test_should_send_the_video_half_unmarked_after_the_static_half(self) -> None:
        parts = render_plan_parts(_VIDEO_A)

        second = plan_user_content(parts)[1]

        assert second == {"type": "text", "text": parts.video}

    def test_should_send_one_plain_string_when_there_is_no_static_half(self) -> None:
        assert plan_user_content(PlanPrompt(static="", video="whole prompt")) == "whole prompt"
