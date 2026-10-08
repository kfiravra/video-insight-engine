"""Registry integrity tests for the domains.json extraction-field maps.

Covers ``dataSources``, ``demoteTo``, ``requirementEvidence``, ``quizPolicy``,
``quizEnrichment`` and ``grouping`` (pipeline-1min task 0.4): every registered
path must resolve to a real schema field (Pydantic model AND prompt schema
skeleton), every defaultTab must point at a registered path (the check that
would have caught ``tech.setup`` vs ``tech.setup.commands``), every component
named anywhere must exist, and the toolkit's ``{valid_datasources}`` block must
render exactly the registry.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from types import NoneType, UnionType
from typing import Union, get_args, get_origin

import pytest
from pydantic import BaseModel

from src.models.domain_types import DOMAIN_MODELS, MODIFIER_MODELS
from src.models.pipeline_types import EnrichmentData
from src.services.pipeline.assembly.core import _MOMENT_CAP_MAX
from src.shared_config.domain_config import (
    EVIDENCE_KEYS,
    NON_EXTRACTION_DATASOURCES,
    assembler_item_caps,
    data_source,
    data_sources,
    demote_to,
    domain_requirements,
    get_config,
    grouping_config,
    quiz_enrichment,
    quiz_policy,
    render_valid_datasources,
    requirement_evidence,
    sibling_datasources,
    valid_components,
)

PROMPTS_DIR = Path(__file__).parent.parent / "src" / "prompts"
_TOOLKIT_PATH = PROMPTS_DIR / "component_toolkit.txt"
_ROOT_MODELS: dict[str, type[BaseModel]] = {
    **DOMAIN_MODELS,
    **MODIFIER_MODELS,
    "enrichment": EnrichmentData,
}
_ALL_PATHS = list(data_sources())
_SCHEMA_PATHS = [p for p in _ALL_PATHS if data_sources()[p]["domain"] != "enrichment"]


# ─── helpers ────────────────────────────────────────────────────────────


def _unwrap_optional(annotation: object) -> object:
    """``X | None`` → ``X``; anything else unchanged."""
    if get_origin(annotation) in (Union, UnionType):
        args = [a for a in get_args(annotation) if a is not NoneType]
        if len(args) == 1:
            return args[0]
    return annotation


def _model_field(model: type[BaseModel], key: str) -> object | None:
    """Annotation of the field named ``key`` by attribute name or alias."""
    for name, info in model.model_fields.items():
        if key in (name, info.alias):
            return _unwrap_optional(info.annotation)
    return None


def _resolve_model_annotation(path: str) -> object | None:
    """Walk a dataSource path through the Pydantic models; None when it breaks."""
    domain, *keys = path.split(".")
    current: object = _ROOT_MODELS.get(domain)
    for key in keys:
        if not (isinstance(current, type) and issubclass(current, BaseModel)):
            return None
        current = _model_field(current, key)
    return current


def _schema_skeleton(domain: str) -> dict:
    """The JSON skeleton (first ``{...}`` block, ``> UI:`` lines removed) of a schema."""
    text = (PROMPTS_DIR / "schemas" / f"{domain}.txt").read_text()
    body = "\n".join(ln for ln in text.splitlines() if not ln.lstrip().startswith(">"))
    start = body.index("\n{") + 1
    decoder = json.JSONDecoder()
    skeleton, _ = decoder.raw_decode(body[start:])
    return skeleton


def _resolve_skeleton_value(path: str) -> object:
    """Walk a dataSource path through its schema skeleton; KeyError when it breaks."""
    domain, *keys = path.split(".")
    current: object = _schema_skeleton(domain)
    for key in keys:
        assert isinstance(current, dict)
        current = current[key]
    return current


def _rendered_toolkit_block() -> str:
    """The ``<valid_datasources>`` block of the toolkit after rendering."""
    rendered = _TOOLKIT_PATH.read_text().replace("{valid_datasources}", render_valid_datasources())
    return rendered.split("<valid_datasources>\n")[1].split("\n\nUse only")[0]


def _all_default_tabs() -> list[dict]:
    return [tab for d in get_config()["domains"].values() for tab in d["defaultTabs"]]


# ─── dataSources: shape ─────────────────────────────────────────────────


class TestDataSourceEntries:
    @pytest.mark.parametrize("path", _ALL_PATHS)
    def test_should_name_its_own_domain_and_field_when_registered(self, path):
        spec = data_sources()[path]

        assert f"{spec['domain']}.{spec['field']}" == path

    @pytest.mark.parametrize("path", _ALL_PATHS)
    def test_should_have_positive_int_cap_and_weight_when_registered(self, path):
        spec = data_sources()[path]
        values = (spec["cap"], spec["outputWeight"])

        assert all(type(v) is int and v > 0 for v in values)

    @pytest.mark.parametrize("path", _ALL_PATHS)
    def test_should_list_only_known_components_when_registered(self, path):
        components = data_sources()[path]["components"]

        assert components and set(components) <= valid_components()

    @pytest.mark.parametrize("path", _ALL_PATHS)
    def test_should_name_vocabulary_evidence_when_gated(self, path):
        evidence = data_sources()[path]["requiresEvidence"]

        assert evidence is None or evidence in EVIDENCE_KEYS

    @pytest.mark.parametrize("path", _ALL_PATHS)
    def test_should_use_known_kind_and_bool_visual_flag_when_registered(self, path):
        spec = data_sources()[path]

        assert spec["kind"] in ("list", "object") and type(spec["waitsForVisual"]) is bool

    @pytest.mark.parametrize("path", _ALL_PATHS)
    def test_should_point_siblings_at_other_paths_sharing_a_component(self, path):
        spec = data_sources()[path]
        broken = [
            sib
            for sib in spec["siblings"]
            if sib == path
            or sib not in data_sources()
            or not set(data_sources()[sib]["components"]) & set(spec["components"])
        ]

        assert broken == []

    def test_should_return_none_when_path_is_unregistered(self):
        assert data_source("tech.setup") is None

    @pytest.mark.parametrize("path", _ALL_PATHS)
    def test_should_not_ask_for_more_items_than_the_primary_renderer_keeps(self, path):
        spec = data_sources()[path]
        # moment_track's hard cap scales with duration up to _MOMENT_CAP_MAX.
        limits = {**assembler_item_caps(), "moment_track": _MOMENT_CAP_MAX}
        limit = limits.get(spec["components"][0])

        assert limit is None or spec["cap"] <= limit

    def test_should_wait_for_visual_only_on_screen_read_paths(self):
        visual = {p for p, spec in data_sources().items() if spec["waitsForVisual"]}

        assert visual == {
            "tech.snippets",
            "tech.patterns",
            "tech.cheatSheet",
            "tech.setup.commands",
            "gaming.highlights",
        }

    def test_should_not_wait_for_visual_when_moments_are_transcript_driven(self):
        assert data_source("narrative.keyMoments")["waitsForVisual"] is False

    @pytest.mark.parametrize("tab", _all_default_tabs(), ids=lambda t: t["dataSource"])
    def test_should_list_every_default_tab_sibling_in_the_registry(self, tab):
        tag = next(d for d, cfg in get_config()["domains"].items() if tab in cfg["defaultTabs"])
        spec = data_source(tab["dataSource"])
        registered = set(spec["siblings"]) if spec else set()

        assert set(sibling_datasources(tag, tab["dataSource"])) <= registered


class TestAccessorsReturnCopies:
    def test_should_not_leak_mutation_when_a_registry_entry_is_edited(self):
        data_sources()["food.steps"]["components"].append("mutated")

        assert "mutated" not in data_sources()["food.steps"]["components"]

    def test_should_not_leak_mutation_when_a_single_entry_is_edited(self):
        data_source("food.steps")["siblings"].append("mutated")

        assert "mutated" not in data_source("food.steps")["siblings"]

    def test_should_not_leak_mutation_when_quiz_policy_is_edited(self):
        quiz_policy()["attachmentHostsExclude"].append("mutated")

        assert "mutated" not in quiz_policy()["attachmentHostsExclude"]

    def test_should_not_leak_mutation_when_quiz_enrichment_is_edited(self):
        quiz_enrichment()["quizDomains"].append("mutated")

        assert "mutated" not in quiz_enrichment()["quizDomains"]

    def test_should_not_leak_mutation_when_grouping_is_edited(self):
        grouping_config()["maxTextGroups"] = -1

        assert grouping_config()["maxTextGroups"] != -1


# ─── dataSources: schema resolution ─────────────────────────────────────


class TestDataSourcesResolveToSchemaFields:
    @pytest.mark.parametrize("path", _ALL_PATHS)
    def test_should_resolve_to_a_pydantic_field_when_registered(self, path):
        annotation = _resolve_model_annotation(path)

        assert annotation is not None, f"{path} is not a field of its domain model"

    @pytest.mark.parametrize("path", _ALL_PATHS)
    def test_should_match_the_model_field_kind_when_registered(self, path):
        annotation = _resolve_model_annotation(path)
        is_list = get_origin(annotation) is list

        assert is_list == (data_sources()[path]["kind"] == "list")

    @pytest.mark.parametrize("path", _SCHEMA_PATHS)
    def test_should_resolve_to_a_schema_skeleton_key_when_registered(self, path):
        value = _resolve_skeleton_value(path)

        expected = list if data_sources()[path]["kind"] == "list" else dict
        assert isinstance(value, expected)


# ─── defaultTabs ────────────────────────────────────────────────────────


class TestDefaultTabsUseRegisteredPaths:
    @pytest.mark.parametrize("tab", _all_default_tabs(), ids=lambda t: t["dataSource"])
    def test_should_point_at_a_registered_path_when_used_as_fallback(self, tab):
        known = set(data_sources()) | NON_EXTRACTION_DATASOURCES

        assert tab["dataSource"] in known

    @pytest.mark.parametrize("tab", _all_default_tabs(), ids=lambda t: t["dataSource"])
    def test_should_render_with_a_component_its_path_supports(self, tab):
        spec = data_source(tab["dataSource"])

        assert spec is None or tab["component"] in spec["components"]

    def test_should_use_setup_commands_when_tech_falls_back(self):
        tabs = {t["id"]: t for t in get_config()["domains"]["tech"]["defaultTabs"]}

        assert tabs["setup"]["dataSource"] == "tech.setup.commands"

    def test_should_use_flash_deck_when_language_vocabulary_falls_back(self):
        tabs = {t["id"]: t for t in get_config()["domains"]["language"]["defaultTabs"]}

        assert tabs["vocabulary"]["component"] == "flash_deck"


# ─── components named by policy maps ────────────────────────────────────


class TestPolicyComponentsExist:
    def test_should_only_require_known_components_per_domain(self):
        named = {
            c
            for rules in domain_requirements().values()
            for key in ("required", "forbidden")
            for c in rules.get(key, [])
        } | {c for rules in domain_requirements().values() for c in rules.get("max", {})}

        assert named - valid_components() == set()

    def test_should_only_name_known_components_in_playbooks(self):
        named = {
            c
            for pb in get_config().get("playbooks", {}).values()
            for key in ("required", "preferred", "forbidden")
            for c in pb.get(key, [])
        }

        assert named - valid_components() == set()

    def test_should_only_name_known_components_in_demote_to(self):
        named = set(demote_to()) | {c for targets in demote_to().values() for c in targets}

        assert named - valid_components() == set()

    def test_should_never_demote_a_component_to_itself(self):
        cycles = [c for c, targets in demote_to().items() if c in targets]

        assert cycles == []

    def test_should_have_a_demotion_rung_for_every_evidence_gated_component(self):
        gated = {
            c
            for spec in data_sources().values()
            if spec["requiresEvidence"]
            for c in spec["components"]
        }

        assert gated - set(demote_to()) == set()


# ─── requirementEvidence / quiz / grouping ──────────────────────────────


class TestRequirementEvidence:
    def test_should_use_vocabulary_keys_when_gating_requirements(self):
        keys = {k for comps in requirement_evidence().values() for k in comps.values()}

        assert keys - set(EVIDENCE_KEYS) == set()

    def test_should_only_gate_components_the_domain_requires(self):
        stale = [
            (domain, comp)
            for domain, comps in requirement_evidence().items()
            for comp in comps
            if comp not in domain_requirements().get(domain, {}).get("required", [])
        ]

        assert stale == []


class TestQuizPolicy:
    def test_should_only_exclude_known_attachment_hosts(self):
        hosts = set(quiz_policy()["attachmentHostsExclude"])

        assert hosts - valid_components() == set()

    def test_should_gate_quiz_on_vocabulary_evidence(self):
        assert quiz_policy()["requiresEvidence"] in EVIDENCE_KEYS

    def test_should_allow_quiz_exactly_where_quiz_arena_is_not_forbidden(self):
        allowed = {
            d
            for d, rules in domain_requirements().items()
            if "quiz_arena" not in rules.get("forbidden", [])
        }

        assert set(quiz_enrichment()["quizDomains"]) == allowed

    def test_should_have_a_flavor_line_for_every_quiz_domain(self):
        enrichment = quiz_enrichment()

        assert set(enrichment["flavor"]) == set(enrichment["quizDomains"])


class TestGrouping:
    def test_should_use_positive_int_limits(self):
        values = list(grouping_config().values())

        assert values and all(type(v) is int and v > 0 for v in values)


# ─── toolkit render ─────────────────────────────────────────────────────


class TestValidDatasourcesRender:
    def test_should_declare_the_placeholder_in_the_toolkit(self):
        assert "{valid_datasources}" in _TOOLKIT_PATH.read_text()

    def test_should_render_every_registered_path_in_registry_order(self):
        rendered = [
            p.strip()
            for line in _rendered_toolkit_block().splitlines()
            for p in line.split(":", 1)[1].split(",")
        ]

        assert rendered == _ALL_PATHS

    def test_should_render_one_domain_prefixed_line_per_domain(self):
        lines = _rendered_toolkit_block().splitlines()
        domains = [ln.split(":", 1)[0] for ln in lines]

        assert len(domains) == len(set(domains)) and all(
            re.fullmatch(rf"{d}: {re.escape(d)}\.\S+(, {re.escape(d)}\.\S+)*", ln)
            for d, ln in zip(domains, lines, strict=True)
        )

    def test_should_render_the_pre_registry_list_plus_fitness_tips(self):
        # The hardcoded toolkit block this placeholder replaced (8d52e23^),
        # with fitness.tips — the one path the registry added — appended and
        # enrichment.flashcards/scenarios gone with quiz-only enrichment (1d.1).
        expected = "\n".join(
            [
                "tech: tech.snippets, tech.patterns, tech.cheatSheet, tech.setup.commands, tech.topics",
                "learning: learning.keyPoints, learning.concepts, learning.takeaways, learning.timestamps",
                "project: project.steps, project.materials, project.tools, project.safetyWarnings",
                "food: food.ingredients, food.steps, food.tips, food.equipment, food.substitutions",
                "travel: travel.itinerary, travel.budget, travel.packingList",
                "review: review.pros, review.cons, review.specs, review.comparisons, review.verdict",
                "fitness: fitness.exercises, fitness.warmup, fitness.cooldown, fitness.timer, fitness.tips",
                "music: music.analysis, music.structure, music.lyrics, music.credits",
                "language: language.phrases, language.rules, language.drills, language.vocabulary",
                "science: science.concepts, science.keyFacts, science.experiments",
                "podcast: podcast.segments, podcast.guests, podcast.quotes, podcast.topics",
                "news: news.storyTimeline, news.entities, news.claims, news.context",
                "gaming: gaming.highlights, gaming.loadout, gaming.walkthrough, gaming.rankings",
                "sport: sport.matchEvents, sport.formation, sport.statComparison",
                "narrative: narrative.keyMoments, narrative.quotes, narrative.takeaways",
                "enrichment: enrichment.quiz",
            ]
        )

        assert render_valid_datasources() == expected
