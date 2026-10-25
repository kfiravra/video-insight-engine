"""4.1 — an interview routed as one, its guests asked for, its disagreements named.

Kfir 2026-10-22: S14 (Talks at Google, a moderator questioning Peter Attia) is an
interview. The probe gets one line telling a moderated Q&A from a talk, and a
``learning`` + ``interview`` answer reaches the interview playbook through
``playbookAliases``. The guest card is the first section "Guest" of Positions (not
the hero, not a tab), so any spot tab of a podcast run asks for ``podcast.guests``
(one guest included) with the second the guest is introduced. Where host and guest
differ, the key idea is ``kind: disagreement``.
"""

from __future__ import annotations

import pytest

from src.models.domain_types import PodcastGuest
from src.models.idea_types import LearningKeyIdea
from src.services.pipeline import tier_probe
from src.services.pipeline.extraction_prompt import load_schema_text
from src.services.pipeline.field_requests import FieldRequest, requested_fields
from src.services.pipeline.plan import _normalize_plan_data
from src.services.pipeline.plan_prompt import _render_playbook
from src.services.pipeline.plan_reconcile import ARGUMENT_PATHS
from src.services.pipeline.schema_fields import scalar_fields, slice_skeleton
from src.shared_config.domain_config import get_config, get_playbook

_GUIDANCE = get_config()["playbooks"]["podcast:interview"]["planGuidance"]
_POSITIONS = {
    "id": "positions",
    "label": "Positions",
    "component": "spot_explorer",
    "dataSource": "learning.keyIdeas",
}


def _probe_guidance() -> str:
    template = tier_probe.PROMPT_PATH.read_text(encoding="utf-8")
    return template.split("<classification_guidance>")[1].split("</classification_guidance>")[0]


def _guests(content_tags: list[str]) -> list[FieldRequest]:
    fields = requested_fields(
        [_POSITIONS], content_tags, [], scalar_fields, primary_tag=content_tags[0]
    )
    return [f for f in fields if f.path == "podcast.guests"]


# ─── Routing ───


class TestInterviewRouting:
    def test_should_resolve_the_interview_playbook_when_the_probe_says_learning_interview(
        self,
    ) -> None:
        assert get_playbook("learning", "interview") == get_playbook("podcast", "interview")

    def test_should_keep_a_learning_talk_on_the_talk_playbook(self) -> None:
        assert get_playbook("learning", "talk") == get_config()["playbooks"]["learning:talk"]

    def test_should_render_the_qa_tab_in_the_plan_block_when_the_probe_says_learning_interview(
        self,
    ) -> None:
        block = _render_playbook("learning", "interview")

        assert '<playbook for="learning:interview">' in block and "podcast.qa" in block

    def test_should_tell_the_probe_a_moderated_qa_is_an_interview(self) -> None:
        line = next(line for line in _probe_guidance().splitlines() if "throughout" in line)

        assert 'makes an "interview"' in line and 'stays "talk"' in line

    def test_should_ask_for_both_tags_when_an_interview_is_planned(self) -> None:
        assert "tags podcast + learning" in _GUIDANCE


class TestPlanKeepsTheInterviewTabs:
    """A learning + interview plan that forgot the podcast tag keeps its Q&A (4.1, S14)."""

    def _plan(self, *tabs: dict) -> dict:
        data = {"contentTags": ["learning"], "primaryTag": "learning", "tabs": list(tabs)}
        _normalize_plan_data(data, "interview")
        return data

    def _qa(self) -> dict:
        return {
            "id": "qa",
            "label": "Q&A",
            "component": "moment_track",
            "dataSource": "podcast.qa",
            "goal": "After this you know what he was asked",
        }

    def test_should_keep_a_planned_podcast_qa_tab_when_the_plan_forgot_the_podcast_tag(
        self,
    ) -> None:
        data = self._plan({**_POSITIONS, "goal": "After this you know"}, self._qa())

        qa = next(t for t in data["tabs"] if t["id"] == "qa")
        assert qa["dataSource"] == "podcast.qa"

    def test_should_switch_podcast_on_when_a_qa_tab_is_planned_without_the_tag(self) -> None:
        assert self._plan(self._qa())["contentTags"] == ["learning", "podcast"]

    def test_should_record_no_repoint_when_podcast_is_switched_on(self) -> None:
        assert self._plan(self._qa())["reconcile"] == []

    def test_should_keep_a_planned_guests_tab_when_the_plan_forgot_the_podcast_tag(
        self,
    ) -> None:
        guests = {
            "id": "guests",
            "label": "Guest",
            "component": "spot_explorer",
            "dataSource": "podcast.guests",
            "goal": "After this you know who he is",
        }

        assert self._plan(guests)["tabs"][0]["dataSource"] == "podcast.guests"

    def test_should_leave_the_argument_paths_alone_when_interview_paths_switch_podcast_on(
        self,
    ) -> None:
        # where_hints reads ARGUMENT_PATHS: a Q&A line must keep its chunked prompt bytes.
        assert ARGUMENT_PATHS == frozenset({"learning.keyIdeas"})


# ─── Guests ───


class TestGuests:
    def test_should_request_the_guests_when_a_spot_tab_is_planned_on_a_podcast_run(
        self,
    ) -> None:
        assert [f.tab_ids for f in _guests(["podcast", "learning"])] == [("positions",)]

    def test_should_not_request_the_guests_when_podcast_is_not_tagged(self) -> None:
        assert _guests(["learning"]) == []

    def test_should_plan_the_guests_as_the_first_section_of_positions(self) -> None:
        assert "guests ride as its first section, never a tab of their own" in _GUIDANCE

    def test_should_drop_the_two_guest_minimum_when_one_guest_is_on(self) -> None:
        assert "2+ guests" not in _GUIDANCE

    def test_should_keep_the_intro_second_when_a_guest_has_a_clock(self) -> None:
        guest = PodcastGuest.model_validate({"name": "Peter Attia", "timestamp": "1:05"})

        assert guest.timestamp == 65

    def test_should_ask_for_the_intro_second_when_the_guests_are_extracted(self) -> None:
        guest = slice_skeleton("podcast", ["guests"])["guests"][0]

        assert "timestamp" in guest


# ─── Disagreements ───


class TestDisagreements:
    def test_should_accept_kind_disagreement_when_a_key_idea_is_pushback(self) -> None:
        idea = LearningKeyIdea.model_validate({"claim": "c", "kind": "Disagreement"})

        assert idea.kind == "disagreement"

    def test_should_name_disagreements_as_their_own_kind_when_an_interview_is_planned(
        self,
    ) -> None:
        assert "disagreements as kind disagreement" in _GUIDANCE

    @pytest.mark.parametrize("needle", ['kind "disagreement"', "speaker = who pushes back"])
    def test_should_tell_the_extraction_how_to_write_a_disagreement(self, needle: str) -> None:
        assert needle in load_schema_text("podcast")
