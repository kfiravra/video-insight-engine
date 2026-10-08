"""Quiz-only enrichment (pipeline-1min 1d.1): demand gate, prompt, salvage and the stage call."""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.models.pipeline_types import EnrichmentData, PlanResult
from src.services.pipeline import enrichment as enrichment_mod
from src.services.pipeline.enrichment import (
    EMPTY_VIDEO_MEMORY,
    _has_meaningful_data,
    build_quiz_prompt,
    enrich_quiz,
    needs_quiz,
    parse_quiz,
    quiz_allowed,
    render_tab_goals,
)
from src.shared_config.domain_config import data_source, get_config, quiz_enrichment, quiz_policy
from src.utils.language_utils import ENGLISH_OUTPUT_DIRECTIVE

_PATCH_LLM = "src.services.pipeline.enrichment.call_llm_with_retry"
_QUIZ_DOMAINS = quiz_enrichment()["quizDomains"]
_QUIZ_CAP = (data_source("enrichment.quiz") or {"cap": 0})["cap"]
_PLACEHOLDER_RE = re.compile(r"\{[a-z_]+\}")
_VIDEO_MEMORY = "<video_memory>\ndomains: tech · goal: Ship a FastAPI service\n</video_memory>"
_EXTRACTION = {"tech": {"snippets": [{"code": "pip install fastapi", "timestamp": 70}]}}


def _question(n: int = 0, **overrides: Any) -> dict[str, Any]:
    item: dict[str, Any] = {
        "question": f"Which command installs the framework (#{n})?",
        "options": [
            "pip install fastapi",
            "pip install flask",
            "npm i fastapi",
            "brew install fastapi",
        ],
        "correctIndex": 0,
        "explanation": "At 1:10 the speaker runs pip install fastapi before writing any route.",
    }
    item.update(overrides)
    return item


def _tab(component: str, data_source_path: str = "", **extra: Any) -> dict[str, Any]:
    return {"id": component, "label": component.title(), "component": component,
            "dataSource": data_source_path, "goal": f"{component} goal", **extra}  # fmt: skip


def _plan(
    primary: str = "tech", tabs: list[dict] | None = None, evidence: dict | None = None
) -> PlanResult:
    return PlanResult.model_validate(
        {
            "primaryTag": primary,
            "contentTags": [primary],
            "tabs": tabs or [],
            "evidence": evidence or {},
        }
    )


def _llm_reply(*questions: dict[str, Any], **extra: Any) -> str:
    return json.dumps({"quiz": list(questions), **extra})


async def _enrich(primary: str = "tech", tabs: list[dict] | None = None) -> EnrichmentData | None:
    return await enrich_quiz(
        MagicMock(),
        primary_tag=primary,
        extraction_data=_EXTRACTION,
        video_memory=_VIDEO_MEMORY,
        tabs=tabs or [_tab("quiz_arena", "enrichment.quiz")],
    )


class TestNeedsQuiz:
    def test_should_demand_a_quiz_when_the_plan_has_a_quiz_arena_tab(self) -> None:
        assert needs_quiz(_plan(tabs=[_tab("quiz_arena", "enrichment.quiz")]))

    def test_should_demand_a_quiz_when_a_tab_reads_enrichment_quiz(self) -> None:
        assert needs_quiz(_plan(tabs=[_tab("display_section", "enrichment.quiz")]))

    def test_should_demand_a_quiz_tab_even_when_the_plan_says_not_learnable(self) -> None:
        plan = _plan(tabs=[_tab("quiz_arena", "enrichment.quiz")], evidence={"is_learnable": False})
        assert needs_quiz(plan)

    def test_should_demand_a_quiz_when_a_learnable_plan_has_a_strip_host(self) -> None:
        plan = _plan(tabs=[_tab("info_grid", "tech.topics")], evidence={"is_learnable": True})
        assert needs_quiz(plan)

    def test_should_demand_a_quiz_when_learnability_is_unanswered(self) -> None:
        assert needs_quiz(_plan(tabs=[_tab("info_grid", "tech.topics")]))

    def test_should_not_demand_a_quiz_when_the_plan_says_not_learnable(self) -> None:
        plan = _plan(tabs=[_tab("info_grid", "tech.topics")], evidence={"is_learnable": False})
        assert not needs_quiz(plan)

    def test_should_not_demand_a_quiz_when_no_tab_can_host_a_strip(self) -> None:
        plan = _plan(tabs=[_tab("overview"), _tab("video_filmstrip", "frames"), _tab("budget")])
        assert not needs_quiz(plan)

    @pytest.mark.parametrize("host", quiz_policy()["attachmentHostsExclude"])
    def test_should_not_demand_a_quiz_when_the_only_host_is_excluded(self, host: str) -> None:
        assert not needs_quiz(_plan(tabs=[_tab(host, "tech.setup.commands")]))

    def test_should_not_demand_a_quiz_when_the_domain_forbids_quiz_arena(self) -> None:
        tabs = [_tab("quiz_arena", "enrichment.quiz"), _tab("info_grid", "food.tips")]
        assert not needs_quiz(_plan(primary="food", tabs=tabs))

    def test_should_not_demand_a_quiz_without_a_plan(self) -> None:
        assert not needs_quiz(None)

    @pytest.mark.parametrize("domain", sorted(get_config()["domains"]))
    def test_should_allow_a_quiz_exactly_in_the_quiz_domains(self, domain: str) -> None:
        assert quiz_allowed(domain) == (domain in _QUIZ_DOMAINS)


class TestRenderTabGoals:
    def test_should_show_label_component_goal_and_the_brief(self) -> None:
        tab = _tab("quiz_arena", "enrichment.quiz", label="✅ Test Yourself",
                   brief={"what": "checks on routing and DI", "where": [], "expect": 5})  # fmt: skip

        line = render_tab_goals([tab])

        assert (
            line
            == '- "✅ Test Yourself" (quiz_arena): quiz_arena goal — what: checks on routing and DI; expect ~5'
        )

    def test_should_strip_template_and_tag_characters_from_plan_text(self) -> None:
        line = render_tab_goals([_tab("info_grid", goal="use {braces} and <tags>")])

        assert "{" not in line and "<" not in line

    def test_should_say_not_specified_when_there_are_no_tabs(self) -> None:
        assert render_tab_goals([]) == "Not specified"


class TestBuildQuizPrompt:
    @pytest.mark.parametrize("domain", _QUIZ_DOMAINS)
    def test_should_put_the_domain_flavor_line_in_the_prompt(self, domain: str) -> None:
        prompt = build_quiz_prompt(domain, _EXTRACTION, _VIDEO_MEMORY, [])

        assert prompt is not None and quiz_enrichment()["flavor"][domain] in prompt

    def test_should_place_the_video_memory_block_verbatim(self) -> None:
        prompt = build_quiz_prompt("tech", _EXTRACTION, _VIDEO_MEMORY, [])

        assert prompt is not None and _VIDEO_MEMORY in prompt

    def test_should_say_not_available_when_the_run_has_no_video_memory(self) -> None:
        prompt = build_quiz_prompt("tech", _EXTRACTION, "", [])

        assert prompt is not None and EMPTY_VIDEO_MEMORY in prompt

    def test_should_send_the_full_extraction_not_a_prefix(self) -> None:
        long_extraction = {
            "learning": {"keyPoints": [{"text": f"point {i} " + "x" * 200} for i in range(100)]}
        }

        prompt = build_quiz_prompt("learning", long_extraction, _VIDEO_MEMORY, [])

        assert prompt is not None and "point 99 " in prompt

    def test_should_leave_no_placeholder_unfilled(self) -> None:
        prompt = build_quiz_prompt("tech", _EXTRACTION, _VIDEO_MEMORY, [_tab("quiz_arena")])

        assert prompt is not None and not _PLACEHOLDER_RE.findall(prompt)

    def test_should_start_with_the_english_output_directive(self) -> None:
        prompt = build_quiz_prompt("tech", _EXTRACTION, _VIDEO_MEMORY, [])

        assert prompt is not None and prompt.startswith(ENGLISH_OUTPUT_DIRECTIVE)

    def test_should_return_none_when_the_domain_has_no_flavor_line(self) -> None:
        assert build_quiz_prompt("food", _EXTRACTION, _VIDEO_MEMORY, []) is None


class TestParseQuiz:
    @pytest.mark.parametrize(
        "broken",
        [
            _question(question="   "),
            _question(options=["only one"]),
            _question(options=["a", " "]),
            _question(correctIndex=4),
            _question(explanation=""),
            {"front": "Al dente", "back": "Firm to the bite"},
            "not an object",
        ],
    )
    def test_should_drop_a_broken_question_and_keep_the_valid_ones(self, broken: object) -> None:
        questions = parse_quiz({"quiz": [_question(1), broken, _question(2)]}, cap=8)

        assert [q.question for q in questions] == [
            _question(1)["question"],
            _question(2)["question"],
        ]

    def test_should_drop_a_repeated_question(self) -> None:
        repeat = _question(1, question=_question(1)["question"].upper())

        assert len(parse_quiz({"quiz": [_question(1), repeat]}, cap=8)) == 1

    def test_should_keep_at_most_cap_questions(self) -> None:
        assert len(parse_quiz({"quiz": [_question(i) for i in range(12)]}, cap=8)) == 8

    def test_should_accept_a_bare_list(self) -> None:
        assert len(parse_quiz([_question(1), _question(2)], cap=8)) == 2

    def test_should_ignore_keys_other_than_quiz(self) -> None:
        data = {"quiz": [_question(1)], "flashcards": [_question(2)], "scenarios": [_question(3)]}

        assert len(parse_quiz(data, cap=8)) == 1

    def test_should_return_nothing_when_quiz_is_not_a_list(self) -> None:
        assert parse_quiz({"quiz": "none"}, cap=8) == []


class TestEnrichQuiz:
    async def test_should_return_only_the_quiz_when_the_model_adds_other_keys(self) -> None:
        reply = _llm_reply(_question(1), _question(2), _question(3), flashcards=[{"front": "f"}])
        with patch(_PATCH_LLM, new=AsyncMock(return_value=reply)):
            result = await _enrich()

        assert result is not None and list(result.model_dump(by_alias=True)) == ["quiz"]
        assert len(result.quiz) == 3

    async def test_should_call_the_enrichment_stage_with_the_quiz_budget(self) -> None:
        llm = AsyncMock(return_value=_llm_reply(_question(1), _question(2)))
        with patch(_PATCH_LLM, new=llm):
            await _enrich()

        assert llm.await_args is not None
        kwargs = llm.await_args.kwargs
        assert (kwargs["stage_name"], kwargs["max_tokens"], kwargs["timeout"], kwargs["max_retries"]) == (
            "enrichment", 2048, 25.0, 1)  # fmt: skip
        assert kwargs["json_mode"] is True

    async def test_should_keep_at_most_the_registry_cap(self) -> None:
        reply = _llm_reply(*(_question(i) for i in range(_QUIZ_CAP + 3)))
        with patch(_PATCH_LLM, new=AsyncMock(return_value=reply)):
            result = await _enrich()

        assert result is not None and len(result.quiz) == _QUIZ_CAP

    @pytest.mark.parametrize("reply", [None, "", "no json here", _llm_reply(_question(1))])
    async def test_should_return_none_when_fewer_than_two_questions_survive(
        self, reply: str | None
    ) -> None:
        with patch(_PATCH_LLM, new=AsyncMock(return_value=reply)):
            assert await _enrich() is None

    async def test_should_not_call_the_llm_for_a_domain_without_a_flavor_line(self) -> None:
        llm = AsyncMock()
        with patch(_PATCH_LLM, new=llm):
            result = await _enrich(primary="food")

        assert result is None
        llm.assert_not_awaited()

    async def test_should_give_up_when_the_whole_stage_exceeds_its_time_cap(self) -> None:
        async def slow_llm(*_args: object, **_kwargs: object) -> str:
            await asyncio.sleep(1)
            return _llm_reply(_question(1), _question(2))

        with (
            patch(_PATCH_LLM, new=slow_llm),
            patch.object(enrichment_mod, "QUIZ_TOTAL_TIMEOUT_S", 0.01),
        ):
            assert await _enrich() is None

    async def test_should_not_raise_when_the_prompt_file_is_missing(self) -> None:
        with patch.object(
            enrichment_mod, "load_prompt_text", side_effect=FileNotFoundError("gone")
        ):
            assert await _enrich() is None


class TestHasMeaningfulData:
    """The phase skips the quiz when extraction carries nothing to ask about."""

    def test_returns_true_for_nonempty_list(self) -> None:
        assert _has_meaningful_data({"items": [1]})

    def test_returns_true_for_long_string(self) -> None:
        assert _has_meaningful_data({"text": "a long enough string"})

    def test_returns_false_for_empty_dict(self) -> None:
        assert not _has_meaningful_data({})

    def test_returns_false_for_empty_nested(self) -> None:
        assert not _has_meaningful_data({"tech": {"snippets": [], "topics": []}})

    def test_returns_false_for_short_strings(self) -> None:
        assert not _has_meaningful_data({"a": "short"})

    def test_returns_false_for_none_values(self) -> None:
        assert not _has_meaningful_data({"a": None})


class TestRemovedReaders:
    def test_should_have_no_legacy_enrichment_map_in_the_registry(self) -> None:
        assert "enrichment" not in get_config()

    def test_should_have_no_per_domain_enrich_prompts(self) -> None:
        assert not (enrichment_mod.PROMPT_PATH.parent / "enrich").exists()

    def test_should_register_only_the_quiz_as_enrichment_data(self) -> None:
        paths = [
            p for p, spec in get_config()["dataSources"].items() if spec["domain"] == "enrichment"
        ]

        assert paths == ["enrichment.quiz"]
