"""Plan-parse guard (hotfix 2.1, narrowed by 3.2): re-point or drop tabs on a domain
the plan did not tag. Evidence is judged at plan-done (``test_reconcile.py``)."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from src.models.pipeline_types import PlanResult
from src.services.pipeline import plan as plan_mod
from src.services.pipeline import plan_prompt
from src.services.pipeline.plan_reconcile import reconcile_plan_tabs, replacement
from tests.test_phase_assembly_cache import _build_ctx


def _tab(tab_id: str, component: str, data_source: str) -> dict:
    return {"id": tab_id, "label": tab_id, "component": component, "dataSource": data_source}


def _9vng_tabs() -> list[dict]:
    """9VNG0h4pLh0 (prod 2026-10-08): news.claims planned on a learning/tech video.

    Since output-quality 1.4 (D11) claims are unfenced: the tab re-points to
    ``learning.claims``. The drop is pinned on a path no active domain can take
    (a sport formation on the same video)."""
    return [
        _tab("concepts", "concept_canvas", "learning.concepts"),
        _tab("real_world_cases", "formation_diagram", "sport.formation"),
        _tab("moments", "moment_track", "learning.timestamps"),
        _tab("quiz", "quiz_arena", "enrichment.quiz"),
    ]


class TestInactiveDomain:
    def test_should_drop_a_tab_whose_domain_is_not_a_content_tag(self) -> None:
        kept, _, _ = reconcile_plan_tabs(_9vng_tabs(), ["learning", "tech"], [])

        assert [t["id"] for t in kept] == ["concepts", "moments", "quiz"]

    def test_should_record_the_drop_for_pipeline_reconcile(self) -> None:
        _, _, records = reconcile_plan_tabs(_9vng_tabs(), ["learning", "tech"], [])

        assert records == [
            {
                "tabId": "real_world_cases",
                "component": "formation_diagram",
                "from": "sport.formation",
                "to": None,
                "reason": "inactive_domain",
                "action": "dropped",
                "stage": "plan_parse",
                "evidence": None,
            }
        ]

    def test_should_list_the_drop_in_the_dropped_tabs_shape(self) -> None:
        _, dropped, _ = reconcile_plan_tabs(_9vng_tabs(), ["learning", "tech"], [])

        assert dropped == [
            {
                "id": "real_world_cases",
                "component": "formation_diagram",
                "dataSource": "sport.formation",
                "reason": "reconcile_inactive_domain",
            }
        ]

    def test_should_count_modifiers_as_active_domains(self) -> None:
        tab = _tab("moments", "moment_track", "narrative.keyMoments")

        kept, _, records = reconcile_plan_tabs([tab], ["learning"], ["narrative"])

        assert (len(kept), records) == (1, [])

    def test_should_flag_a_modifier_path_when_the_modifier_is_not_planned(self) -> None:
        tab = _tab("moments", "moment_track", "narrative.keyMoments")

        _, _, records = reconcile_plan_tabs([tab], ["learning"], [])

        assert records[0]["reason"] == "inactive_domain"

    def test_should_reuse_a_source_another_tab_renders_with_another_component(self) -> None:
        """jMq8lEu-of0 replay: moments planned on learning.timestamps for a food-only video."""
        tabs = [
            _tab("cook_steps", "step_player", "food.steps"),
            _tab("key_moments", "moment_track", "learning.timestamps"),
        ]

        kept, _, records = reconcile_plan_tabs(tabs, ["food"], [])

        assert (kept[1]["dataSource"], records[0]["reason"]) == ("food.steps", "inactive_domain")


class TestEvidenceWaitsForPlanDone:
    """The plan's own ``false`` cannot make a key doubly false — the parse keeps the tab."""

    def test_should_keep_a_tab_whose_evidence_the_plan_marked_false(self) -> None:
        tab = _tab("code", "code_playground", "tech.snippets")

        kept, _, records = reconcile_plan_tabs([tab], ["tech"], [])

        assert (kept, records) == ([tab], [])

    def test_should_keep_the_quiz_whatever_the_plan_said_about_learnability(self) -> None:
        quiz = _tab("quiz", "quiz_arena", "enrichment.quiz")

        kept, _, _ = reconcile_plan_tabs([quiz], ["learning"], [])

        assert kept == [quiz]

    def test_should_leave_unregistered_sources_alone(self) -> None:
        tab = _tab("filmstrip", "video_filmstrip", "frames")

        kept, _, records = reconcile_plan_tabs([tab], ["learning"], [])

        assert (kept, records) == ([tab], [])


class TestReplacement:
    """``replacement`` — the same-component re-point reconcile also uses at plan-done."""

    def test_should_swap_in_a_same_component_sibling_when_evidence_is_false(self) -> None:
        target = replacement("tech.snippets", "code_playground", ["tech"], {}, set())

        assert target == "tech.patterns"

    def test_should_skip_a_source_whose_evidence_is_false(self) -> None:
        target = replacement(
            "tech.snippets", "code_playground", ["tech"], {"has_code": False}, set()
        )

        assert target is None

    def test_should_skip_a_source_another_tab_renders_with_the_same_component(self) -> None:
        taken = {("tech.snippets", "code_playground"), ("tech.patterns", "code_playground")}

        target = replacement("tech.snippets", "code_playground", ["tech"], {}, taken)

        assert target == "tech.cheatSheet"

    def test_should_accept_a_replacement_whose_evidence_key_is_unanswered(self) -> None:
        target = replacement("learning.timestamps", "moment_track", ["food"], {}, set())

        assert target == "food.steps"


class TestMomentTrackNeverDropped:
    """Kfir, phase-1 close: a moment_track tab stays; assembly fills it from the fallback chain."""

    def test_should_keep_a_moment_tab_no_active_source_can_feed(self) -> None:
        """T1dQhQAm8Tc (prod 2026-10-09): moments on learning.timestamps for a tech-only video."""
        tab = _tab("moments", "moment_track", "learning.timestamps")

        kept, dropped, _ = reconcile_plan_tabs([tab], ["tech"], [])

        assert (kept, dropped) == ([tab], [])

    def test_should_record_the_kept_moment_tab_for_the_fallback(self) -> None:
        tab = _tab("moments", "moment_track", "learning.timestamps")

        _, _, records = reconcile_plan_tabs([tab], ["tech"], [])

        assert (records[0]["reason"], records[0]["action"]) == (
            "inactive_domain",
            "kept_for_moment_fallback",
        )

    def test_should_still_repoint_a_moment_tab_when_an_active_source_can_feed_it(self) -> None:
        tab = _tab("moments", "moment_track", "learning.timestamps")

        kept, _, records = reconcile_plan_tabs([tab], ["food"], [])

        assert (kept[0]["dataSource"], records[0]["action"]) == ("food.steps", "repointed")


def _plan_json(tabs: list[dict], evidence: dict[str, bool]) -> str:
    return json.dumps(
        {
            "contentTags": ["learning", "tech"],
            "primaryTag": "learning",
            "confidence": 0.9,
            "evidence": evidence,
            "tabs": tabs,
        }
    )


async def _run_plan_with(tabs: list[dict], evidence: dict[str, bool] | None = None) -> PlanResult:
    answer = _plan_json(tabs, evidence or {"has_claims": True, "is_learnable": True})
    with (
        patch.object(plan_prompt, "_load_plan_prompt", return_value="<video>{title}</video>"),
        patch.object(plan_prompt, "_load_component_toolkit", return_value=""),
        patch.object(plan_mod, "call_llm_with_retry", AsyncMock(return_value=answer)),
    ):
        return await plan_mod.run_plan(
            title="T",
            channel="C",
            description="",
            duration=600,
            category_hint="learning",
            content_format=None,
            transcript="",
            llm_service=MagicMock(),
        )


class TestRunPlan:
    async def test_should_remove_the_reconciled_tab_from_the_plan(self) -> None:
        result = await _run_plan_with(_9vng_tabs())

        assert [t["id"] for t in result.tabs] == ["concepts", "moments", "quiz"]

    async def test_should_carry_the_reconcile_record_on_the_plan(self) -> None:
        result = await _run_plan_with(_9vng_tabs())

        assert [(r["tabId"], r["reason"]) for r in result.reconcile] == [
            ("real_world_cases", "inactive_domain")
        ]

    async def test_should_add_the_reconcile_drop_to_dropped_tabs(self) -> None:
        result = await _run_plan_with(_9vng_tabs())

        assert [d["reason"] for d in result.dropped_tabs] == ["reconcile_inactive_domain"]

    async def test_should_keep_a_tab_the_plans_own_evidence_rules_out(self) -> None:
        tabs = [_tab("concepts", "concept_canvas", "learning.concepts")]
        tabs.append(_tab("code", "code_playground", "tech.snippets"))

        result = await _run_plan_with(tabs, evidence={"has_code": False})

        assert [t["id"] for t in result.tabs] == ["concepts", "code"]


async def _saved_pipeline(ctx_updates: dict) -> dict:
    from src.services.pipeline.phases import assembly as phase

    ctx = _build_ctx()
    ctx.triage = SimpleNamespace(tabs=[])
    for key, value in ctx_updates.items():
        setattr(ctx, key, value)
    with (
        patch.object(phase, "assemble_response", return_value={"tabs": [], "meta": {}}),
        patch.object(
            phase,
            "settings",
            SimpleNamespace(REDIS_ENABLED=False, QDRANT_ENABLED=False, PIPELINE_VERSION="vtest"),
        ),
        patch.object(phase, "response_cache"),
    ):
        _ = [chunk async for chunk in phase.run_phase_assembly(ctx)]  # type: ignore[arg-type]
    return ctx.repository.save_structured_result.call_args.args[1]["pipeline"]


class TestPersistence:
    async def test_should_persist_the_plan_reconcile_record(self) -> None:
        record = {"tabId": "x", "component": "c", "from": "news.claims", "to": None, "reason": "r"}
        plan = PlanResult.model_validate({"reconcile": [record]})

        saved = await _saved_pipeline({"plan_result": plan})

        assert saved["reconcile"] == [record]

    async def test_should_persist_dropped_item_counts_under_extraction(self) -> None:
        saved = await _saved_pipeline(
            {
                "plan_result": PlanResult(),
                "extraction_data": {"tech": {"topics": ["a"]}},
                "extraction_dropped": {"tech.patterns": {"dropped": 7, "total": 7}},
                "extraction_mis_targeted": ["tech.patterns"],
            }
        )

        assert saved["extraction"]["_dropped"] == {"tech.patterns": {"dropped": 7, "total": 7}}

    async def test_should_persist_the_defaulted_count_under_extraction(self) -> None:
        saved = await _saved_pipeline(
            {
                "plan_result": PlanResult(),
                "extraction_data": {"tech": {"cheatSheet": []}},
                "extraction_defaulted": 22,
            }
        )

        assert saved["extraction"]["_defaulted"] == 22

    async def test_should_persist_mis_targeted_fields_under_extraction(self) -> None:
        saved = await _saved_pipeline(
            {
                "plan_result": PlanResult(),
                "extraction_data": {"tech": {"topics": ["a"]}},
                "extraction_dropped": {"tech.patterns": {"dropped": 7, "total": 7}},
                "extraction_mis_targeted": ["tech.patterns"],
            }
        )

        assert saved["extraction"]["_misTargeted"] == ["tech.patterns"]
