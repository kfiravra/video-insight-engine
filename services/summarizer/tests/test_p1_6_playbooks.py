"""p1.6 — spoken-word / review / news playbooks (L31) and R6 (info_grid never alone).

The playbooks of content-model §1–§8 — ``learning:talk``, ``learning:lecture``,
``podcast:podcast``, ``podcast:interview``, ``review:review``, ``news:explainer``
— steer the plan to each format's tab set (rendered into the plan prompt's
``<playbook>`` block, enforced through ``effective_requirements``). The probe
names a review ``commentary`` / ``comparison`` and a news explainer ``news`` /
``documentary`` / ``news_commentary``: ``playbookAliases`` reaches the playbook.
R6 (Kfir 2026-10-21): a standalone info_grid folds into the tab whose question it
answers — as a section of a spot_explorer host, else as a bottom attachment.
"""

from __future__ import annotations

import re

import pytest

from src.models.memory_types import MemoryResult
from src.models.pipeline_types import PlanResult
from src.services.pipeline.assembly import assemble_response
from src.services.pipeline.assembly.core import resolve_data_source
from src.services.pipeline.assembly.info_grid_fold import apply_folds
from src.services.pipeline.field_requests import requested_fields
from src.services.pipeline.plan_prompt import _render_playbook
from src.services.pipeline.reconcile import reconcile_at_plan_done
from src.services.pipeline.schema_fields import scalar_fields
from src.shared_config.domain_config import (
    NON_EXTRACTION_DATASOURCES,
    data_source,
    effective_requirements,
    get_config,
    get_playbook,
)

_SIX = (
    "learning:talk",
    "learning:lecture",
    "podcast:podcast",
    "podcast:interview",
    "review:review",
    "news:explainer",
)
_PATH = re.compile(r"\b[a-z]+\.[a-z][A-Za-z.]*[A-Za-z]")


# ─── Playbooks ───


class TestPlaybooks:
    @pytest.mark.parametrize("key", _SIX)
    def test_should_define_the_format_playbook(self, key: str) -> None:
        domain, fmt = key.split(":")

        assert get_playbook(domain, fmt)["planGuidance"]

    @pytest.mark.parametrize("key", _SIX)
    def test_should_name_only_registered_paths_in_the_guidance(self, key: str) -> None:
        guidance = get_config()["playbooks"][key]["planGuidance"]
        paths = set(_PATH.findall(guidance)) - {"e.g", "i.e"}

        assert all(data_source(p) or p in NON_EXTRACTION_DATASOURCES for p in paths), paths

    @pytest.mark.parametrize(
        ("domain", "fmt", "target"),
        [
            ("review", "commentary", "review:review"),
            ("review", "comparison", "review:review"),
            ("news", "documentary", "news:explainer"),
            ("news", "news", "news:explainer"),
            ("news", "news_commentary", "news:explainer"),
        ],
    )
    def test_should_reach_the_playbook_when_the_probe_names_its_format_otherwise(
        self, domain: str, fmt: str, target: str
    ) -> None:
        assert get_playbook(domain, fmt) == get_config()["playbooks"][target]

    def test_should_keep_a_pairs_own_playbook_over_an_alias(self) -> None:
        assert get_playbook("review", "unboxing") == get_config()["playbooks"]["review:unboxing"]

    def test_should_point_every_alias_at_a_playbook(self) -> None:
        playbooks = get_config()["playbooks"]

        assert set(get_config()["playbookAliases"].values()) <= set(playbooks)

    def test_should_forbid_a_filmstrip_and_flash_cards_on_a_talk(self) -> None:
        forbidden = effective_requirements("learning", "talk")["forbidden"]

        assert {"video_filmstrip", "flash_deck"} <= forbidden

    def test_should_keep_the_podcast_moment_track_required_in_both_podcast_formats(self) -> None:
        required = [
            effective_requirements("podcast", f)["required"] for f in ("podcast", "interview")
        ]

        assert required == [["moment_track"], ["moment_track"]]


class TestPlanPromptBlock:
    def test_should_route_a_talk_to_key_ideas_when_the_probe_says_talk(self) -> None:
        block = _render_playbook("learning", "talk")

        assert '<playbook for="learning:talk">' in block and "learning.keyIdeas" in block

    def test_should_route_an_interview_to_its_qa_when_the_probe_says_interview(self) -> None:
        block = _render_playbook("podcast", "interview")

        assert "podcast.qa" in block and "podcast.segments" in block

    def test_should_render_the_review_playbook_when_the_probe_says_commentary(self) -> None:
        assert '<playbook for="review:commentary">' in _render_playbook("review", "commentary")


# ─── R6: info_grid folds into the tab whose question it answers ───


def _planned(tab_id: str, component: str, source: str, label: str | None = None) -> dict:
    return {
        "id": tab_id,
        "label": label or tab_id,
        "component": component,
        "dataSource": source,
        "goal": "g",
        "brief": {"what": "w", "where": [], "expect": 6},
    }


def _reconciled(primary: str, tabs: list[dict]) -> list[dict]:
    plan = PlanResult.model_validate(
        {
            "contentTags": [primary],
            "primaryTag": primary,
            "confidence": 0.9,
            "evidence": {},
            "tabs": tabs,
        }
    )
    return reconcile_at_plan_done(plan, None, None).tabs


_RECIPE = [
    _planned("ingredients", "checklist", "food.ingredients"),
    _planned("steps", "step_player", "food.steps", "Method"),
    _planned("moments", "moment_track", "food.steps"),
    _planned("tips", "info_grid", "food.tips", "Chef Tips"),
]


class TestPlanFold:
    def test_should_fold_the_chef_tips_into_the_method_at_plan_done(self) -> None:
        tabs = _reconciled("food", [dict(t) for t in _RECIPE])
        method = next(t for t in tabs if t["id"] == "steps")

        assert "tips" not in [t["id"] for t in tabs]
        assert [(f["id"], f["dataSource"]) for f in method["folds"]] == [("tips", "food.tips")]

    def test_should_keep_the_grid_a_tab_when_no_planned_tab_can_host_it(self) -> None:
        tabs = [
            _planned("highlights", "moment_track", "gaming.highlights"),
            _planned("pulls", "tier_list", "gaming.rankings"),
            _planned("odds", "info_grid", "gaming.loadout"),
            _planned("more", "moment_track", "learning.timestamps"),
        ]

        assert "odds" in [t["id"] for t in _reconciled("gaming", tabs)]

    def test_should_keep_the_grid_a_tab_when_folding_would_leave_under_three_tabs(self) -> None:
        tabs = [
            _planned("ex", "workout_room", "fitness.exercises"),
            _planned("tips", "info_grid", "fitness.tips"),
        ]

        assert "tips" in [t["id"] for t in _reconciled("fitness", tabs)]

    def test_should_request_a_folded_field_for_its_host(self) -> None:
        tabs = _reconciled("food", [dict(t) for t in _RECIPE])

        fields = requested_fields(tabs, ["food"], [], scalar_fields, primary_tag="food")
        tips = next(f for f in fields if f.path == "food.tips")

        assert "steps" in tips.tab_ids and tips.reason == "planned"


class TestAssemblyFold:
    def _host(self, component: str, props: dict) -> dict:
        return {"id": "host", "label": "Key Ideas", "component": component, "props": props}

    def test_should_join_a_spot_host_as_a_section_without_key_only_rows(self) -> None:
        host = self._host("spot_explorer", {"spots": [{"name": "A", "description": "a"}]})
        extraction = {
            "learning": {
                "keyPoints": [
                    {"title": "150,000 lines", "detail": "in a month"},
                    {"title": "Key only", "detail": ""},
                ]
            }
        }
        fold = {"id": "numbers", "label": "Numbers", "dataSource": "learning.keyPoints"}

        apply_folds(host, [fold], extraction, None, resolve_data_source)

        assert [s["name"] for s in host["props"]["spots"]] == ["A", "150,000 lines"]
        assert host["props"]["sections"][-1] == {"label": "Numbers", "spotIndices": [1]}

    def test_should_attach_the_grid_under_a_non_spot_host(self) -> None:
        host = self._host("step_player", {"steps": [{"number": 1, "instruction": "Brown"}]})
        extraction = {"food": {"tips": [{"tip": "Brown the meat in batches.", "type": "chef_tip"}]}}

        apply_folds(
            host, [{"id": "tips", "dataSource": "food.tips"}], extraction, None, resolve_data_source
        )

        assert [a["component"] for a in host["attachments"]] == ["info_grid"]

    def test_should_ship_the_chef_tips_inside_the_method_when_the_recipe_assembles(self) -> None:
        steps = [
            {"number": i, "instruction": f"Step {i} in full.", "timestamp": 60 * i}
            for i in range(1, 7)
        ]
        food = {
            "ingredients": [{"name": n, "amount": 1, "unit": "cup"} for n in ("flour", "milk")],
            "steps": steps,
            "tips": [
                {"tip": "Brown the meat in batches so it sears.", "type": "chef_tip"},
                {"tip": "Salt the pasta water like the sea.", "type": "chef_tip"},
            ],
        }
        tabs = _reconciled("food", [dict(t) for t in _RECIPE])
        triage = {"primaryTag": "food", "contentTags": ["food"], "tabs": tabs}

        result = assemble_response(
            triage, {"food": food}, None, None, video_meta={"duration": 1020}
        )
        method = next(t for t in result["tabs"] if t["id"] == "steps")

        assert "info_grid" in [a["component"] for a in method.get("attachments", [])]


class TestFoldSafety:
    def test_should_never_fold_into_a_tab_deferred_to_frames_done(self) -> None:
        tabs = [
            _planned("workflow", "code_playground", "tech.snippets"),
            _planned("stack", "info_grid", "tech.topics"),
            _planned("concepts", "concept_canvas", "learning.concepts"),
            _planned("quiz", "quiz_arena", "enrichment.quiz"),
        ]
        plan = PlanResult.model_validate(
            {
                "contentTags": ["tech", "learning"],
                "primaryTag": "tech",
                "confidence": 0.9,
                "evidence": {"has_code": False},
                "tabs": tabs,
            }
        )
        memory = MemoryResult(evidence={"has_code": False})

        outcome = reconcile_at_plan_done(plan, memory, None)

        assert "stack" in [t["id"] for t in outcome.tabs]

    def test_should_give_the_folds_back_as_tabs_when_the_host_does_not_ship(self) -> None:
        tips = [
            {"tip": "Brown the meat in batches so it sears.", "type": "chef_tip"},
            {"tip": "Salt the pasta water like the sea.", "type": "chef_tip"},
        ]
        host = {
            **_planned("steps", "step_player", "food.steps", "Method"),
            "folds": [
                {
                    "id": "tips",
                    "label": "Chef Tips",
                    "dataSource": "food.tips",
                    "component": "info_grid",
                    "goal": "g",
                }
            ],
        }
        triage = {"primaryTag": "food", "contentTags": ["food"], "tabs": [host]}

        result = assemble_response(triage, {"food": {"tips": tips}}, None, None)

        assert "tips" in [t["id"] for t in result["tabs"]]
