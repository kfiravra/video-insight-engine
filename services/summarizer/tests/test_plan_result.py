"""Plan output contract (pipeline-1min 1b.2, Appendix B.2).

The plan drops ``reasoning``, ``outboundLinks``, ``identity.audience`` and
``itemCounts`` and adds a ``brief`` per tab, the Appendix-C ``evidence``
booleans and up to 12 canonical ``terms``. Fallback plans keep their shape:
default tabs carry the empty brief and no evidence is invented.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.models.pipeline_types import MAX_PLAN_TERMS, PlanIdentity, PlanResult, TabBrief
from src.services.pipeline import plan as plan_mod
from src.services.pipeline import plan_prompt
from src.shared_config.domain_config import EVIDENCE_KEYS, get_config

_EMPTY_BRIEF = {"what": "", "where": [], "expect": 0}


class TestTabBrief:
    def test_should_return_empty_brief_when_raw_is_not_an_object(self) -> None:
        assert TabBrief.from_raw(["1:10-2:40"]).model_dump() == _EMPTY_BRIEF

    def test_should_normalize_dashes_when_range_uses_an_en_dash(self) -> None:
        assert TabBrief.from_raw({"where": "1:10 – 2:40"}).where == ["1:10-2:40"]

    def test_should_keep_hour_ranges_when_video_is_long(self) -> None:
        assert TabBrief.from_raw({"where": ["1:02:05-1:04:00"]}).where == ["1:02:05-1:04:00"]

    def test_should_drop_prose_when_where_is_not_a_time(self) -> None:
        assert TabBrief.from_raw({"where": ["near the end", "3:00"]}).where == ["3:00"]

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [(14, 14), (14.7, 14), (-3, 0), (True, 0), ("about 9", 9), ("many", 0), (None, 0)],
    )
    def test_should_coerce_expect_to_a_count_when_value_varies(
        self, raw: object, expected: int
    ) -> None:
        assert TabBrief.from_raw({"expect": raw}).expect == expected


class TestPlanResultEvidence:
    def test_should_keep_only_vocabulary_keys_when_planner_answers(self) -> None:
        plan = PlanResult.model_validate(
            {"evidence": {"has_steps": True, "has_ingredients": False, "has_vibes": True}}
        )

        assert plan.evidence == {"has_steps": True, "has_ingredients": False}

    def test_should_drop_non_boolean_answers_when_validating(self) -> None:
        plan = PlanResult.model_validate({"evidence": {"has_code": "false", "is_learnable": 1}})

        assert plan.evidence == {}

    def test_should_hold_no_opinion_when_evidence_is_missing(self) -> None:
        assert PlanResult.model_validate({}).evidence == {}

    def test_should_accept_every_vocabulary_key_when_all_answered(self) -> None:
        answers = dict.fromkeys(EVIDENCE_KEYS, False)

        assert PlanResult.model_validate({"evidence": answers}).evidence == answers


class TestPlanResultTerms:
    def test_should_strip_and_dedupe_when_terms_repeat(self) -> None:
        plan = PlanResult.model_validate({"terms": [" guanciale ", "Guanciale", "", 7, "Marco"]})

        assert plan.terms == ["guanciale", "Marco"]

    def test_should_cap_terms_when_planner_sends_too_many(self) -> None:
        plan = PlanResult.model_validate({"terms": [f"term {i}" for i in range(20)]})

        assert len(plan.terms) == MAX_PLAN_TERMS

    def test_should_return_no_terms_when_value_is_not_a_list(self) -> None:
        assert PlanResult.model_validate({"terms": "guanciale"}).terms == []


class TestPlanResultTabs:
    def test_should_give_every_tab_a_brief_when_validating(self) -> None:
        plan = PlanResult.model_validate({"tabs": [{"id": "a", "label": "A"}]})

        assert plan.tabs[0]["brief"] == _EMPTY_BRIEF

    def test_should_give_fallback_tabs_empty_briefs_when_plan_falls_back(self) -> None:
        plan = plan_mod._build_fallback_plan("food")

        assert plan.tabs and all(tab["brief"] == _EMPTY_BRIEF for tab in plan.tabs)

    def test_should_not_mutate_cached_default_tabs_when_falling_back(self) -> None:
        plan_mod._build_fallback_plan("food")

        defaults = get_config()["domains"]["food"]["defaultTabs"]
        assert not any("brief" in tab for tab in defaults)


class TestDroppedPlanFields:
    def test_should_not_declare_dropped_fields_when_modelling_the_plan(self) -> None:
        declared = set(PlanResult.model_fields) | {
            info.alias for info in PlanResult.model_fields.values() if info.alias
        }

        assert declared.isdisjoint({"reasoning", "item_counts", "itemCounts", "outboundLinks"})

    def test_should_not_declare_audience_when_modelling_identity(self) -> None:
        assert "audience" not in PlanIdentity.model_fields

    def test_should_ignore_dropped_fields_when_an_old_prompt_still_sends_them(self) -> None:
        plan = PlanResult.model_validate(
            {"reasoning": "r", "itemCounts": {"steps": 3}, "identity": {"audience": "x"}}
        )

        assert "reasoning" not in plan.model_dump(by_alias=True)


# ─── run_plan end to end (LLM mocked) ──────────────────────────────────

_GOOD_PLAN = {
    "contentTags": ["food"],
    "modifiers": [],
    "primaryTag": "food",
    "confidence": 0.93,
    "userGoal": "Cook the lasagna this weekend",
    "terms": ["béchamel", "MeatballMethod = sear meatballs, then break them up"],
    "evidence": {"has_steps": True, "has_ingredients": True, "is_learnable": False},
    "tabs": [
        {
            "id": "ingredients",
            "label": "🛒 18 Ingredients",
            "component": "checklist",
            "dataSource": "food.ingredients",
            "goal": "Shop from one list.",
            "brief": {"what": "every ingredient with amount", "where": ["1:10-2:40"], "expect": 18},
        },
        {
            "id": "steps",
            "label": "👨‍🍳 10 Steps",
            "component": "step_player",
            "dataSource": "food.steps",
            "goal": "Cook along.",
            "brief": {"what": "every step with time", "where": ["2:40-13:00"], "expect": 10},
        },
    ],
}


@pytest.fixture
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


async def _run(response: str | None) -> tuple[PlanResult, AsyncMock]:
    mock_call = AsyncMock(return_value=response)
    with patch.object(plan_mod, "call_llm_with_retry", mock_call):
        result = await plan_mod.run_plan(
            title="BA's Best Lasagna",
            channel="Bon Appétit",
            description="Chris makes lasagna.",
            duration=1001,
            category_hint="food",
            content_format="tutorial",
            transcript="[0:00] Hi, I'm Chris.\n[0:21] Today, lasagna.",
            llm_service=MagicMock(),
        )
    return result, mock_call


@pytest.mark.usefixtures("disk_prompts")
class TestRunPlan:
    async def test_should_return_briefs_evidence_and_terms_when_plan_is_valid(self) -> None:
        result, _ = await _run(json.dumps(_GOOD_PLAN))

        assert (result.tabs[1]["brief"]["expect"], result.evidence["has_steps"], result.terms) == (
            10,
            True,
            _GOOD_PLAN["terms"],
        )

    async def test_should_call_with_45s_timeout_and_one_retry_when_planning(self) -> None:
        _, mock_call = await _run(json.dumps(_GOOD_PLAN))

        kwargs = mock_call.call_args.kwargs
        assert (kwargs["timeout"], kwargs["max_retries"]) == (45.0, 1)

    async def test_should_not_request_prompt_caching_when_planning(self) -> None:
        _, mock_call = await _run(json.dumps(_GOOD_PLAN))

        assert mock_call.call_args.kwargs.get("cache_static") is None

    async def test_should_fall_back_with_empty_briefs_when_llm_returns_nothing(self) -> None:
        result, _ = await _run(None)

        assert (result.confidence, result.evidence, result.tabs[0]["brief"]) == (
            0.0,
            {},
            _EMPTY_BRIEF,
        )

    async def test_should_fall_back_when_plan_confidence_is_low(self) -> None:
        result, _ = await _run(json.dumps({**_GOOD_PLAN, "confidence": 0.4}))

        assert (result.confidence, result.terms) == (0.4, [])

    async def test_should_fall_back_when_response_is_not_json(self) -> None:
        result, _ = await _run("not json at all")

        assert result.confidence == 0.0
