"""Domain configuration — reads from the shared domains.json.

Resolves path via:
  1. Docker mount: /app/shared/domains.json
  2. Local dev: ../../packages/shared/src/config/domains.json (relative to repo root)
"""

from __future__ import annotations

import copy
import json
import logging
from collections.abc import Mapping
from functools import lru_cache
from pathlib import Path
from typing import Literal, TypedDict

logger = logging.getLogger(__name__)

# Possible locations for domains.json
_DOCKER_PATH = Path("/app/shared/domains.json")
_LOCAL_PATH = (
    Path(__file__).resolve().parent.parent.parent.parent.parent
    / "packages"
    / "shared"
    / "src"
    / "config"
    / "domains.json"
)


@lru_cache(maxsize=1)
def _load_config() -> dict:
    """Load and cache the domain configuration."""
    for path in (_DOCKER_PATH, _LOCAL_PATH):
        if path.exists():
            logger.debug("Loading domain config from %s", path)
            return json.loads(path.read_text())

    raise FileNotFoundError(f"domains.json not found at {_DOCKER_PATH} or {_LOCAL_PATH}")


def get_config() -> dict:
    """Get the full domain configuration dict."""
    return _load_config()


# ─────────────────────────────────────────────────────
# Derived sets (for validation)
# ─────────────────────────────────────────────────────


def valid_content_tags() -> frozenset[str]:
    """All valid content tag names."""
    return frozenset(get_config()["domains"].keys())


def valid_modifiers() -> frozenset[str]:
    """All valid modifier names."""
    return frozenset(get_config()["modifiers"].keys())


def valid_components() -> frozenset[str]:
    """All valid component names from domains.json top-level components array."""
    return frozenset(get_config()["components"])


def component_tiers() -> dict[str, str]:
    """Component name → tier ('primary' | 'secondary' | 'display')."""
    return dict(get_config().get("componentTiers", {}))


def component_tier(name: str) -> str:
    """Tier for a component, defaulting to 'primary' for unmapped names."""
    return component_tiers().get(name, "primary")


def secondary_components() -> frozenset[str]:
    """Attachment-only (secondary-tier) component names."""
    return frozenset(name for name, tier in component_tiers().items() if tier == "secondary")


def primary_components() -> frozenset[str]:
    """Planner-selectable (primary-tier) component names."""
    return frozenset(name for name, tier in component_tiers().items() if tier == "primary")


def ordered_components() -> list[str]:
    """Planner-selectable components in their canonical (config) order."""
    return list(get_config().get("components", []))


def render_valid_component_names() -> str:
    """Render the backtick-comma component list injected into plan.txt's
    ``{valid_components}`` placeholder. Single-sourced from domains.json so the
    planner's selectable surface follows config — no hardcoded prompt list."""
    return ", ".join(f"`{name}`" for name in ordered_components())


def density_gates() -> dict[str, dict[str, str]]:
    """Per-component density-gate guidance shown to the planner.

    NOTE: this is advisory LLM-steering text only. The assembler's hard caps
    live in ``assemblerItemCaps`` (see :func:`assembler_item_caps`) — both are
    in domains.json but intentionally independent: editing this changes what
    the LLM aims for, not the enforced caps.
    """
    return dict(get_config().get("densityGates", {}))


def assembler_item_caps() -> dict[str, int]:
    """HARD per-component item caps enforced by ``_cap_tab_items``
    (assembly/core.py). Independent of the advisory ``densityGates`` — see
    the ``assemblerItemCapsNote`` in domains.json."""
    return dict(get_config().get("assemblerItemCaps", {}))


def domain_requirements() -> dict[str, dict]:
    """Per-domain assembled-output validation rules consumed by
    ``_validate_domain_requirements`` (assembly/core.py): ``required``
    components are backfilled when the planner drops them; ``max`` caps
    per-component tab counts. ``render_domain_requirements`` shows the same
    ``required`` lists to the planner. See ``domainRequirementsNote`` in domains.json."""
    return dict(get_config().get("domainRequirements", {}))


def get_playbook(domain: str, content_format: str | None) -> dict:
    """Layout playbook for a ``<domain>:<format>`` pair, or {} when none exists.

    Playbooks refine domain requirements for a video subtype (e.g.
    ``gaming:unboxing``) — see ``playbooksNote`` in domains.json.
    """
    if not content_format:
        return {}
    playbooks = get_config().get("playbooks", {})
    return dict(playbooks.get(f"{domain}:{content_format}", {}))


def _ruled_out_by_evidence(
    domain: str, component: str, evidence: Mapping[str, bool] | None
) -> bool:
    """True when ``evidence`` answers ``False`` for the key gating ``component``."""
    if not evidence:
        return False
    key = requirement_evidence().get(domain, {}).get(component)
    return key is not None and evidence.get(key) is False


def effective_requirements(
    domain: str,
    content_format: str | None = None,
    evidence: Mapping[str, bool] | None = None,
) -> dict:
    """Merged layout policy for a video: domain requirements + playbook.

    The single merge point for ALL enforcement (plan post-validation, assembly
    backstop, enrichment gating). Semantics: ``forbidden`` is the UNION of
    domain and playbook lists; ``required`` is the playbook's when present,
    else the domain's; ``max`` always comes from the domain.

    ``evidence`` (the plan's Appendix-C booleans) makes ``required``
    conditional: a component whose ``requirementEvidence`` key is ``False``
    there is not required — a food vlog with no recipe needs no checklist. A
    missing key is "no opinion" and keeps the requirement.
    """
    base = domain_requirements().get(domain, {})
    playbook = get_playbook(domain, content_format)
    forbidden = frozenset(base.get("forbidden", [])) | frozenset(playbook.get("forbidden", []))
    required = playbook["required"] if "required" in playbook else base.get("required", [])
    return {
        "required": [c for c in required if not _ruled_out_by_evidence(domain, c, evidence)],
        "max": dict(base.get("max", {})),
        "forbidden": forbidden,
        "preferred": list(playbook.get("preferred", [])),
        "planGuidance": playbook.get("planGuidance", ""),
    }


def visual_criticality_config() -> dict:
    """Adaptive frame-pipeline effort config — see ``visualCriticalityNote``."""
    return dict(get_config().get("visualCriticality", {}))


def render_density_gate_table() -> str:
    """Render the markdown density table injected into component_toolkit.txt's
    ``{density_gates}`` placeholder, single-sourced from domains.json."""
    header = "| Component | min items | max items | max chars/cell |\n|---|---|---|---|"
    rows = [
        f"| {name} | {gate.get('min', '')} | {gate.get('max', '')} | {gate.get('chars', '')} |"
        for name, gate in density_gates().items()
    ]
    return "\n".join([header, *rows])


# ─────────────────────────────────────────────────────
# Category mapping
# ─────────────────────────────────────────────────────


def map_category_to_tag(category: str) -> str:
    """Map a raw category name to a content tag."""
    category_map = get_config()["categoryMap"]
    return category_map.get(category.lower(), "learning")


# ─────────────────────────────────────────────────────
# Tab helpers
# ─────────────────────────────────────────────────────


def get_default_tab_ids(tag: str) -> list[str]:
    """Get the ordered default tab IDs for a domain."""
    domains = get_config()["domains"]
    domain = domains.get(tag, domains.get("learning", {}))
    return [t["id"] for t in domain.get("defaultTabs", []) if isinstance(t, dict) and "id" in t]


def get_tab_meta(tab_id: str) -> tuple[str, str] | None:
    """Get (label, emoji) for a tab ID. Searches defaultTabs across all domains."""
    cfg = get_config()
    for domain in cfg["domains"].values():
        for tab in domain.get("defaultTabs", []):
            if isinstance(tab, dict) and tab.get("id") == tab_id:
                return (tab.get("label", tab_id), tab.get("emoji", ""))
    return None


def get_enrichment_map() -> dict[str, str]:
    """Map of content tag → enrichment prompt filename.

    Only tags listed here trigger the enrichment stage.
    """
    return dict(get_config().get("enrichment", {}))


def build_fallback_tabs(tag: str) -> list[dict]:
    """Build default TabDefinition dicts for a domain.

    Returns complete objects directly from domains.json defaultTabs.
    """
    cfg = get_config()
    domain = cfg["domains"].get(tag, cfg["domains"].get("learning", {}))
    return list(domain.get("defaultTabs", []))


def datasources_for_component(tag: str, component: str) -> list[str]:
    """Default-tab dataSources in `tag` that back `component`, in priority order.

    The defaultTabs order in domains.json IS the priority (e.g. tech lists `code`
    → ``tech.snippets`` before `patterns` → ``tech.patterns``, both backing
    ``code_playground``). Used by assembly to recover an empty planned field by
    swapping in a populated sibling field that renders with the same component.
    """
    cfg = get_config()
    domain = cfg["domains"].get(tag, {})
    sources: list[str] = []
    for tab in domain.get("defaultTabs", []):
        if not isinstance(tab, dict):
            continue
        if tab.get("component") == component and tab.get("dataSource"):
            ds = tab["dataSource"]
            if ds not in sources:
                sources.append(ds)
    return sources


def sibling_datasources(tag: str, data_source: str) -> list[str]:
    """Other dataSources in `tag` sharing `data_source`'s component, priority-ordered.

    Resolves `data_source` to its registered defaultTab component, then returns the
    other dataSources backing that same component (excluding `data_source` itself).
    Returns [] when `data_source` is not a registered defaultTab for `tag`.
    """
    cfg = get_config()
    domain = cfg["domains"].get(tag, {})
    component = next(
        (
            tab.get("component")
            for tab in domain.get("defaultTabs", [])
            if isinstance(tab, dict) and tab.get("dataSource") == data_source
        ),
        None,
    )
    if not component:
        return []
    return [ds for ds in datasources_for_component(tag, component) if ds != data_source]


# ─────────────────────────────────────────────────────
# Extraction-field registry (dataSources + reconcile/grouping policy)
# ─────────────────────────────────────────────────────

# Evidence vocabulary (pipeline-1min brief, Appendix C): the plan and the video
# memory each emit every key as a boolean; registry `requiresEvidence` values
# and `requirementEvidence` must name one of these.
EVIDENCE_KEYS: tuple[str, ...] = (
    "has_steps",
    "has_ingredients",
    "has_materials",
    "has_code",
    "has_drills",
    "has_comparison",
    "has_ranking",
    "has_lineup",
    "has_claims",
    "has_lyrics",
    "has_itinerary",
    "has_packing",
    "is_learnable",
)

# Tab dataSources that are not extraction fields: ``frames`` feeds
# video_filmstrip straight from the frames phase, so it has no registry entry.
NON_EXTRACTION_DATASOURCES: frozenset[str] = frozenset({"frames"})


class DataSourceSpec(TypedDict):
    """One ``dataSources`` entry — see ``dataSourcesNote`` in domains.json."""

    domain: str
    field: str
    kind: Literal["list", "object"]
    components: list[str]
    siblings: list[str]
    requiresEvidence: str | None
    waitsForVisual: bool
    cap: int
    outputWeight: int


class QuizPolicy(TypedDict):
    position: Literal["last"]
    requiresEvidence: str
    attachmentHostsExclude: list[str]


class QuizEnrichment(TypedDict):
    quizDomains: list[str]
    flavor: dict[str, str]


class GroupingConfig(TypedDict):
    maxTextGroups: int
    groupOutputBudget: int


def _registry() -> dict[str, DataSourceSpec]:
    """The cached registry map itself — read-only internal access, never returned."""
    return get_config().get("dataSources", {})


def data_sources() -> dict[str, DataSourceSpec]:
    """dataSource path → registry entry, in prompt (config) order.

    Deep-copied: the loaded config is cached process-wide, so a caller mutating
    an entry (e.g. a spec's ``components`` list) must not leak into later reads.
    """
    return copy.deepcopy(_registry())


def data_source(path: str) -> DataSourceSpec | None:
    """Registry entry for one dataSource path, or None when it is not registered."""
    spec = _registry().get(path)
    return copy.deepcopy(spec) if spec is not None else None


def demote_to() -> dict[str, list[str]]:
    """Plan-time evidence demotion ladder (component → simpler components)."""
    return {k: list(v) for k, v in get_config().get("demoteTo", {}).items()}


def requirement_evidence() -> dict[str, dict[str, str]]:
    """Domain → required component → evidence key gating its backfill."""
    return {k: dict(v) for k, v in get_config().get("requirementEvidence", {}).items()}


def render_requirement(domain: str, component: str) -> str:
    """One required component as the planner reads it, gated on its evidence key.

    The plan emits its own ``evidence`` in the same call, so the condition is
    stated in the prompt instead of being resolved before the call.
    """
    key = requirement_evidence().get(domain, {}).get(component)
    if key is None:
        return f"`{component}` always"
    return f"`{component}` only when {key} is true"


def render_domain_requirements() -> str:
    """Render plan.txt's ``{domain_requirements}`` list from domainRequirements.

    One line per domain with a non-empty ``required`` list, each component
    conditional on its ``requirementEvidence`` key — the same rule assembly's
    backfill applies, so the prompt never demands what the content lacks.
    """
    lines: list[str] = []
    for domain, rules in domain_requirements().items():
        required = rules.get("required", [])
        if required:
            parts = " · ".join(render_requirement(domain, c) for c in required)
            lines.append(f"- {domain}: {parts}")
    return "\n".join(lines)


def render_extraction_caps() -> str:
    """Render plan.txt's ``{extraction_caps}`` block: one cap per registry dataSource.

    One line per domain in registry order (``- food: ingredients ≤ 30, …``);
    an object path is one item by definition, so it carries no number.
    """
    by_domain: dict[str, list[str]] = {}
    for spec in _registry().values():
        cap = "(one object)" if spec["kind"] == "object" else f"≤ {spec['cap']}"
        by_domain.setdefault(spec["domain"], []).append(f"{spec['field']} {cap}")
    return "\n".join(f"- {domain}: {', '.join(fields)}" for domain, fields in by_domain.items())


def quiz_policy() -> QuizPolicy:
    """Quiz placement/attachment policy (position, evidence, excluded hosts)."""
    return copy.deepcopy(get_config()["quizPolicy"])


def quiz_enrichment() -> QuizEnrichment:
    """Quiz-only enrichment config: allowed domains + per-domain flavor line."""
    return copy.deepcopy(get_config()["quizEnrichment"])


def grouping_config() -> GroupingConfig:
    """Phase-3 extraction grouping limits (text-group cap, per-group output budget)."""
    return copy.deepcopy(get_config()["grouping"])


def registered_data_source(data_source: str, component: str) -> str | None:
    """The registered dataSource a planned tab should read.

    ``data_source`` itself when the registry (or ``NON_EXTRACTION_DATASOURCES``)
    knows it; otherwise the first registered path of the same domain whose
    components include ``component``; ``None`` when neither exists.
    """
    registry = _registry()
    if data_source in registry or data_source in NON_EXTRACTION_DATASOURCES:
        return data_source
    domain = data_source.split(".", 1)[0]
    return next(
        (
            path
            for path, spec in registry.items()
            if spec["domain"] == domain and component in spec["components"]
        ),
        None,
    )


def render_valid_datasources() -> str:
    """Render component_toolkit.txt's ``{valid_datasources}`` block from the registry.

    One ``domain: path, path`` line per domain in first-appearance order — the
    exact format of the list that used to be hardcoded in the toolkit, so the
    planner sees the same text while the registry stays the single source.
    """
    by_domain: dict[str, list[str]] = {}
    for path, spec in _registry().items():
        by_domain.setdefault(spec["domain"], []).append(path)
    return "\n".join(f"{domain}: {', '.join(paths)}" for domain, paths in by_domain.items())
