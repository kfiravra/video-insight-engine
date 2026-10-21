"""Tier-probe formats ``talk`` and ``interview`` (output-quality p1.14, L31).

The probe's format is the only early signal that tells a keynote from a
lecture and a guest show from a podcast; the spoken-word playbooks (1.6)
route on ``<domain>:<format>``. These tests pin the label set against the
prompt, the wording that separates the four spoken-word formats, and that
every consumer of ``format`` takes the new label without a special case.
The LLM is never called.
"""

from __future__ import annotations

import copy
import json
import re
from collections.abc import Iterator
from unittest.mock import patch

import pytest

from src.models.probe_types import DEFAULT_FORMAT, VALID_FORMATS, TierProbe
from src.services.media import visual_tier
from src.services.media.visual_tier import derive_tier
from src.services.pipeline import tier_probe
from src.services.pipeline.phases.triage import probe_hint
from src.services.pipeline.plan_prompt import _render_playbook
from src.services.pipeline.tier_probe import (
    TierProbeInput,
    parse_tier_probe,
    render_tier_probe_prompt,
)
from src.shared_config import domain_config
from src.shared_config.domain_config import effective_requirements, get_playbook

_TEMPLATE = tier_probe.PROMPT_PATH.read_text(encoding="utf-8")
_FORMAT_LINE = re.compile(r"^- ([a-z_]+): (.+)$", re.MULTILINE)
_PLACEHOLDER = re.compile(r"\{[a-z_]+\}")
# The shipped visual-criticality table (test_visual_tier.py reads the same one).
_TIER_TABLE = {
    "highDomains": ["travel", "food", "fitness", "project", "sport"],
    "lowDomains": ["podcast", "news"],
    "highTitleKeywords": ["unboxing", "opening", "pulls", "box break", "haul"],
    "highFormats": ["unboxing"],
}
_TALK_PLAYBOOK = {
    "required": [],
    "preferred": ["spot_explorer", "checklist", "moment_track"],
    "forbidden": ["code_playground"],
    "planGuidance": "A keynote: the argument first.",
}


def _block(name: str) -> str:
    return _TEMPLATE.split(f"<{name}>")[1].split(f"</{name}>")[0]


def _format_lines() -> dict[str, str]:
    return dict(_FORMAT_LINE.findall(_block("formats")))


def _haiku_answer(domain: str, fmt: str) -> str:
    """A fenced answer the way Haiku writes it (S01/S02 traces, gate 0 A/B)."""
    data = {
        "domain": domain,
        "format": fmt,
        "has_visual_demo": False,
        "confidence": 0.95,
        "transcript_usable": True,
    }
    return "```json\n" + json.dumps(data) + "\n```"


def _probe(domain: str, fmt: str, visual: bool = False) -> TierProbe:
    return TierProbe(domain=domain, format=fmt, has_visual_demo=visual, confidence=0.95)


def _keynote_input() -> TierProbeInput:
    return TierProbeInput(
        title="Rails World 2026 Opening Keynote - DHH",
        channel="Ruby on Rails",
        duration=63 * 60,
        youtube_category="Science & Technology",
        description="DHH opens Rails World 2026 in Austin with a keynote on the age of AI agents.",
        transcript="welcome to Austin and to this delirious talk on the future " * 60,
    )


# ─── Label set ───────────────────────────────────────────────────────────────


class TestFormatLabels:
    def test_should_accept_talk_and_interview_as_formats(self) -> None:
        assert {"talk", "interview"} <= VALID_FORMATS

    def test_should_keep_commentary_as_the_default_format(self) -> None:
        assert DEFAULT_FORMAT == "commentary" and DEFAULT_FORMAT in VALID_FORMATS

    def test_should_offer_the_model_exactly_the_formats_the_validator_accepts(self) -> None:
        assert set(_format_lines()) == VALID_FORMATS

    def test_should_list_each_format_once_in_the_prompt(self) -> None:
        ids = [fmt for fmt, _ in _FORMAT_LINE.findall(_block("formats"))]
        assert len(ids) == len(set(ids))


# ─── Fixture answers ─────────────────────────────────────────────────────────


class TestFixtureAnswers:
    def test_should_keep_talk_when_a_keynote_answer_says_so(self) -> None:
        probe = parse_tier_probe(_haiku_answer("learning", "talk"))
        assert probe == _probe("learning", "talk")

    def test_should_keep_interview_when_a_guest_show_answer_says_so(self) -> None:
        probe = parse_tier_probe(_haiku_answer("podcast", "interview"))
        assert probe is not None and (probe.domain, probe.format) == ("podcast", "interview")

    def test_should_normalise_a_capitalised_talk_label(self) -> None:
        probe = parse_tier_probe(_haiku_answer("learning", " Talk "))
        assert probe is not None and probe.format == "talk"

    @pytest.mark.parametrize("label", ["keynote", "fireside", "conversation", "ted_talk", ""])
    def test_should_fall_back_to_the_default_format_when_the_label_is_unknown(
        self, label: str
    ) -> None:
        probe = parse_tier_probe(_haiku_answer("learning", label))
        assert probe is not None and probe.format == DEFAULT_FORMAT


# ─── Prompt wording ──────────────────────────────────────────────────────────


class TestSpokenWordWording:
    def test_should_tie_talk_to_a_keynote_before_an_audience(self) -> None:
        line = _format_lines()["talk"]
        assert "keynote" in line and "audience" in line

    def test_should_tie_lecture_to_teaching_a_subject(self) -> None:
        assert "teaching a subject" in _format_lines()["lecture"]

    def test_should_tie_interview_to_a_guest_being_questioned(self) -> None:
        line = _format_lines()["interview"]
        assert "questioning a guest" in line and "guest's answers" in line

    def test_should_tie_podcast_to_hosts_without_a_guest_at_the_centre(self) -> None:
        line = _format_lines()["podcast"]
        assert "host(s)" in line and "no guest at the centre" in line

    def test_should_send_a_guest_episode_to_interview_not_podcast(self) -> None:
        guidance = _block("classification_guidance")
        assert 'guest is "interview", not "podcast"' in guidance

    def test_should_send_a_developer_keynote_to_learning_talk_not_motivational(self) -> None:
        guidance = _block("classification_guidance")
        assert '"learning" + "talk"' in guidance
        assert '"learning" + "motivational"' not in guidance

    def test_should_keep_a_talk_that_inspires_as_a_talk(self) -> None:
        assert 'still "talk" when it inspires' in _block("classification_guidance")

    def test_should_count_a_talk_as_no_visual_demo(self) -> None:
        false_line = next(
            line for line in _block("has_visual_demo").splitlines() if line.startswith("false")
        )
        assert "talks" in false_line and "lectures" in false_line

    def test_should_render_a_keynote_with_every_placeholder_filled(self) -> None:
        prompt = render_tier_probe_prompt(_TEMPLATE, _keynote_input())
        assert _PLACEHOLDER.findall(prompt) == []
        assert "Opening Keynote" in prompt and "- talk:" in prompt

    def test_should_keep_the_output_schema_unchanged(self) -> None:
        assert (
            '{"domain": "<domain id>", "format": "<format id>", '
            '"has_visual_demo": <true or false>, "confidence": <number from 0.0 to 1.0>, '
            '"transcript_usable": <true or false>}'
        ) in _block("output_format")


# ─── Consumers of ``format`` ─────────────────────────────────────────────────


@pytest.fixture
def tier_table() -> Iterator[None]:
    with patch.object(visual_tier, "visual_criticality_config", return_value=_TIER_TABLE):
        yield


@pytest.fixture
def talk_playbook() -> Iterator[None]:
    """The registry with a ``learning:talk`` playbook (what 1.6 adds)."""
    config = copy.deepcopy(domain_config.get_config())
    config.setdefault("playbooks", {})["learning:talk"] = _TALK_PLAYBOOK
    with patch.object(domain_config, "get_config", return_value=config):
        yield


class TestFormatConsumers:
    @pytest.mark.usefixtures("tier_table")
    def test_should_keep_a_keynote_at_the_standard_frame_tier(self) -> None:
        title = "Rails World 2026 Keynote - DHH"
        assert derive_tier(_probe("learning", "talk"), title) == "standard"

    @pytest.mark.usefixtures("tier_table")
    def test_should_let_a_reveal_keyword_in_the_title_outrank_the_talk_format(self) -> None:
        # S01's real title carries "opening" (a highTitleKeywords entry meant for
        # pack openings): the keyword rule runs before the format, as shipped.
        title = "Rails World 2026 Opening Keynote - DHH"
        assert derive_tier(_probe("learning", "talk"), title) == "high"

    @pytest.mark.usefixtures("tier_table")
    def test_should_put_a_guest_show_without_a_demo_at_the_low_tier(self) -> None:
        assert derive_tier(_probe("podcast", "interview"), "Max Tegmark: Life 3.0") == "low"

    @pytest.mark.usefixtures("tier_table")
    def test_should_lift_a_guest_show_with_a_demo_off_the_low_tier(self) -> None:
        probe = _probe("podcast", "interview", visual=True)
        assert derive_tier(probe, "Omarchy stand-up") == "standard"

    def test_should_render_the_format_in_the_plan_hint(self) -> None:
        assert probe_hint(_probe("learning", "talk")) == (
            "domain=learning format=talk has_visual_demo=false confidence=0.95"
        )

    def test_should_merge_requirements_for_a_talk_without_special_casing(self) -> None:
        merged = effective_requirements("learning", "talk")
        base = effective_requirements("learning", None)
        assert merged["max"] == base["max"] and merged["forbidden"] >= base["forbidden"]

    def test_should_treat_an_unknown_pair_as_no_playbook(self) -> None:
        assert get_playbook("learning", "keynote") == {}
        assert _render_playbook("learning", "keynote") == ""

    @pytest.mark.usefixtures("talk_playbook")
    def test_should_route_a_talk_through_its_playbook_once_registered(self) -> None:
        block = _render_playbook("learning", "talk")
        assert '<playbook for="learning:talk">' in block
        assert "A keynote: the argument first." in block

    @pytest.mark.usefixtures("talk_playbook")
    def test_should_union_the_talk_playbook_forbidden_list_with_the_domain(self) -> None:
        merged = effective_requirements("learning", "talk")
        assert "code_playground" in merged["forbidden"]
        assert merged["preferred"] == ["spot_explorer", "checklist", "moment_track"]
