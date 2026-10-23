"""The quiz's format gate (output-quality 4.6e; D9 revised by Kfir 2026-10-23).

The quiz ships only on instructional formats — ``quizPolicy.formats`` in domains.json, in
the tier probe's vocabulary: a lecture (an explainer that builds up concepts is one) and a
tutorial / how-to — never on a talk, an interview, a podcast or the news. On any other
format no quiz call is made, flag on or off; an unknown format rules nothing out — and no
playbook of such a format offers the planner a quiz tab reconcile would drop.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.config import settings
from src.models.pipeline_types import EnrichmentData, PlanResult, QuizQuestion
from src.models.probe_types import VALID_FORMATS
from src.services.pipeline.phases import enrichment as enrichment_phase
from src.services.pipeline.quiz_format import quiz_format_allowed, quiz_formats
from src.shared_config.domain_config import get_config, get_playbook, quiz_policy

_INSTRUCTIONAL = ("lecture", "tutorial")
# Revised D9's own list of the formats that never carry a quiz.
_NEVER = ("talk", "interview", "podcast", "news")
_OTHER = tuple(sorted(VALID_FORMATS - {*_INSTRUCTIONAL, *_NEVER}))
_QUIZ_TAB = {
    "id": "quiz",
    "label": "Quiz",
    "component": "quiz_arena",
    "dataSource": "enrichment.quiz",
}
_QUESTION = QuizQuestion.model_validate(
    {"question": "Why?", "options": ["a", "b", "c", "d"], "correctIndex": 1, "explanation": "b."}
)
_EXTRACTION = {"learning": {"keyIdeas": [{"claim": "Sleep consolidates memory"}]}}


def _ctx(content_format: str | None, primary: str = "learning") -> Any:
    plan = PlanResult.model_validate(
        {"primaryTag": primary, "contentTags": [primary], "tabs": [_QUIZ_TAB]}
    )
    return SimpleNamespace(
        video_summary_id="vsid",
        plan_result=plan,
        content_format=content_format,
        extraction_data=_EXTRACTION,
        video_memory="",
        enrichment_data=None,
        llm_service=MagicMock(),
        quiz_task=None,
        video_data=SimpleNamespace(duration=600, title="t"),
        memory=None,
        prompt_transcript="[0:00] Sleep consolidates memory.",
    )


def _registry_formats() -> list[str]:
    """``quizPolicy.formats`` as the registry holds it (the TypedDict predates the key)."""
    policy: dict[str, Any] = dict(quiz_policy())
    return list(policy["formats"])


async def _late_group(ctx: Any) -> tuple[list[str], AsyncMock, AsyncMock]:
    """The late group's own quiz call (no task started), both quiz versions faked."""
    v1 = AsyncMock(return_value=EnrichmentData(quiz=[_QUESTION, _QUESTION]))
    v2 = AsyncMock(return_value=EnrichmentData(quiz=[_QUESTION, _QUESTION, _QUESTION]))
    with (
        patch.object(enrichment_phase, "enrich_quiz", v1),
        patch.object(enrichment_phase, "enrich_quiz_v2", v2),
    ):
        events = [event async for event in enrichment_phase.run_phase_enrichment(ctx)]
    return events, v1, v2


# ─── The registry ───


class TestQuizFormats:
    def test_should_ship_the_quiz_only_on_the_instructional_formats(self) -> None:
        assert quiz_formats() == frozenset(_INSTRUCTIONAL)

    def test_should_name_only_formats_the_tier_probe_can_answer(self) -> None:
        assert set(_registry_formats()) <= VALID_FORMATS

    @pytest.mark.parametrize("content_format", _INSTRUCTIONAL)
    def test_should_allow_a_quiz_when_the_format_is_instructional(
        self, content_format: str
    ) -> None:
        assert quiz_format_allowed(content_format)

    @pytest.mark.parametrize("content_format", _NEVER)
    def test_should_refuse_a_quiz_when_the_format_is_one_revised_d9_names(
        self, content_format: str
    ) -> None:
        assert not quiz_format_allowed(content_format)

    @pytest.mark.parametrize("content_format", _OTHER)
    def test_should_refuse_a_quiz_when_the_format_is_any_other_probe_format(
        self, content_format: str
    ) -> None:
        assert not quiz_format_allowed(content_format)

    def test_should_rule_nothing_out_when_the_format_is_unknown(self) -> None:
        assert quiz_format_allowed(None)


# ─── No call where no quiz may ship ───


class TestNoQuizCall:
    @pytest.mark.parametrize("content_format", _NEVER)
    async def test_should_start_no_quiz_task_when_the_format_is_not_instructional(
        self, content_format: str
    ) -> None:
        ctx = _ctx(content_format)

        started = enrichment_phase.start_quiz_task(ctx, _EXTRACTION)

        assert (started, ctx.quiz_task) == (False, None)

    async def test_should_make_no_quiz_v2_call_when_the_late_group_runs_on_a_talk(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(settings, "QUIZ_V2_ENABLED", True)

        events, _, v2 = await _late_group(_ctx("talk"))

        assert (events, v2.await_count) == ([], 0)

    async def test_should_make_no_flag_off_quiz_call_when_the_late_group_runs_on_a_podcast(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(settings, "QUIZ_V2_ENABLED", False)

        events, v1, _ = await _late_group(_ctx("podcast", primary="podcast"))

        assert (events, v1.await_count) == ([], 0)

    async def test_should_store_no_quiz_when_the_format_is_news(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(settings, "QUIZ_V2_ENABLED", True)
        ctx = _ctx("news", primary="news")

        await _late_group(ctx)

        assert ctx.enrichment_data is None

    async def test_should_still_call_the_quiz_when_the_format_is_a_lecture(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(settings, "QUIZ_V2_ENABLED", True)

        events, _, v2 = await _late_group(_ctx("lecture"))

        assert (len(events), v2.await_count) == (1, 1)

    async def test_should_still_call_the_quiz_when_the_probe_gave_no_format(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(settings, "QUIZ_V2_ENABLED", True)

        _, _, v2 = await _late_group(_ctx(None))

        assert v2.await_count == 1


# ─── The planner is never offered a quiz that cannot ship ───


def _playbook_keys() -> list[str]:
    """Every ``<domain>:<format>`` the planner gets a playbook for, aliases included."""
    config = get_config()
    return sorted({*config["playbooks"], *config.get("playbookAliases", {})})


_BLOCKED_PLAYBOOKS = [key for key in _playbook_keys() if not quiz_format_allowed(key.split(":")[1])]


class TestPlaybooks:
    @pytest.mark.parametrize("key", _BLOCKED_PLAYBOOKS)
    def test_should_not_prefer_a_quiz_when_the_playbooks_format_carries_none(
        self, key: str
    ) -> None:
        domain, content_format = key.split(":")

        assert "quiz_arena" not in get_playbook(domain, content_format).get("preferred", [])

    @pytest.mark.parametrize("key", _BLOCKED_PLAYBOOKS)
    def test_should_not_guide_the_planner_to_a_quiz_when_the_format_carries_none(
        self, key: str
    ) -> None:
        domain, content_format = key.split(":")

        guidance = get_playbook(domain, content_format).get("planGuidance", "")
        assert "test yourself" not in guidance.casefold()

    def test_should_still_offer_the_quiz_when_the_video_is_a_lecture(self) -> None:
        assert "quiz_arena" in get_playbook("learning", "lecture")["preferred"]
